from __future__ import annotations

from datetime import datetime, timezone
from uuid import uuid4

import pytest

from app.models.common import VisualOutputFormat
from app.models.ti_assets import TiAsset, TiAssetSet, TiAssetType, TiState
from app.ti_assets.errors import TiAssetMissingFileError
from app.ti_assets.storage import TiAssetFileStore, build_relative_path
from app.ti_assets.validation import validate_files_exist


def _full_asset_set(asset_set_id) -> TiAssetSet:
    assets = [
        TiAsset(
            asset_set_id=asset_set_id,
            state=state,
            asset_type=TiAssetType.STILL,
            relative_path=build_relative_path(asset_set_id, "v1", state, VisualOutputFormat.PNG),
            output_format=VisualOutputFormat.PNG,
            mime_type="image/png",
            width=1024,
            height=1024,
            transparent_background=True,
        )
        for state in TiState
    ]
    return TiAssetSet(id=asset_set_id, version="v1", assets=assets, created_at=datetime.now(timezone.utc))


def test_validate_files_exist_passes_when_all_files_present(tmp_path):
    asset_set_id = uuid4()
    asset_set = _full_asset_set(asset_set_id)
    file_store = TiAssetFileStore(tmp_path)
    for asset in asset_set.assets:
        file_store.write(asset.relative_path, b"data")

    validate_files_exist(asset_set, file_store)  # must not raise


def test_validate_files_exist_raises_naming_missing_states(tmp_path):
    asset_set_id = uuid4()
    asset_set = _full_asset_set(asset_set_id)
    file_store = TiAssetFileStore(tmp_path)
    for asset in asset_set.assets:
        if asset.state is TiState.NEUTRAL:
            continue
        file_store.write(asset.relative_path, b"data")

    with pytest.raises(TiAssetMissingFileError, match="NEUTRAL"):
        validate_files_exist(asset_set, file_store)


def test_validate_files_exist_reports_all_missing_states(tmp_path):
    asset_set_id = uuid4()
    asset_set = _full_asset_set(asset_set_id)
    file_store = TiAssetFileStore(tmp_path)  # nothing written

    with pytest.raises(TiAssetMissingFileError) as exc_info:
        validate_files_exist(asset_set, file_store)

    message = str(exc_info.value)
    for state in TiState:
        assert state.value in message
