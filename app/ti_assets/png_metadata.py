"""Minimal, dependency-free PNG header inspection for canonical-asset
ingestion validation (Phase 21.1).

Pillow is not currently a project dependency (confirmed absent from both
the installed environment and pyproject.toml before writing this module).
Adding it was evaluated and rejected as unjustified for this need: Phase
21.1 only has to confirm a file is a valid PNG, read its width/height, and
determine whether it carries an alpha channel or tRNS-based transparency --
all of which live in the first few chunks of the file (signature + IHDR,
optionally tRNS) and need zero pixel-data decoding. Python's stdlib
(`struct` for binary unpacking) is sufficient and is the smaller
dependency footprint of the two options, consistent with this project's
existing preference (see CloudflareImageProvider reusing httpx directly
rather than adding a vendor SDK). This is deliberately NOT a general-
purpose PNG/image library -- it never decodes IDAT pixel data and would be
the wrong tool for anything beyond this narrow validation need.
"""

from __future__ import annotations

import struct
from dataclasses import dataclass

from app.ti_assets.errors import TiAssetInvalidImageError

_PNG_SIGNATURE = b"\x89PNG\r\n\x1a\n"

# Color types (PNG spec) with an inherent alpha channel.
_ALPHA_COLOR_TYPES = frozenset({4, 6})
# Color types that may carry transparency only via a separate tRNS chunk.
_TRNS_CAPABLE_COLOR_TYPES = frozenset({0, 2, 3})


@dataclass(frozen=True)
class PngMetadata:
    width: int
    height: int
    color_type: int
    bit_depth: int
    has_alpha: bool


def read_png_metadata(data: bytes) -> PngMetadata:
    """Parse just enough of a PNG file's chunk structure (signature, IHDR,
    presence of tRNS) to answer this phase's validation questions. Raises
    TiAssetInvalidImageError for anything that isn't a well-formed PNG,
    rather than guessing or returning partial data."""
    if not data.startswith(_PNG_SIGNATURE):
        raise TiAssetInvalidImageError("not a valid PNG file (bad signature)")

    offset = len(_PNG_SIGNATURE)
    width: int | None = None
    height: int | None = None
    bit_depth: int | None = None
    color_type: int | None = None
    has_trns = False

    while offset < len(data):
        if offset + 8 > len(data):
            raise TiAssetInvalidImageError("truncated PNG: incomplete chunk header")

        length, raw_type = struct.unpack_from(">I4s", data, offset)
        chunk_type = raw_type.decode("ascii", errors="replace")
        chunk_data_start = offset + 8
        chunk_data_end = chunk_data_start + length

        if length < 0 or chunk_data_end + 4 > len(data):
            raise TiAssetInvalidImageError(f"truncated PNG: incomplete {chunk_type!r} chunk")

        if chunk_type == "IHDR":
            if length != 13:
                raise TiAssetInvalidImageError("malformed PNG: IHDR chunk has wrong length")
            width, height, bit_depth, color_type, _compression, _filter_method, _interlace = (
                struct.unpack_from(">IIBBBBB", data, chunk_data_start)
            )
        elif chunk_type == "tRNS":
            has_trns = True
        elif chunk_type == "IEND":
            break

        offset = chunk_data_end + 4  # skip the 4-byte CRC

    if width is None or color_type is None or bit_depth is None:
        raise TiAssetInvalidImageError("malformed PNG: no IHDR chunk found")
    if width <= 0 or height <= 0:
        raise TiAssetInvalidImageError(f"malformed PNG: non-positive dimensions ({width}x{height})")

    has_alpha = color_type in _ALPHA_COLOR_TYPES or (
        color_type in _TRNS_CAPABLE_COLOR_TYPES and has_trns
    )

    return PngMetadata(
        width=width,
        height=height,
        color_type=color_type,
        bit_depth=bit_depth,
        has_alpha=has_alpha,
    )
