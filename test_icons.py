"""Tests for the shipped icon files.

The app shows its icon in the title bar and the Dock. macOS answers a bundle
whose icon file is missing or unreadable with a placeholder of its own, so
the files are checked here rather than trusted, and the artwork is confirmed
to be the one the rest of the project ships.
"""

from __future__ import annotations

import struct
import sys
from pathlib import Path

import pytest
from PIL import Image, ImageChops

sys.path.insert(0, str(Path(__file__).resolve().parent / "tools"))

from verify_bundle_icon import read_icns  # noqa: E402

ASSETS = Path(__file__).resolve().parent / "assets"

# The icon types macOS expects in an .icns, with the pixel size of each.
ICNS_ENTRIES = {
    "ic04": 16,
    "ic05": 32,
    "ic11": 32,
    "ic12": 64,
    "ic07": 128,
    "ic13": 256,
    "ic08": 256,
    "ic14": 512,
    "ic09": 512,
    "ic10": 1024,
}

ICO_SIZES = [(16, 16), (24, 24), (32, 32), (48, 48), (64, 64), (128, 128), (256, 256)]

# Metadata that would name the tool the artwork came from. The icon is drawn
# by tools/make_icons.py, so nothing of the sort belongs in the file.
PROVENANCE_MARKERS = (b"c2pa", b"jumb", b"OpenAI", b"openai", b"Content Credentials")


def mean_difference(left: Image.Image, right: Image.Image) -> float:
    """Average per-pixel difference between two images of the same size."""
    difference = ImageChops.difference(left.convert("RGB"), right.convert("RGB"))
    pixels = difference.convert("L").tobytes()
    return sum(pixels) / len(pixels)


def test_window_icon_is_a_plain_png() -> None:
    data = (ASSETS / "ice_cube.png").read_bytes()
    assert data[:8] == b"\x89PNG\r\n\x1a\n"
    for marker in PROVENANCE_MARKERS:
        assert marker not in data, f"icon carries provenance data: {marker!r}"


def test_window_icon_is_opaque_where_it_matters() -> None:
    """A transparent icon is invisible; the artwork must cover its tile."""
    image = Image.open(ASSETS / "ice_cube.png").convert("RGBA")
    assert image.size == (512, 512)
    alpha = image.getchannel("A")
    assert alpha.getextrema()[1] == 255
    covered = sum(1 for value in alpha.tobytes() if value > 200)
    assert covered > 0.3 * image.width * image.height


def test_icns_carries_every_size_macos_asks_for() -> None:
    entries = read_icns(ASSETS / "app.icns")
    for kind, size in ICNS_ENTRIES.items():
        assert kind in entries, f"missing {kind} ({size}px)"
        payload = entries[kind]
        if payload[:4] == b"\x89PNG":
            width, height = struct.unpack(">II", payload[16:24])
            assert (width, height) == (size, size)


def test_icns_artwork_matches_the_window_icon() -> None:
    """The bundle icon and the title bar icon have to be the same drawing."""
    entries = read_icns(ASSETS / "app.icns")
    bundled = Image.open(ASSETS / "ice_cube.png").convert("RGBA")
    for kind in ("ic10", "ic09", "ic07"):
        payload = entries[kind]
        if payload[:4] != b"\x89PNG":
            continue
        from io import BytesIO

        entry_image = Image.open(BytesIO(payload)).convert("RGBA")
        resized = bundled.resize(entry_image.size, Image.LANCZOS)
        assert mean_difference(entry_image, resized) < 12


def test_ico_carries_every_size_windows_asks_for() -> None:
    image = Image.open(ASSETS / "app.ico")
    assert sorted(image.info["sizes"]) == sorted(ICO_SIZES)


@pytest.mark.parametrize("name", ["ice_cube.png", "app.ico", "app.icns"])
def test_shipped_icons_exist(name: str) -> None:
    assert (ASSETS / name).is_file()
