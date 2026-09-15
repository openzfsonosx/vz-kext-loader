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

# Ad-hoc sign so Gatekeeper/TCC treat it as a stable identity locally.
codesign --force --sign - "$APP" >/dev/null 2>&1 || \
    echo "   (codesign skipped; unsigned bundle still runs locally)"

echo ">> done: $APP"
echo "   run:  open $APP        (or: $BIN  to see console logs)"
