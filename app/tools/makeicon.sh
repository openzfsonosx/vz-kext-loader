#!/bin/bash
# Build AppIcon.icns from the generated 1024 PNG.
#   ./tools/makeicon.sh   ->  app/AppIcon.icns
set -euo pipefail
cd "$(dirname "$0")/.."

PNG="$(mktemp -d)/icon_1024.png"
swift tools/makeicon.swift "$PNG"

ICONSET="$(mktemp -d)/AppIcon.iconset"
mkdir -p "$ICONSET"
for sz in 16 32 128 256 512; do
    sips -z $sz $sz         "$PNG" --out "$ICONSET/icon_${sz}x${sz}.png"    >/dev/null
    sips -z $((sz*2)) $((sz*2)) "$PNG" --out "$ICONSET/icon_${sz}x${sz}@2x.png" >/dev/null
done
iconutil -c icns "$ICONSET" -o AppIcon.icns
echo ">> wrote AppIcon.icns"
