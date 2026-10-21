#!/usr/bin/env python3
"""Draw the application icon.

Run with `.venv/bin/python tools/make_icons.py`.

The artwork is generated here instead of being imported from somewhere else,
so every shipped file is reproducible from this one script and carries no
provenance metadata from whatever produced it. The three outputs are:

    assets/ice_cube.png   the window icon, also the master for everything else
    assets/app.icns       the macOS bundle icon
    assets/app.ico        the Windows executable icon

`iconutil` is macOS only, so `assets/app.icns` can only be rebuilt there; the
other two are written anywhere Pillow runs.
"""

from __future__ import annotations

import math
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

from PIL import Image, ImageDraw, ImageFilter

ROOT = Path(__file__).resolve().parent.parent
ASSETS = ROOT / "assets"

MASTER_SIZE = 1024
SUPERSAMPLE = 2
# The window icon is drawn smaller than the master: a Dock tile is 128 points,
# and 512 pixels is plenty of detail for it and for the README.
WINDOW_ICON_SIZE = 512

# The tile is a deep blue squircle; the cube on it is near white so the
# silhouette still reads at 16 pixels in a Dock.
TILE_TOP = (9, 58, 140)
TILE_BOTTOM = (24, 122, 220)
TILE_HIGHLIGHT = (86, 176, 255)

CUBE_TOP = (255, 255, 255)
CUBE_LEFT = (214, 240, 255)
CUBE_RIGHT = (163, 212, 245)
CUBE_EDGE = (255, 255, 255)

TILE_INSET = 0.072
TILE_CORNER_POWER = 5.0
CUBE_CENTER_Y = 0.545
CUBE_RADIUS = 0.245

ICONSET_SIZES = {
    "icon_16x16.png": 16,
    "icon_16x16@2x.png": 32,
    "icon_32x32.png": 32,
    "icon_32x32@2x.png": 64,
    "icon_128x128.png": 128,
    "icon_128x128@2x.png": 256,
    "icon_256x256.png": 256,
    "icon_256x256@2x.png": 512,
    "icon_512x512.png": 512,
    "icon_512x512@2x.png": 1024,
}


def squircle_points(size: int, inset: int, power: float) -> list[tuple[float, float]]:
    """Outline of a superellipse, which is the shape of a macOS app icon."""
    radius = (size - 2 * inset) / 2
    center = size / 2
    steps = 720
    points = []
    for index in range(steps):
        angle = 2 * math.pi * index / steps
        cosine, sine = math.cos(angle), math.sin(angle)
        x = math.copysign(abs(cosine) ** (2 / power), cosine)
        y = math.copysign(abs(sine) ** (2 / power), sine)
        points.append((center + x * radius, center + y * radius))
    return points


def cube_points(size: int) -> dict[str, tuple[float, float]]:
    """Vertices of an isometric cube: the outline and the three inner edges."""
    center_x = size / 2
    center_y = size * CUBE_CENTER_Y
    radius = size * CUBE_RADIUS
    half_width = radius * math.sqrt(3) / 2
    half_height = radius / 2
    return {
        "top": (center_x, center_y - radius),
        "upper_left": (center_x - half_width, center_y - half_height),
        "upper_right": (center_x + half_width, center_y - half_height),
        "center": (center_x, center_y),
        "lower_left": (center_x - half_width, center_y + half_height),
        "lower_right": (center_x + half_width, center_y + half_height),
        "bottom": (center_x, center_y + radius),
    }


