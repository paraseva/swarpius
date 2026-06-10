#!/usr/bin/env python3
"""Generate cross-platform installer art (app icons + macOS DMG background)
from the brand favicon glyph. Run from agent/installer/. Requires cairosvg +
Pillow (dev-only). Outputs committed to this directory:

  swarpius.icns            - macOS app + DMG volume icon (prebuilt)
  swarpius.iconset/        - source PNGs; regenerate icns on macOS via:
                               iconutil -c icns swarpius.iconset -o swarpius.icns
  swarpius.png             - Linux AppImage / .desktop icon (256x256)
  swarpius.ico             - Windows exe icon (16-256 multi-resolution)
  swarpius-icon-1024.png   - 1024 master (source of truth for the icon)
  dmg-background.png       - macOS install-window background (600x380 = window size)

Colours are the favicon's dark-mode scheme: gold #c9a85a wave/flame, the
accent centre-line set to the #1a1612 background so it reads as a channel.
"""
import io
import os

import cairosvg
from PIL import Image, ImageDraw, ImageFont

BG = "#1a1612"
GOLD = "#c9a85a"
CREAM = "#f5ede0"
HERE = os.path.dirname(os.path.abspath(__file__))

GLYPH = '''<svg width="121" height="91" viewBox="0 0 121 91" xmlns="http://www.w3.org/2000/svg">
<path d="M7.50172 35.4151C7.50172 35.4151 8.59625 30.8536 12.5017 32.4151C17.5017 34.4142 19.1424 43.7811 20.5017 48.916C25.0017 65.9151 30.0017 82.9151 35.5017 82.9151C41.0017 82.9151 50.0017 32.4151 60.5017 32.4151C71.0017 32.4151 79.0017 82.9151 85.5017 82.9151C92.0017 82.9151 97.0018 61.4151 100.502 47.9151C102.639 39.6729 103.915 34.2483 108.502 32.4151C113.089 30.5819 113.502 35.4151 113.502 35.4151" stroke="{w}" stroke-width="15" stroke-linecap="round" stroke-linejoin="bevel" fill="none"/>
<path d="M12.5017 32.4151C17.5017 34.4142 19.1424 43.7811 20.5017 48.9159C25.0017 65.9151 30.0017 82.9151 35.5017 82.9151C41.0017 82.9151 50.0017 32.4151 60.5017 32.4151C71.0017 32.4151 79.0017 82.9151 85.5017 82.9151C92.0017 82.9151 97.0018 61.4151 100.502 47.9151C102.639 39.6729 103.915 34.2483 108.502 32.4151" stroke="{a}" stroke-width="5" stroke-linejoin="bevel" fill="none"/>
<path d="M52.5017 16.896C52.5018 20.696 57.2746 23.2102 60.6597 23.196C63.9969 23.182 69.0137 20.596 69.0137 16.896C69.0137 13.196 60.8217 0 60.8217 0C60.8217 0 52.5017 13.096 52.5017 16.896Z" fill="{w}"/></svg>'''

def squircle(size, rf=0.2237):
    m = Image.new("L", (size, size), 0)
    ImageDraw.Draw(m).rounded_rectangle([0, 0, size - 1, size - 1], radius=int(size * rf), fill=255)
    return m

def make_icon(size):
    # Render glyph at 2x then downscale for crisp edges.
    gw = int(size * 0.62)
    png = cairosvg.svg2png(bytestring=GLYPH.format(w=GOLD, a=BG).encode(),
                           output_width=gw * 2, output_height=int(gw * 2 * 91 / 121))
    g = Image.open(io.BytesIO(png)).convert("RGBA").resize(
        (gw, int(gw * 91 / 121)), Image.LANCZOS)
    base = Image.new("RGBA", (size, size), (0, 0, 0, 0))
    base = Image.composite(Image.new("RGBA", (size, size), BG), base, squircle(size))
    base.alpha_composite(g, ((size - g.width) // 2, (size - g.height) // 2))
    return base

# Master + iconset
master = make_icon(1024)
master.save(os.path.join(HERE, "swarpius-icon-1024.png"))
iconset = os.path.join(HERE, "swarpius.iconset")
os.makedirs(iconset, exist_ok=True)
for base_pt in (16, 32, 128, 256, 512):
    for scale in (1, 2):
        px = base_pt * scale
        name = f"icon_{base_pt}x{base_pt}{'@2x' if scale == 2 else ''}.png"
        make_icon(px).save(os.path.join(iconset, name))
# Prebuilt icns from master (macOS)
master.save(os.path.join(HERE, "swarpius.icns"), format="ICNS")
# Linux AppImage / .desktop icon (build-appimage.sh picks this up directly).
make_icon(256).save(os.path.join(HERE, "swarpius.png"))
# Windows multi-resolution .ico (wired into swarpius-windows.spec).
ico_sizes = [(16, 16), (24, 24), (32, 32), (48, 48), (64, 64), (128, 128), (256, 256)]
make_icon(256).save(os.path.join(HERE, "swarpius.ico"), format="ICO", sizes=ico_sizes)

# Must be exactly the window size — create-dmg/Finder render it 1:1, so a
# larger image is cropped. Cream keeps Finder's black icon labels readable.
def font(sz):
    for p in ("/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf",
              "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf"):
        if os.path.exists(p):
            return ImageFont.truetype(p, sz)
    return ImageFont.load_default()

W, H = 600, 380
bg = Image.new("RGB", (W, H), (245, 237, 224))  # cream
d = ImageDraw.Draw(bg)
maroon = (107, 44, 62)
ax1, ax2, ay = 175 + 48, 425 - 48, 190
d.line([(ax1, ay), (ax2 - 18, ay)], fill=maroon, width=6)
d.polygon([(ax2, ay), (ax2 - 18, ay - 11), (ax2 - 18, ay + 11)], fill=maroon)
cap = "Drag Swarpius to Applications"
f = font(21)
tb = d.textbbox((0, 0), cap, font=f)
d.text(((W - (tb[2] - tb[0])) // 2, 46), cap, font=f, fill=maroon)
bg.save(os.path.join(HERE, "dmg-background.png"))
print("wrote: swarpius.icns, swarpius.iconset/, swarpius-icon-1024.png, dmg-background.png")
