#!/usr/bin/env python3
"""Draws the app icons into web/icons/. Usage: python3 scripts/make-icons.py

A weight plate seen face on: a white rim, an accent groove, a white hub and
the bar hole, on the accent color. Standard library only, so it runs without
the venv. The plate stays inside the central 80% circle that Android's
maskable icons keep, so one drawing serves every size and purpose.
"""

from __future__ import annotations

import math
import struct
import zlib
from pathlib import Path

ACCENT = (0x1D, 0x4E, 0xD0)
WHITE = (0xFF, 0xFF, 0xFF)

# (radius as a fraction of the icon size, color), outermost first.
RINGS = [(0.34, WHITE), (0.255, ACCENT), (0.13, WHITE), (0.05, ACCENT)]

OUT = Path(__file__).resolve().parent.parent / "web" / "icons"


def pixel(x: float, y: float, size: int) -> tuple[int, int, int]:
    """Each ring's edge is antialiased by how far the pixel center is from it."""
    d = math.hypot(x - size / 2, y - size / 2)
    color = ACCENT
    for frac, ring in RINGS:
        coverage = max(0.0, min(1.0, frac * size - d + 0.5))
        if coverage:
            color = tuple(round(c * (1 - coverage) + r * coverage) for c, r in zip(color, ring))
    return color


def png(size: int) -> bytes:
    rows = b"".join(
        b"\x00" + bytes(v for x in range(size) for v in pixel(x + 0.5, y + 0.5, size))
        for y in range(size)
    )

    def chunk(kind: bytes, data: bytes) -> bytes:
        return struct.pack(">I", len(data)) + kind + data + struct.pack(">I", zlib.crc32(kind + data))

    header = struct.pack(">IIBBBBB", size, size, 8, 2, 0, 0, 0)  # 8-bit RGB, no alpha
    return b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", header) + chunk(b"IDAT", zlib.compress(rows, 9)) + chunk(b"IEND", b"")


def svg() -> str:
    hexes = {c: "#%02X%02X%02X" % c for c in (ACCENT, WHITE)}
    circles = "".join(f'<circle cx="50" cy="50" r="{frac * 100:g}" fill="{hexes[c]}"/>' for frac, c in RINGS)
    return (f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 100 100">'
            f'<rect width="100" height="100" rx="22" fill="{hexes[ACCENT]}"/>{circles}</svg>\n')


def main() -> None:
    OUT.mkdir(parents=True, exist_ok=True)
    # apple-touch-icon is opaque on purpose: iOS fills transparency with black.
    for name, size in [("icon-192.png", 192), ("icon-512.png", 512), ("apple-touch-icon.png", 180)]:
        (OUT / name).write_bytes(png(size))
        print(f"  web/icons/{name}")
    (OUT / "icon.svg").write_text(svg())
    print("  web/icons/icon.svg")


if __name__ == "__main__":
    main()