def draw_icon(size: int) -> Image.Image:
    """Render the icon at `size` pixels square."""
    scale = SUPERSAMPLE
    canvas_size = size * scale
    inset = int(canvas_size * TILE_INSET)

    # Vertical gradient across the tile.
    tile = Image.new("RGBA", (canvas_size, canvas_size), TILE_TOP + (255,))
    gradient = Image.new("RGB", (1, canvas_size))
    gradient_pixels = gradient.load()
    for y in range(canvas_size):
        fraction = y / max(1, canvas_size - 1)
        gradient_pixels[0, y] = tuple(
            round(TILE_TOP[channel] + (TILE_BOTTOM[channel] - TILE_TOP[channel]) * fraction)
            for channel in range(3)
        )
    tile = gradient.resize((canvas_size, canvas_size), Image.BILINEAR).convert("RGBA")

    # A soft light from the upper left, so the tile is not flat.
    light = Image.new("L", (canvas_size, canvas_size), 0)
    light_draw = ImageDraw.Draw(light)
    light_draw.ellipse(
        (-canvas_size * 0.35, -canvas_size * 0.55, canvas_size * 0.85, canvas_size * 0.55),
        fill=110,
    )
    light = light.filter(ImageFilter.GaussianBlur(canvas_size * 0.12))
    tile = Image.composite(
        Image.new("RGBA", tile.size, TILE_HIGHLIGHT + (255,)), tile, light.point(lambda v: v * 255 // 255)
    )

    outline = squircle_points(canvas_size, inset, TILE_CORNER_POWER)
    mask = Image.new("L", (canvas_size, canvas_size), 0)
    ImageDraw.Draw(mask).polygon(outline, fill=255)

    # The cube, with its shadow underneath.
    points = cube_points(canvas_size)
    shadow = Image.new("L", (canvas_size, canvas_size), 0)
    shadow_draw = ImageDraw.Draw(shadow)
    shadow_offset = canvas_size * 0.022
    shadow_draw.polygon(
        [(x, y + shadow_offset) for x, y in (points["top"], points["upper_right"], points["lower_right"], points["bottom"], points["lower_left"], points["upper_left"])],
        fill=110,
    )
    shadow = shadow.filter(ImageFilter.GaussianBlur(canvas_size * 0.018))
    tile.paste(Image.new("RGBA", tile.size, (4, 26, 62, 255)), (0, 0), shadow)

    cube = Image.new("RGBA", (canvas_size, canvas_size), (0, 0, 0, 0))
    cube_draw = ImageDraw.Draw(cube)
    cube_draw.polygon(
        [points["top"], points["upper_right"], points["center"], points["upper_left"]],
        fill=CUBE_TOP + (255,),
    )
    cube_draw.polygon(
        [points["upper_left"], points["center"], points["bottom"], points["lower_left"]],
        fill=CUBE_LEFT + (255,),
    )
    cube_draw.polygon(
        [points["center"], points["upper_right"], points["lower_right"], points["bottom"]],
        fill=CUBE_RIGHT + (255,),
    )

    edge_width = max(2, round(canvas_size * 0.011))
    cube_draw.line(
        [
            points["top"],
            points["upper_right"],
            points["lower_right"],
            points["bottom"],
            points["lower_left"],
            points["upper_left"],
            points["top"],
        ],
        fill=CUBE_EDGE + (255,),
        width=edge_width,
        joint="curve",
    )
    for edge in ("upper_left", "upper_right", "bottom"):
        cube_draw.line(
            [points["center"], points[edge]],
            fill=CUBE_EDGE + (255,),
            width=edge_width,
        )
    tile = Image.alpha_composite(tile, cube)

    # A hairline along the top edge of the tile.
    rim = Image.new("RGBA", (canvas_size, canvas_size), (0, 0, 0, 0))
    rim_draw = ImageDraw.Draw(rim)
    rim_width = max(2, round(canvas_size * 0.006))
    rim_draw.line(outline + [outline[0]], fill=(255, 255, 255, 70), width=rim_width, joint="curve")
    rim = rim.filter(ImageFilter.GaussianBlur(canvas_size * 0.002))
    tile = Image.alpha_composite(tile, rim)

    # Clip the artwork to the squircle.
    tile.putalpha(mask)

    return tile.resize((size, size), Image.LANCZOS)


def write_png(image: Image.Image, path: Path) -> None:
    """Write a PNG without any metadata."""
    image.save(path, format="PNG", optimize=True)


def write_icns(master: Image.Image, path: Path) -> None:
    """Build the macOS icon set and let iconutil assemble the .icns."""
    if shutil.which("iconutil") is None:
        raise SystemExit(
            "iconutil is only available on macOS; assets/app.icns cannot be "
            "rebuilt here."
        )
    with tempfile.TemporaryDirectory() as directory:
        iconset = Path(directory) / "app.iconset"
        iconset.mkdir()
        for name, size in ICONSET_SIZES.items():
            write_png(master.resize((size, size), Image.LANCZOS), iconset / name)
        subprocess.run(
            ["iconutil", "-c", "icns", str(iconset), "-o", str(path)],
            check=True,
        )


def write_ico(master: Image.Image, path: Path) -> None:
    """Write the Windows icon with every size the shell asks for."""
    master.save(
        path,
        format="ICO",
        sizes=[(16, 16), (24, 24), (32, 32), (48, 48), (64, 64), (128, 128), (256, 256)],
    )


def main() -> int:
    ASSETS.mkdir(parents=True, exist_ok=True)
    master = draw_icon(MASTER_SIZE)
    write_png(master.resize((WINDOW_ICON_SIZE, WINDOW_ICON_SIZE), Image.LANCZOS),
              ASSETS / "ice_cube.png")
    write_ico(master, ASSETS / "app.ico")
    write_icns(master, ASSETS / "app.icns")
    print(f"Wrote {ASSETS / 'ice_cube.png'}")
    print(f"Wrote {ASSETS / 'app.ico'}")
    print(f"Wrote {ASSETS / 'app.icns'}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
