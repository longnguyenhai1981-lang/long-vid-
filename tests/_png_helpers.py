"""Minimal, dependency-free synthetic PNG builder for tests -- deliberately
NOT collected by pytest (no test_ prefix). Used by
tests/test_ti_asset_png_metadata.py, tests/test_ti_asset_ingest.py, and
tests/test_ingest_ti_assets_cli.py so those tests never depend on Pillow or
any real asset file. Only builds what app/ti_assets/png_metadata.py needs
to see: a valid signature, an IHDR chunk, an optional tRNS chunk, and a
structurally valid (if visually meaningless) IDAT/IEND pair.
"""

from __future__ import annotations

import struct
import zlib

_CHANNELS_BY_COLOR_TYPE = {0: 1, 2: 3, 3: 1, 4: 2, 6: 4}


def _chunk(chunk_type: bytes, data: bytes) -> bytes:
    return (
        struct.pack(">I", len(data))
        + chunk_type
        + data
        + struct.pack(">I", zlib.crc32(chunk_type + data) & 0xFFFFFFFF)
    )


def make_png_bytes(
    width: int = 4,
    height: int = 4,
    color_type: int = 6,
    bit_depth: int = 8,
    include_trns: bool = False,
) -> bytes:
    """Build a small, structurally valid PNG. color_type 6 (RGBA) and 4
    (grayscale+alpha) have an inherent alpha channel; color_type 0
    (grayscale) and 2 (RGB) only gain transparency if include_trns=True."""
    signature = b"\x89PNG\r\n\x1a\n"

    ihdr = struct.pack(">IIBBBBB", width, height, bit_depth, color_type, 0, 0, 0)
    chunks = [_chunk(b"IHDR", ihdr)]

    if color_type == 3:
        chunks.append(_chunk(b"PLTE", b"\x00\x00\x00"))  # one black palette entry

    if include_trns:
        trns_by_type = {
            0: struct.pack(">H", 0),
            2: struct.pack(">HHH", 0, 0, 0),
            3: b"\x00",
        }
        chunks.append(_chunk(b"tRNS", trns_by_type.get(color_type, b"")))

    channels = _CHANNELS_BY_COLOR_TYPE[color_type]
    bytes_per_pixel = max(1, (bit_depth * channels) // 8)
    row = b"\x00" + (b"\x00" * bytes_per_pixel * width)  # filter byte 0 + zeroed pixels
    raw = row * height
    chunks.append(_chunk(b"IDAT", zlib.compress(raw)))
    chunks.append(_chunk(b"IEND", b""))

    return signature + b"".join(chunks)


def make_truncated_png_bytes() -> bytes:
    """A PNG signature followed by an incomplete chunk header -- not a
    valid PNG."""
    return b"\x89PNG\r\n\x1a\n" + b"\x00\x00"


def make_garbage_bytes() -> bytes:
    return b"this is not a png file at all"
