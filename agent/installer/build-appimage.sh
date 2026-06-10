#!/bin/bash
# Wrap the PyInstaller --onedir Linux output (dist/Swarpius/) into an
# AppImage. Run from agent/ after running:
#
#     pyinstaller installer/swarpius-linux.spec
#
# Outputs: dist/Swarpius-x86_64.AppImage
set -euo pipefail

cd "$(dirname "$0")/.."  # agent/

BUNDLE_DIR="dist/Swarpius"
APPDIR="dist/Swarpius.AppDir"
ICON_SRC="installer/swarpius.png"
SVG_SRC="../web-client/public/swarpius-favicon.svg"
OUTPUT="dist/Swarpius-x86_64.AppImage"
APPIMAGETOOL="${APPIMAGETOOL:-appimagetool}"

if [ ! -d "$BUNDLE_DIR" ]; then
  echo "error: $BUNDLE_DIR not found — run pyinstaller installer/swarpius-linux.spec first" >&2
  exit 1
fi

if ! command -v "$APPIMAGETOOL" >/dev/null 2>&1; then
  echo "error: appimagetool not found in PATH. Override with APPIMAGETOOL=/path/to/appimagetool." >&2
  echo "Download: https://github.com/AppImage/AppImageKit/releases" >&2
  exit 1
fi

# Render an icon PNG from the SVG when one isn't already present.
# rsvg-convert (librsvg2-bin) is the lightest option; ImageMagick's
# convert is the fallback.
if [ ! -f "$ICON_SRC" ]; then
  if [ ! -f "$SVG_SRC" ]; then
    echo "error: neither $ICON_SRC nor $SVG_SRC found" >&2
    exit 1
  fi
  if command -v rsvg-convert >/dev/null 2>&1; then
    rsvg-convert -w 256 -h 256 "$SVG_SRC" -o "$ICON_SRC"
  elif command -v convert >/dev/null 2>&1; then
    convert -background none -resize 256x256 "$SVG_SRC" "$ICON_SRC"
  else
    echo "error: install librsvg2-bin (rsvg-convert) or imagemagick (convert) to rasterise the icon, or supply $ICON_SRC directly" >&2
    exit 1
  fi
fi

rm -rf "$APPDIR" "$OUTPUT"
mkdir -p "$APPDIR/usr/bin"

# Move the whole PyInstaller bundle under usr/bin/ rather than copying
# binary-by-binary — keeps relative paths between swarpius and its
# bundled libs intact.
cp -a "$BUNDLE_DIR/." "$APPDIR/usr/bin/"

cp installer/AppRun "$APPDIR/AppRun"
chmod +x "$APPDIR/AppRun"
cp installer/swarpius.desktop "$APPDIR/swarpius.desktop"
cp "$ICON_SRC" "$APPDIR/swarpius.png"

ARCH=x86_64 "$APPIMAGETOOL" "$APPDIR" "$OUTPUT"
echo "built: $OUTPUT"
