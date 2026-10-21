#!/usr/bin/env python3
"""Check that the built application carries an icon macOS can read.

macOS answers a bundle whose declared icon is missing or unreadable with a
placeholder of its own, so the icon is verified after the build instead of
being assumed. Run with `.venv/bin/python tools/verify_bundle_icon.py
dist/ICEDetentionPathway.app`.
"""

from __future__ import annotations

import plistlib
import struct
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
ASSETS = ROOT / "assets"

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


def read_icns(path: Path) -> dict[str, bytes]:
    """Return the entries of an .icns container, keyed by icon type."""
    data = path.read_bytes()
    if data[:4] != b"icns":
        raise ValueError(f"{path} is not an icns container")
    if struct.unpack(">I", data[4:8])[0] != len(data):
        raise ValueError(f"{path} is truncated")
    entries: dict[str, bytes] = {}
    offset = 8
    while offset < len(data):
        kind = data[offset : offset + 4].decode("latin1")
        length = struct.unpack(">I", data[offset + 4 : offset + 8])[0]
        entries[kind] = data[offset + 8 : offset + length]
        offset += length
    return entries


def check_icns(path: Path) -> None:
    """Fail unless the file is an .icns macOS can read."""
    entries = read_icns(path)
    for kind, size in ICNS_ENTRIES.items():
        if kind not in entries:
            raise ValueError(f"{path} has no {kind} entry ({size}px)")
        payload = entries[kind]
        if payload[:4] == b"\x89PNG":
            width, height = struct.unpack(">II", payload[16:24])
            if (width, height) != (size, size):
                raise ValueError(f"{path} {kind} is {width}x{height}, expected {size}")


def check_ico(path: Path) -> None:
    """Fail unless the file holds every size the Windows shell asks for."""
    from PIL import Image

    sizes = sorted(Image.open(path).info.get("sizes", []))
    if sizes != sorted(ICO_SIZES):
        raise ValueError(f"{path} holds {sizes}, expected {sorted(ICO_SIZES)}")


def check_macos_app(app_path: Path) -> None:
    """Fail unless the bundle declares an icon file it can actually read."""
    plist_path = app_path / "Contents" / "Info.plist"
    if not plist_path.is_file():
        raise ValueError(f"{app_path} has no Info.plist")
    plist = plistlib.loads(plist_path.read_bytes())
    icon_name = plist.get("CFBundleIconFile")
    if not icon_name:
        raise ValueError(f"{app_path} declares no CFBundleIconFile")
    icon_path = app_path / "Contents" / "Resources" / icon_name
    if not icon_path.is_file():
        raise ValueError(f"{app_path} declares {icon_name}, which is not in the bundle")
    check_icns(icon_path)


def main(argv: list[str]) -> int:
    if len(argv) != 2:
        print(__doc__)
        return 2
    app_path = Path(argv[1])
    try:
        check_ico(ASSETS / "app.ico")
        if app_path.suffix == ".app":
            check_macos_app(app_path)
        else:
            print(f"{app_path} is not a macOS bundle; checked the icon files only")
    except ValueError as error:
        print(f"Icon check failed: {error}")
        return 1
    print(f"Icons verified for {app_path}")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
