from __future__ import annotations

import pytest

from app.ti_assets.errors import TiAssetInvalidImageError
from app.ti_assets.png_metadata import read_png_metadata
from tests._png_helpers import make_garbage_bytes, make_png_bytes, make_truncated_png_bytes


def test_reads_width_and_height():
    data = make_png_bytes(width=7, height=3, color_type=6)
    metadata = read_png_metadata(data)
    assert metadata.width == 7
    assert metadata.height == 3


def test_rgba_color_type_has_alpha():
    metadata = read_png_metadata(make_png_bytes(color_type=6))
    assert metadata.has_alpha is True


def test_grayscale_alpha_color_type_has_alpha():
    metadata = read_png_metadata(make_png_bytes(color_type=4))
    assert metadata.has_alpha is True


def test_rgb_without_trns_has_no_alpha():
    metadata = read_png_metadata(make_png_bytes(color_type=2, include_trns=False))
    assert metadata.has_alpha is False


def test_rgb_with_trns_has_alpha():
    metadata = read_png_metadata(make_png_bytes(color_type=2, include_trns=True))
    assert metadata.has_alpha is True


def test_grayscale_without_trns_has_no_alpha():
    metadata = read_png_metadata(make_png_bytes(color_type=0, include_trns=False))
    assert metadata.has_alpha is False


def test_grayscale_with_trns_has_alpha():
    metadata = read_png_metadata(make_png_bytes(color_type=0, include_trns=True))
    assert metadata.has_alpha is True


def test_garbage_bytes_rejected():
    with pytest.raises(TiAssetInvalidImageError, match="signature"):
        read_png_metadata(make_garbage_bytes())


def test_truncated_bytes_rejected():
    with pytest.raises(TiAssetInvalidImageError):
        read_png_metadata(make_truncated_png_bytes())


def test_empty_bytes_rejected():
    with pytest.raises(TiAssetInvalidImageError):
        read_png_metadata(b"")


def test_zero_width_rejected():
    with pytest.raises(TiAssetInvalidImageError, match="non-positive"):
        read_png_metadata(make_png_bytes(width=0, height=4, color_type=6))


def test_zero_height_rejected():
    with pytest.raises(TiAssetInvalidImageError, match="non-positive"):
        read_png_metadata(make_png_bytes(width=4, height=0, color_type=6))


def test_missing_ihdr_rejected():
    # Valid signature, then straight to IEND -- no IHDR at all.
    import struct
    import zlib

    def chunk(t: bytes, d: bytes) -> bytes:
        return struct.pack(">I", len(d)) + t + d + struct.pack(">I", zlib.crc32(t + d) & 0xFFFFFFFF)

    data = b"\x89PNG\r\n\x1a\n" + chunk(b"IEND", b"")
    with pytest.raises(TiAssetInvalidImageError, match="IHDR"):
        read_png_metadata(data)
