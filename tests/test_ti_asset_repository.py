from __future__ import annotations

from datetime import datetime, timezone
from uuid import uuid4

import pytest
from sqlalchemy import select

from app.models.common import VisualOutputFormat
from app.models.ti_assets import TiAsset, TiAssetSet, TiAssetType, TiState
from app.storage.errors import TiAssetSetNotFoundError, TiAssetSetValidationError
from app.storage.orm import TiAssetSetRow
from app.storage.ti_assets import (
    activate_ti_asset_set,
    get_active_ti_asset_set,
    get_ti_asset_set,
    list_ti_asset_sets,
    save_ti_asset_set,
)


def _asset(asset_set_id, state) -> TiAsset:
    return TiAsset(
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


def _asset_set(asset_set_id=None, version="v1", is_active=False) -> TiAssetSet:
    asset_set_id = asset_set_id or uuid4()
    return TiAssetSet(
        id=asset_set_id,
        version=version,
        is_active=is_active,
        assets=[_asset(asset_set_id, state) for state in TiState],
        created_at=datetime.now(timezone.utc),
    )


def test_save_and_get_round_trip(engine):
    asset_set = _asset_set()
    save_ti_asset_set(engine, asset_set)
    restored = get_ti_asset_set(engine, asset_set.id, asset_set.version)
    assert restored == asset_set


def test_get_missing_asset_set_raises(engine):
    with pytest.raises(TiAssetSetNotFoundError):
        get_ti_asset_set(engine, uuid4(), "v1")


def test_save_upserts_same_key(engine):
    asset_set_id = uuid4()
    save_ti_asset_set(engine, _asset_set(asset_set_id, "v1", is_active=False))
    updated = _asset_set(asset_set_id, "v1", is_active=False)
    save_ti_asset_set(engine, updated)
    restored = get_ti_asset_set(engine, asset_set_id, "v1")
    assert restored == updated

    with engine.connect() as conn:
        count = conn.execute(select(TiAssetSetRow)).fetchall()
    assert len(count) == 1


def test_distinct_versions_are_separate_rows(engine):
    asset_set_id = uuid4()
    save_ti_asset_set(engine, _asset_set(asset_set_id, "v1"))
    save_ti_asset_set(engine, _asset_set(asset_set_id, "v2"))
    assert len(list_ti_asset_sets(engine)) == 2


def test_activating_one_version_deactivates_all_others(engine):
    asset_set_id = uuid4()
    save_ti_asset_set(engine, _asset_set(asset_set_id, "v1", is_active=True))
    save_ti_asset_set(engine, _asset_set(asset_set_id, "v2", is_active=False))

    activate_ti_asset_set(engine, asset_set_id, "v2")

    v1 = get_ti_asset_set(engine, asset_set_id, "v1")
    v2 = get_ti_asset_set(engine, asset_set_id, "v2")
    assert v1.is_active is False
    assert v2.is_active is True


def test_only_one_active_set_across_different_lineages(engine):
    first = _asset_set(version="v1", is_active=True)
    second = _asset_set(version="v1", is_active=True)
    save_ti_asset_set(engine, first)
    save_ti_asset_set(engine, second)

    active = get_active_ti_asset_set(engine)
    assert active.id == second.id

    with engine.connect() as conn:
        active_rows = conn.execute(
            select(TiAssetSetRow).where(TiAssetSetRow.is_active.is_(True))
        ).fetchall()
    assert len(active_rows) == 1


def test_get_active_raises_when_none_active(engine):
    save_ti_asset_set(engine, _asset_set(is_active=False))
    with pytest.raises(TiAssetSetNotFoundError):
        get_active_ti_asset_set(engine)


def test_activate_missing_asset_set_raises(engine):
    with pytest.raises(TiAssetSetNotFoundError):
        activate_ti_asset_set(engine, uuid4(), "v1")


def test_corrupt_payload_fails_cleanly(engine):
    from sqlalchemy.orm import Session

    asset_set_id = uuid4()
    with Session(engine) as session, session.begin():
        session.add(
            TiAssetSetRow(
                asset_set_id=str(asset_set_id),
                version="v1",
                is_active=False,
                payload_json="{not valid json",
                created_at=datetime.now(timezone.utc),
            )
        )
    with pytest.raises(TiAssetSetValidationError):
        get_ti_asset_set(engine, asset_set_id, "v1")


def test_no_raw_bytes_stored_in_payload(engine):
    asset_set = _asset_set(is_active=True)
    save_ti_asset_set(engine, asset_set)
    with engine.connect() as conn:
        row = conn.execute(
            select(TiAssetSetRow.payload_json).where(
                TiAssetSetRow.asset_set_id == str(asset_set.id)
            )
        ).scalar_one()
    assert "asset_bytes" not in row
    for state in TiState:
        assert f"{state.value}.png" in row
