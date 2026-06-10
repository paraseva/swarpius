#!/bin/bash
# Wrap the PyInstaller .app bundle output (dist/Swarpius.app) into a
# drag-to-Applications .dmg. Run from agent/ after:
#
#     pyinstaller installer/swarpius-macos.spec
#
# Outputs: dist/Swarpius-arm64.dmg
#
# Signing + notarisation are NOT performed here. In CI the `build-macos`
# job codesigns dist/Swarpius.app (hardened runtime) *before* calling this
# script, then codesigns + notarises + staples the resulting DMG after.
# See .github/workflows/installer.yml.
set -euo pipefail

cd "$(dirname "$0")/.."  # agent/

APP_BUNDLE="dist/Swarpius.app"
OUTPUT="dist/Swarpius-arm64.dmg"

if [ ! -d "$APP_BUNDLE" ]; then
  echo "error: $APP_BUNDLE not found — run pyinstaller installer/swarpius-macos.spec first" >&2
  exit 1
fi

if ! command -v create-dmg >/dev/null 2>&1; then
  echo "error: create-dmg not found. Install with: brew install create-dmg" >&2
  exit 1
fi

rm -f "$OUTPUT"

# --no-internet-enable: macOS deprecated the flag and warns without it.
# --background must match --window-size: create-dmg shows it 1:1, else it crops.
create-dmg \
  --volname "Swarpius" \
  --volicon "installer/swarpius.icns" \
  --background "installer/dmg-background.png" \
  --window-pos 200 120 \
  --window-size 600 380 \
  --icon-size 100 \
  --icon "Swarpius.app" 175 190 \
  --app-drop-link 425 190 \
  --hide-extension "Swarpius.app" \
  --no-internet-enable \
  "$OUTPUT" \
  "$APP_BUNDLE"

echo "built: $OUTPUT"
