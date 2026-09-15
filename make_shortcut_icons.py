"""Generate new desktop icons matching the provided MouseSpotHelper design."""
from __future__ import annotations

import math
from pathlib import Path

from PIL import Image, ImageDraw

BASE = Path(__file__).resolve().parent
SIZES = [16, 24, 32, 48, 64, 128, 256]


def rounded_rect(draw, box, radius, fill):
    draw.rounded_rectangle(box, radius=radius, fill=fill)


def draw_gear(draw, cx, cy, outer_r, inner_r, teeth, fill, outline=None, outline_width=1):
    """Draw a simple gear using a polygon with teeth."""
    points = []
    for i in range(teeth * 2):
        angle = 2 * math.pi * i / (teeth * 2) - math.pi / 2
        if i % 2 == 0:
            r = outer_r
        else:
            r = inner_r + (outer_r - inner_r) * 0.35
        x = cx + r * math.cos(angle)
        y = cy + r * math.sin(angle)
        points.append((x, y))
    draw.polygon(points, fill=fill, outline=outline, width=outline_width)
    # center hole
    hole_r = inner_r * 0.55
    draw.ellipse([cx - hole_r, cy - hole_r, cx + hole_r, cy + hole_r], fill=(255, 255, 255, 255))


def draw_arrow(draw, x0, y0, size, fill):
    """Draw a simple up-right arrow inside a rounded square badge."""
    pad = size * 0.25
    x1 = x0 + size - pad
    y1 = y0 + size - pad
    # arrow shaft
    shaft_x0 = x0 + size * 0.30
    shaft_y1 = y1 - size * 0.30
    shaft_x1 = x1 - size * 0.35
    shaft_y0 = y0 + size * 0.35
    draw.line([(shaft_x0, shaft_y1), (shaft_x1, shaft_y0)], fill=fill, width=max(1, int(size * 0.12)))
    # arrow head
    head_len = size * 0.22
    draw.polygon([
        (shaft_x1, shaft_y0 - head_len),
        (shaft_x1 + head_len, shaft_y0),
        (shaft_x1, shaft_y0 + head_len * 0.4),
    ], fill=fill)


def make_mouse_spot_icon(path: Path) -> None:
    images = []
    for s in SIZES:
        img = Image.new("RGBA", (s, s), (0, 0, 0, 0))
        d = ImageDraw.Draw(img)
        pad = max(1, int(s * 0.06))
        # outer red rounded square
        rounded_rect(d, [pad, pad, s - 1 - pad, s - 1 - pad], radius=max(4, s // 8), fill=(239, 68, 68, 255))
        # inner white rounded square
        inner_pad = max(2, int(s * 0.12))
        rounded_rect(
            d,
            [inner_pad, inner_pad, s - 1 - inner_pad, s - 1 - inner_pad],
            radius=max(3, s // 12),
            fill=(255, 255, 255, 255),
        )
        # two gray gears
        gear_fill = (120, 120, 120, 255)
        gear_outline = (90, 90, 90, 255)
        # large gear
        g1_r = s * 0.22
        draw_gear(d, s * 0.42, s * 0.45, g1_r, g1_r * 0.55, 10, gear_fill, gear_outline, max(1, s // 64))
        # small gear
        g2_r = s * 0.15
        draw_gear(d, s * 0.66, s * 0.58, g2_r, g2_r * 0.55, 8, gear_fill, gear_outline, max(1, s // 80))
        # blue arrow badge bottom-left
        badge_s = max(6, int(s * 0.28))
        badge_x = inner_pad + max(1, s // 32)
        badge_y = s - inner_pad - badge_s - max(1, s // 32)
        rounded_rect(d, [badge_x, badge_y, badge_x + badge_s, badge_y + badge_s], radius=max(2, s // 20), fill=(255, 255, 255, 255))
        draw_arrow(d, badge_x, badge_y, badge_s, fill=(30, 136, 229, 255))
        images.append(img)
    images[-1].save(path, format="ICO", sizes=[(im.width, im.height) for im in images], append_images=images[:-1])
    images[-1].save(path.with_suffix(".png"))
    print(f"wrote {path}")


def make_llm_monitor_icon(path: Path) -> None:
    """Companion icon in the same visual family, dark with green accent."""
    images = []
    for s in SIZES:
        img = Image.new("RGBA", (s, s), (0, 0, 0, 0))
        d = ImageDraw.Draw(img)
        pad = max(1, int(s * 0.06))
        # outer dark rounded square
        rounded_rect(d, [pad, pad, s - 1 - pad, s - 1 - pad], radius=max(4, s // 8), fill=(37, 37, 38, 255))
        # inner white rounded square
        inner_pad = max(2, int(s * 0.12))
        rounded_rect(
            d,
            [inner_pad, inner_pad, s - 1 - inner_pad, s - 1 - inner_pad],
            radius=max(3, s // 12),
            fill=(255, 255, 255, 255),
        )
        # table/grid lines
        left = inner_pad + s * 0.10
        top = inner_pad + s * 0.18
        right = s - inner_pad - s * 0.10
        bottom = s - inner_pad - s * 0.10
        d.rectangle([left, top, right, bottom], outline=(79, 193, 255, 255), width=max(1, s // 40))
        for i in (1, 2):
            y = top + (bottom - top) * i / 3
            d.line([(left, y), (right, y)], fill=(180, 180, 180, 255), width=max(1, s // 64))
            x = left + (right - left) * i / 3
            d.line([(x, top), (x, bottom)], fill=(180, 180, 180, 255), width=max(1, s // 64))
        # colored chips
        chip_h = max(2, int(s * 0.07))
        chip_w = max(4, int(s * 0.12))
        colors = [(255, 235, 59, 255), (76, 175, 80, 255), (239, 68, 68, 255)]
        for i, col in enumerate(colors):
            y = top + (bottom - top) * (i + 0.35) / 3
            x = left + (right - left) * 0.55
            d.rounded_rectangle([x, y, x + chip_w, y + chip_h], radius=max(1, s // 40), fill=col)
        # green status dot top-right
        sx, sy = s * 0.78, s * 0.18
        r = max(2, s // 14)
        d.ellipse([sx - r, sy - r, sx + r, sy + r], fill=(76, 175, 80, 255))
        images.append(img)
    images[-1].save(path, format="ICO", sizes=[(im.width, im.height) for im in images], append_images=images[:-1])
    images[-1].save(path.with_suffix(".png"))
    print(f"wrote {path}")


if __name__ == "__main__":
    make_mouse_spot_icon(BASE / "mouse_spot_icon.ico")
    make_llm_monitor_icon(BASE / "llm_task_monitor_icon.ico")
    print("done")
