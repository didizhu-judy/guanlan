"""Generate icon.icns for T212-Dashboard.app.

Design: rounded-square green gradient with ascending white candlesticks +
a diagonal up-arrow. Bullish vibes.

Run:
    python3 make_icon.py
"""

from __future__ import annotations
import math
import shutil
import subprocess
from pathlib import Path

from PIL import Image, ImageDraw, ImageFilter

HERE = Path(__file__).parent
ICONSET = HERE / "icon.iconset"
ICNS_OUT = HERE / "icon.icns"
APP_RESOURCES = HERE / "T212-Dashboard.app" / "Contents" / "Resources"


def make_icon(size: int) -> Image.Image:
    """Render one icon at the given pixel size."""
    radius = int(size * 0.22)   # macOS Big Sur+ corner radius
    img = Image.new("RGBA", (size, size), (0, 0, 0, 0))

    # --- Background: diagonal green gradient -----------------------------
    bg = Image.new("RGBA", (size, size))
    top    = (10, 70, 30)      # deep emerald
    middle = (40, 150, 60)
    bottom = (90, 220, 100)    # bright leaf green
    pixels = bg.load()
    for y in range(size):
        # vertical gradient with a slight horizontal tilt for depth
        t = y / max(size - 1, 1)
        if t < 0.55:
            u = t / 0.55
            r = int(top[0]    + (middle[0] - top[0])    * u)
            g = int(top[1]    + (middle[1] - top[1])    * u)
            b = int(top[2]    + (middle[2] - top[2])    * u)
        else:
            u = (t - 0.55) / 0.45
            r = int(middle[0] + (bottom[0] - middle[0]) * u)
            g = int(middle[1] + (bottom[1] - middle[1]) * u)
            b = int(middle[2] + (bottom[2] - middle[2]) * u)
        for x in range(size):
            pixels[x, y] = (r, g, b, 255)

    # Subtle radial highlight in upper-left corner
    glow = Image.new("RGBA", (size, size), (0, 0, 0, 0))
    gdraw = ImageDraw.Draw(glow)
    cx, cy = size * 0.25, size * 0.20
    max_r = size * 0.55
    steps = 12
    for i in range(steps, 0, -1):
        rr = max_r * i / steps
        a = int(60 * (1 - i / steps))
        gdraw.ellipse((cx - rr, cy - rr, cx + rr, cy + rr),
                       fill=(255, 255, 255, a))
    bg = Image.alpha_composite(bg, glow)

    # --- Rounded-corner mask ---------------------------------------------
    mask = Image.new("L", (size, size), 0)
    ImageDraw.Draw(mask).rounded_rectangle(
        (0, 0, size, size), radius=radius, fill=255
    )
    img.paste(bg, mask=mask)

    # --- Candlesticks -----------------------------------------------------
    draw = ImageDraw.Draw(img, "RGBA")
    n_candles = 5
    margin_x = size * 0.17
    margin_top = size * 0.22
    margin_bot = size * 0.20
    inner_w = size - 2 * margin_x
    spacing = inner_w / n_candles

    base_y = size - margin_bot
    top_y  = margin_top
    body_h = size * 0.13
    body_w = spacing * 0.46
    wick_w = max(2, int(size / 180))
    candle_fill = (255, 255, 255, 245)
    wick_fill   = (255, 255, 255, 235)
    candle_corner = max(1, int(size * 0.012))

    candle_centers = []
    for i in range(n_candles):
        cx = margin_x + spacing * (i + 0.5)
        progress = i / (n_candles - 1)
        center_y = base_y - (base_y - top_y) * progress
        body_top = center_y - body_h / 2
        body_bot = center_y + body_h / 2
        wick_top = body_top - size * 0.045
        wick_bot = body_bot + size * 0.045

        # Soft drop shadow
        shadow_offset = max(2, int(size / 240))
        shadow_fill = (0, 0, 0, 60)
        draw.rounded_rectangle(
            [cx - body_w/2 + shadow_offset, body_top + shadow_offset,
             cx + body_w/2 + shadow_offset, body_bot + shadow_offset],
            radius=candle_corner, fill=shadow_fill,
        )

        # Wick
        draw.rectangle([cx - wick_w/2, wick_top, cx + wick_w/2, body_top],
                       fill=wick_fill)
        draw.rectangle([cx - wick_w/2, body_bot, cx + wick_w/2, wick_bot],
                       fill=wick_fill)
        # Body
        draw.rounded_rectangle(
            [cx - body_w/2, body_top, cx + body_w/2, body_bot],
            radius=candle_corner, fill=candle_fill,
        )
        candle_centers.append((cx, center_y))

    # --- Diagonal trend arrow over the candles ---------------------------
    arrow_color = (255, 255, 255, 230)
    arrow_w = max(3, int(size / 60))
    x1, y1 = candle_centers[0][0],  candle_centers[0][1]
    x2, y2 = candle_centers[-1][0], candle_centers[-1][1]
    # Shorten so arrowhead sits flush
    head_size = size * 0.07
    angle = math.atan2(y2 - y1, x2 - x1)
    x2_line = x2 - math.cos(angle) * head_size * 0.55
    y2_line = y2 - math.sin(angle) * head_size * 0.55
    draw.line([(x1, y1), (x2_line, y2_line)],
              fill=arrow_color, width=arrow_w)

    # Arrowhead triangle (filled)
    tip = (x2, y2)
    left_angle = angle + math.pi - math.radians(28)
    right_angle = angle + math.pi + math.radians(28)
    p_left  = (x2 + math.cos(left_angle)  * head_size,
                y2 + math.sin(left_angle)  * head_size)
    p_right = (x2 + math.cos(right_angle) * head_size,
                y2 + math.sin(right_angle) * head_size)
    draw.polygon([tip, p_left, p_right], fill=arrow_color)

    # Re-apply the rounded mask so anything that crept outside is clipped
    final = Image.new("RGBA", (size, size), (0, 0, 0, 0))
    final.paste(img, mask=mask)
    return final


def main() -> None:
    if ICONSET.exists():
        shutil.rmtree(ICONSET)
    ICONSET.mkdir()

    # macOS expects: icon_<N>x<N>.png and icon_<N>x<N>@2x.png
    sizes = [(16, "16x16"), (32, "32x32"), (128, "128x128"),
             (256, "256x256"), (512, "512x512")]
    for px, label in sizes:
        make_icon(px).save(ICONSET / f"icon_{label}.png", "PNG")
        make_icon(px * 2).save(ICONSET / f"icon_{label}@2x.png", "PNG")

    # Compile to .icns via macOS's iconutil
    subprocess.run(
        ["iconutil", "-c", "icns", str(ICONSET), "-o", str(ICNS_OUT)],
        check=True,
    )

    # Install into the .app
    APP_RESOURCES.mkdir(parents=True, exist_ok=True)
    shutil.copy(ICNS_OUT, APP_RESOURCES / "icon.icns")

    print(f"✓ Generated {ICNS_OUT}")
    print(f"✓ Installed → {APP_RESOURCES / 'icon.icns'}")


if __name__ == "__main__":
    main()
