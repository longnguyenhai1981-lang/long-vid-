"""Synthetic image fixtures for tests/test_ti_compositor.py -- deliberately
NOT collected by pytest (no test_ prefix). Uses Pillow directly (unlike
tests/_png_helpers.py's dependency-free byte-level builder) because Phase
22's compositor tests need real, pixel-decodable images: a solid-color
background and a Tí asset with a genuine transparent border around an
opaque colored core, so alpha compositing can actually be exercised and
asserted on.
"""

from __future__ import annotations

from datetime import datetime, timezone
from io import BytesIO
from pathlib import Path
from uuid import uuid4

from PIL import Image

from app.models.common import VisualOutputFormat
from app.models.ti_assets import TiAsset, TiAssetSet, TiAssetType, TiState
from app.storage.ti_assets import save_ti_asset_set
from app.ti_assets.storage import TiAssetFileStore, build_relative_path

TI_OPAQUE_COLOR = (0, 200, 0, 255)  # opaque green core
TI_ASSET_SIZE = (40, 60)  # width, height


def make_background_bytes(width: int, height: int, color: tuple[int, int, int], fmt: str) -> bytes:
    image = Image.new("RGB", (width, height), color)
    buffer = BytesIO()
    image.save(buffer, format=fmt)
    return buffer.getvalue()


def write_background(path: Path, width: int, height: int, color: tuple[int, int, int], fmt: str) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(make_background_bytes(width, height, color, fmt))
    return path


def make_ti_asset_png_bytes(size: tuple[int, int] = TI_ASSET_SIZE) -> bytes:
    """A transparent-bordered opaque colored rectangle: the outer quarter on
    each side is fully transparent (alpha=0), the inner core is fully
    opaque TI_OPAQUE_COLOR. Lets a test assert both that the opaque core
    fully overwrites the background and that the transparent border lets
    the original background color show through untouched -- i.e. no
    white/black is baked in around Tí.
    """
    width, height = size
    image = Image.new("RGBA", (width, height), (0, 0, 0, 0))
    inset_x, inset_y = width // 4, height // 4
    core = Image.new("RGBA", (width - 2 * inset_x, height - 2 * inset_y), TI_OPAQUE_COLOR)
    image.paste(core, (inset_x, inset_y))
    buffer = BytesIO()
    image.save(buffer, format="PNG")
    return buffer.getvalue()


def write_full_ti_asset_set(engine, file_store: TiAssetFileStore, is_active: bool = True) -> TiAssetSet:
    """Register one complete, active TiAssetSet -- every TiState pointing
    at the same synthetic transparent-bordered PNG (Phase 22 tests only
    care about alpha-compositing/placement correctness, not distinguishing
    states visually)."""
    asset_set_id = uuid4()
    asset_bytes = make_ti_asset_png_bytes()
    width, height = TI_ASSET_SIZE

    assets = []
    for state in TiState:
        relative_path = build_relative_path(asset_set_id, "v1", state, VisualOutputFormat.PNG)
        file_store.write(relative_path, asset_bytes)
        assets.append(
            TiAsset(
                asset_set_id=asset_set_id,
                state=state,
                asset_type=TiAssetType.STILL,
                relative_path=relative_path,
                output_format=VisualOutputFormat.PNG,
                mime_type="image/png",
                width=width,
                height=height,
                transparent_background=True,
            )
        )

    asset_set = TiAssetSet(
        id=asset_set_id,
        version="v1",
        is_active=is_active,
        assets=assets,
        created_at=datetime.now(timezone.utc),
    )
    save_ti_asset_set(engine, asset_set)
    return asset_set
