from __future__ import annotations

from datetime import datetime, timezone
from uuid import uuid4

import pytest
from pydantic import ValidationError

from app.models.common import VisualOutputFormat
from app.models.ti_assets import REQUIRED_TI_STATES, TiAsset, TiAssetSet, TiAssetType, TiState


def _asset(asset_set_id, state, **overrides) -> TiAsset:
    fields = dict(
        asset_set_id=asset_set_id,
        state=state,
        asset_type=TiAssetType.STILL,
        relative_path=f"{asset_set_id}/v1/{state.value}.png",
        output_format=VisualOutputFormat.PNG,
        mime_type="image/png",
        width=1024,
        height=1024,
        transparent_background=True,
    )
    fields.update(overrides)
    return TiAsset(**fields)


def _full_asset_list(asset_set_id) -> list[TiAsset]:
    return [_asset(asset_set_id, state) for state in TiState]


def test_required_states_matches_all_ti_state_members():
    assert REQUIRED_TI_STATES == frozenset(TiState)


def test_valid_asset_constructs():
    asset_set_id = uuid4()
    asset = _asset(asset_set_id, TiState.NEUTRAL)
    assert asset.state is TiState.NEUTRAL
    assert asset.asset_type is TiAssetType.STILL


def test_asset_rejects_non_positive_width():
    with pytest.raises(ValidationError):
        _asset(uuid4(), TiState.NEUTRAL, width=0)


def test_asset_rejects_non_positive_height():
    with pytest.raises(ValidationError):
        _asset(uuid4(), TiState.NEUTRAL, height=-1)


def test_asset_rejects_blank_relative_path():
    with pytest.raises(ValidationError):
        _asset(uuid4(), TiState.NEUTRAL, relative_path="   ")


def test_asset_rejects_mismatched_mime_type():
    with pytest.raises(ValidationError):
        _asset(uuid4(), TiState.NEUTRAL, mime_type="image/jpeg")


def test_asset_rejects_extension_not_matching_output_format():
    asset_set_id = uuid4()
    with pytest.raises(ValidationError):
        _asset(
            asset_set_id,
            TiState.NEUTRAL,
            relative_path=f"{asset_set_id}/v1/NEUTRAL.jpg",
        )


def test_asset_rejects_transparent_background_with_jpg():
    asset_set_id = uuid4()
    with pytest.raises(ValidationError):
        _asset(
            asset_set_id,
            TiState.NEUTRAL,
            output_format=VisualOutputFormat.JPG,
            mime_type="image/jpeg",
            relative_path=f"{asset_set_id}/v1/NEUTRAL.jpg",
            transparent_background=True,
        )


def test_jpg_asset_without_transparency_is_valid():
    asset_set_id = uuid4()
    asset = _asset(
        asset_set_id,
        TiState.NEUTRAL,
        output_format=VisualOutputFormat.JPG,
        mime_type="image/jpeg",
        relative_path=f"{asset_set_id}/v1/NEUTRAL.jpg",
        transparent_background=False,
    )
    assert asset.output_format is VisualOutputFormat.JPG


def test_asset_set_with_all_required_states_is_valid():
    asset_set_id = uuid4()
    asset_set = TiAssetSet(
        id=asset_set_id,
        version="v1",
        is_active=True,
        assets=_full_asset_list(asset_set_id),
        created_at=datetime.now(timezone.utc),
    )
    assert len(asset_set.assets) == len(TiState)
    assert asset_set.is_active is True


def test_asset_set_rejects_missing_states():
    asset_set_id = uuid4()
    incomplete = _full_asset_list(asset_set_id)[:-1]
    with pytest.raises(ValidationError, match="incomplete"):
        TiAssetSet(
            id=asset_set_id,
            version="v1",
            assets=incomplete,
            created_at=datetime.now(timezone.utc),
        )


def test_asset_set_rejects_duplicate_state():
    asset_set_id = uuid4()
    assets = _full_asset_list(asset_set_id)
    assets.append(_asset(asset_set_id, TiState.NEUTRAL, relative_path=f"{asset_set_id}/v1/NEUTRAL_2.png"))
    with pytest.raises(ValidationError, match="duplicate"):
        TiAssetSet(
            id=asset_set_id,
            version="v1",
            assets=assets,
            created_at=datetime.now(timezone.utc),
        )


def test_asset_set_rejects_empty_assets():
    with pytest.raises(ValidationError):
        TiAssetSet(version="v1", assets=[], created_at=datetime.now(timezone.utc))


def test_asset_set_rejects_naive_datetime():
    asset_set_id = uuid4()
    with pytest.raises(ValidationError, match="timezone-aware"):
        TiAssetSet(
            id=asset_set_id,
            version="v1",
            assets=_full_asset_list(asset_set_id),
            created_at=datetime.now(),
        )


def test_asset_set_rejects_blank_version():
    asset_set_id = uuid4()
    with pytest.raises(ValidationError):
        TiAssetSet(
            id=asset_set_id,
            version="   ",
            assets=_full_asset_list(asset_set_id),
            created_at=datetime.now(timezone.utc),
        )


def test_asset_set_rejects_asset_with_mismatched_asset_set_id():
    asset_set_id = uuid4()
    other_id = uuid4()
    assets = _full_asset_list(asset_set_id)
    assets[0] = _asset(other_id, assets[0].state)
    with pytest.raises(ValidationError):
        TiAssetSet(
            id=asset_set_id,
            version="v1",
            assets=assets,
            created_at=datetime.now(timezone.utc),
        )


def test_ti_asset_has_no_bytes_field():
    """No raw image bytes may ever be a TiAsset field -- structural
    confirmation that the contract can never be persisted with embedded
    binary content."""
    for name, field in TiAsset.model_fields.items():
        assert field.annotation is not bytes, f"TiAsset.{name} must not be bytes"
