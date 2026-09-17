#!/bin/bash
# Build the SwiftUI executable and assemble a double-clickable .app bundle.
#
#   ./build-app.sh            # release build -> ./VZKextLoader.app
#   open VZKextLoader.app
#
# The app shells out to the Python engine in ../engine. By default it looks for
# it at ~/src/vz-kext-loader/engine; override with the VZKL_ENGINE_DIR env var.
set -euo pipefail
cd "$(dirname "$0")"

CONFIG="${1:-release}"
APP="VZKextLoader.app"

echo ">> swift build -c $CONFIG"
swift build -c "$CONFIG"

BIN=".build/$CONFIG/VZKextLoader"
[ -x "$BIN" ] || { echo "build produced no binary at $BIN" >&2; exit 1; }

echo ">> assembling $APP"
rm -rf "$APP"
mkdir -p "$APP/Contents/MacOS" "$APP/Contents/Resources"
cp "$BIN" "$APP/Contents/MacOS/VZKextLoader"
cp Info.plist "$APP/Contents/Info.plist"
printf 'APPL????' > "$APP/Contents/PkgInfo"

# App icon (generate once if missing).
[ -f AppIcon.icns ] || ./tools/makeicon.sh
cp AppIcon.icns "$APP/Contents/Resources/AppIcon.icns"

# Embed the self-contained runtime (python + r2 + vzkl). Build it if absent.
RT_SRC="${VZKL_RUNTIME:-runtime}"
[ -d "$RT_SRC" ] || { echo ">> runtime missing; building it"; ./build-runtime.sh "$RT_SRC"; }
echo ">> embedding runtime -> $APP/Contents/Resources/runtime"
cp -R "$RT_SRC" "$APP/Contents/Resources/runtime"

# Sign with a stable identity so macOS can grant/remember Automation permission
# for controlling UTM (utmctl uses AppleEvents). Prefer a Developer ID; fall back
# to ad-hoc. Identity comes from VZKL_SIGN_ID, else PKG_CODESIGN_KEY (the same
# variable the zfs `.keys` sets — `. ./.keys` before building), else autodetect.
SIGN_ID="${VZKL_SIGN_ID:-${PKG_CODESIGN_KEY:-}}"
if [ -z "$SIGN_ID" ]; then
    SIGN_ID=$(security find-identity -v -p codesigning 2>/dev/null \
        | grep "Developer ID Application" | head -1 \
        | sed -E 's/^[^"]*"([^"]*)".*$/\1/')
fi
ENT="VZKextLoader.entitlements"
RT_ENT="runtime.entitlements"
RT="$APP/Contents/Resources/runtime"

# codesign one Mach-O file; $2 = optional entitlements. Hardened runtime +
# timestamp under a Developer ID, ad-hoc otherwise.
sign_one() {
    local f="$1" ent="${2:-}"
    if [ -n "$SIGN_ID" ]; then
        codesign --force --options runtime --timestamp \
                 ${ent:+--entitlements "$ent"} --sign "$SIGN_ID" "$f" 2>/dev/null
    else
        codesign --force ${ent:+--entitlements "$ent"} --sign - "$f" 2>/dev/null
    fi
}

# Inside-out: every nested dylib/.so first, then the interpreters (with library
# validation relaxed), then the app last. Deep signing is intentionally avoided.
if [ -d "$RT" ]; then
    echo ">> signing runtime inside-out (${SIGN_ID:-ad-hoc})"
    find "$RT" -type f \( -name '*.dylib' -o -name '*.so' \) -print0 \
        | while IFS= read -r -d '' f; do sign_one "$f"; done
    # interpreters + any Mach-O executables in the tool bin dirs
    while IFS= read -r f; do
        file "$f" 2>/dev/null | grep -q 'Mach-O.*executable' && sign_one "$f" "$RT_ENT"
    done < <(find "$RT/python/bin" "$RT/r2/bin" -type f 2>/dev/null)
fi

if [ -n "$SIGN_ID" ]; then
    echo ">> codesign app: $SIGN_ID (hardened runtime + automation entitlement)"
    codesign --force --options runtime --entitlements "$ENT" --timestamp \
             --sign "$SIGN_ID" "$APP" \
        || { echo "   (with-timestamp failed; retrying without)"; \
             codesign --force --options runtime --entitlements "$ENT" \
                      --sign "$SIGN_ID" "$APP"; }
else
    echo ">> codesign app: ad-hoc (no Developer ID; Automation grant may re-prompt)"
    codesign --force --entitlements "$ENT" --sign - "$APP" >/dev/null 2>&1 || \
        echo "   (codesign skipped; unsigned bundle still runs locally)"
fi

echo ">> verifying signature"
codesign --verify --deep --strict --verbose=2 "$APP" 2>&1 | tail -3 || true

echo ">> done: $APP"
echo "   run:  open $APP        (or: $BIN  to see console logs)"
echo "   next: notarize with notarytool, then staple (see build-runtime.sh header / release notes)"
