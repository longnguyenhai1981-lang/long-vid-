from __future__ import annotations

from uuid import uuid4

import pytest

from app.models.common import VisualOutputFormat
from app.models.ti_assets import TiAsset, TiAssetType, TiState
from app.ti_assets.errors import TiAssetStateNotFoundError
from app.ti_assets.lookup import get_asset_by_state, index_assets_by_state


def _asset(state: TiState) -> TiAsset:
    asset_set_id = uuid4()
    return TiAsset(
        asset_set_id=asset_set_id,
        state=state,
        asset_type=TiAssetType.STILL,
        relative_path=f"{asset_set_id}/v1/{state.value}.png",
        output_format=VisualOutputFormat.PNG,
        mime_type="image/png",
        width=512,
        height=512,
        transparent_background=True,
    )


def test_index_assets_by_state_deterministic():
    assets = [_asset(TiState.NEUTRAL), _asset(TiState.PANIC)]
    index = index_assets_by_state(assets)
    assert index[TiState.NEUTRAL].state is TiState.NEUTRAL
    assert index[TiState.PANIC].state is TiState.PANIC
    assert len(index) == 2


def test_index_assets_by_state_rejects_duplicates():
    assets = [_asset(TiState.NEUTRAL), _asset(TiState.NEUTRAL)]
    with pytest.raises(ValueError, match="duplicate"):
        index_assets_by_state(assets)


def test_get_asset_by_state_returns_exact_match():
    target = _asset(TiState.EXCITED)
    assets = [_asset(TiState.NEUTRAL), target]
    found = get_asset_by_state(assets, TiState.EXCITED)
    assert found is target


def test_get_asset_by_state_missing_raises_clearly():
    assets = [_asset(TiState.NEUTRAL)]
    with pytest.raises(TiAssetStateNotFoundError, match="SERIOUS"):
        get_asset_by_state(assets, TiState.SERIOUS)


def test_get_asset_by_state_never_returns_none_or_fallback():
    assets: list[TiAsset] = []
    with pytest.raises(TiAssetStateNotFoundError):
        get_asset_by_state(assets, TiState.NEUTRAL)
