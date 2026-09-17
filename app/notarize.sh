#!/bin/bash
# Notarize + staple the signed VZKextLoader.app, then wrap it in a DMG.
#
# Same procedure as the ZFS pkg build: source your key file first, then run.
#
#   . ./.keys            # sets PKG_NOTARIZE_KEY (app-specific pass), etc.
#   ./notarize.sh
#
# Uses the same variables as scripts/pkg_macos.sh in the zfs tree:
#   PKG_NOTARIZE_KEY   app-specific password for the notary submission (required)
#   PKG_APPLE_ID       Apple ID for notarization  (default: lundman@lundman.net)
#   PKG_TEAM_ID        Developer team id          (default: 735AM5QEU3)
#
# Prereq: VZKextLoader.app already built + Developer ID signed (./build-app.sh).
# Because build-app.sh signs the runtime inside-out with hardened runtime +
# timestamps, the bundled python3/r2/wheels are all covered by notarization.
set -euo pipefail
cd "$(dirname "$0")"

APP="VZKextLoader.app"
APPLE_ID="${PKG_APPLE_ID:-lundman@lundman.net}"
TEAM_ID="${PKG_TEAM_ID:-735AM5QEU3}"

[ -d "$APP" ] || { echo "no $APP — run ./build-app.sh first" >&2; exit 1; }
if [ -z "${PKG_NOTARIZE_KEY:-}" ]; then
    echo "\$PKG_NOTARIZE_KEY not set — source your key file first:  . ./.keys" >&2
    exit 1
fi

NOTARYTOOL="$(xcrun -f notarytool 2>/dev/null || true)"
[ -n "$NOTARYTOOL" ] || { echo "notarytool not found (need Xcode command line tools)" >&2; exit 1; }

ZIP="VZKextLoader-notarize.zip"
echo ">> zipping $APP for submission"
rm -f "$ZIP"
/usr/bin/ditto -c -k --keepParent "$APP" "$ZIP"

echo ">> notarize: notarytool ($NOTARYTOOL) as $APPLE_ID / $TEAM_ID"
"$NOTARYTOOL" submit --wait \
    --apple-id "$APPLE_ID" --team-id "$TEAM_ID" --password "$PKG_NOTARIZE_KEY" \
    "$ZIP"

echo ">> stapling the ticket into the app"
xcrun stapler staple "$APP"
xcrun stapler validate "$APP"
spctl -a -vv -t exec "$APP" || true      # Gatekeeper assessment (informational)

# DMG for distribution.
VER=$(/usr/libexec/PlistBuddy -c "Print :CFBundleShortVersionString" "$APP/Contents/Info.plist" 2>/dev/null || echo 0.0.0)
DMG="vz-kext-loader-${VER}.dmg"
echo ">> building $DMG"
rm -f "$DMG"
STAGE=$(mktemp -d)
cp -R "$APP" "$STAGE/"
ln -s /Applications "$STAGE/Applications"
hdiutil create -volname "vz-kext-loader" -srcfolder "$STAGE" -ov -format UDZO "$DMG" >/dev/null
rm -rf "$STAGE" "$ZIP"

echo ">> done: $DMG (notarized + stapled)"
