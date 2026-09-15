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

# Sign with a stable identity so macOS can grant/remember Automation permission
# for controlling UTM (utmctl uses AppleEvents). Prefer a Developer ID; fall back
# to ad-hoc. Override the identity with VZKL_SIGN_ID.
SIGN_ID="${VZKL_SIGN_ID:-}"
if [ -z "$SIGN_ID" ]; then
    SIGN_ID=$(security find-identity -v -p codesigning 2>/dev/null \
        | grep "Developer ID Application" | head -1 \
        | sed -E 's/^[^"]*"([^"]*)".*$/\1/')
fi
ENT="VZKextLoader.entitlements"
if [ -n "$SIGN_ID" ]; then
    echo ">> codesign: $SIGN_ID (hardened runtime + automation entitlement)"
    codesign --force --options runtime --entitlements "$ENT" --timestamp \
             --sign "$SIGN_ID" "$APP" \
        || { echo "   (hardened signing failed; trying without timestamp)"; \
             codesign --force --options runtime --entitlements "$ENT" \
                      --sign "$SIGN_ID" "$APP" \
        || { echo "   (Developer ID signing failed; falling back to ad-hoc)"; \
             codesign --force --entitlements "$ENT" --sign - "$APP" >/dev/null 2>&1 || true; }; }
else
    echo ">> codesign: ad-hoc (no Developer ID found; Automation grant may re-prompt each build)"
    codesign --force --entitlements "$ENT" --sign - "$APP" >/dev/null 2>&1 || \
        echo "   (codesign skipped; unsigned bundle still runs locally)"
fi

echo ">> done: $APP"
echo "   run:  open $APP        (or: $BIN  to see console logs)"
