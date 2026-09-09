"""I/O-requiring Tí-asset validation.

Duplicate-state and set-completeness are enforced structurally, inside
TiAssetSet's own Pydantic validator (app/models/ti_assets.py) -- they need
no filesystem access, so they live there, not here. This module holds only
the one validation rule that genuinely requires touching disk: confirming
that every asset a TiAssetSet claims to have actually exists as a file.
"""

from __future__ import annotations

from app.models.ti_assets import TiAssetSet
from app.ti_assets.errors import TiAssetMissingFileError
from app.ti_assets.storage import TiAssetFileStore


def validate_files_exist(asset_set: TiAssetSet, file_store: TiAssetFileStore) -> None:
    """Raise TiAssetMissingFileError, naming every missing state, if any
    asset's relative_path has no corresponding file under file_store's
    root."""
    missing: list[str] = []
    for asset in asset_set.assets:
        if not file_store.exists(asset.relative_path):
            missing.append(f"{asset.state.value} ({asset.relative_path})")

    if missing:
        raise TiAssetMissingFileError(
            f"TiAssetSet {asset_set.id} version {asset_set.version!r} is missing "
            f"file(s) on disk for: " + ", ".join(sorted(missing))
        )
