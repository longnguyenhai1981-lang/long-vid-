from __future__ import annotations

from datetime import datetime, timezone
from uuid import uuid4

import pytest

from app.models.common import VisualOutputFormat
from app.models.ti_assets import TiAsset, TiAssetSet, TiAssetType, TiState
from app.storage.errors import TiAssetSetNotFoundError
from app.storage.ti_assets import save_ti_asset_set
from app.ti_assets.errors import TiAssetMissingFileError
from app.ti_assets.retriever import SqliteTiAssetRetriever, TiAssetRetriever
from app.ti_assets.storage import TiAssetFileStore, build_relative_path


def _write_full_asset_set(engine, file_store, is_active=True) -> TiAssetSet:
    asset_set_id = uuid4()
    assets = []
    for state in TiState:
        relative_path = build_relative_path(asset_set_id, "v1", state, VisualOutputFormat.PNG)
        file_store.write(relative_path, f"bytes-for-{state.value}".encode())
        assets.append(
            TiAsset(
                asset_set_id=asset_set_id,
                state=state,
                asset_type=TiAssetType.STILL,
                relative_path=relative_path,
                output_format=VisualOutputFormat.PNG,
                mime_type="image/png",
                width=1024,
                height=1024,
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


def test_sqlite_ti_asset_retriever_satisfies_protocol(tmp_path, engine):
    retriever = SqliteTiAssetRetriever(engine, TiAssetFileStore(tmp_path))
    assert isinstance(retriever, TiAssetRetriever)


def test_get_active_asset_set_round_trips(tmp_path, engine):
    file_store = TiAssetFileStore(tmp_path)
    asset_set = _write_full_asset_set(engine, file_store)
    retriever = SqliteTiAssetRetriever(engine, file_store)
    assert retriever.get_active_asset_set() == asset_set


def test_get_asset_returns_exact_state_match(tmp_path, engine):
    file_store = TiAssetFileStore(tmp_path)
    _write_full_asset_set(engine, file_store)
    retriever = SqliteTiAssetRetriever(engine, file_store)
    asset = retriever.get_asset(TiState.PANIC)
    assert asset.state is TiState.PANIC


def test_list_available_states_is_deterministic_and_complete(tmp_path, engine):
    file_store = TiAssetFileStore(tmp_path)
    _write_full_asset_set(engine, file_store)
    retriever = SqliteTiAssetRetriever(engine, file_store)
    states = retriever.list_available_states()
    assert states == sorted(TiState, key=lambda s: s.value)


def test_resolve_path_returns_absolute_existing_path(tmp_path, engine):
    file_store = TiAssetFileStore(tmp_path)
    _write_full_asset_set(engine, file_store)
    retriever = SqliteTiAssetRetriever(engine, file_store)
    asset = retriever.get_asset(TiState.NEUTRAL)
    resolved = retriever.resolve_path(asset)
    assert resolved.is_absolute()
    assert resolved.is_file()


def test_get_asset_fails_clearly_when_no_active_set(tmp_path, engine):
    retriever = SqliteTiAssetRetriever(engine, TiAssetFileStore(tmp_path))
    with pytest.raises(TiAssetSetNotFoundError):
        retriever.get_asset(TiState.NEUTRAL)


def test_get_asset_fails_clearly_when_file_missing_on_disk(tmp_path, engine):
    file_store = TiAssetFileStore(tmp_path)
    asset_set = _write_full_asset_set(engine, file_store)
    neutral_asset = next(a for a in asset_set.assets if a.state is TiState.NEUTRAL)
    file_store.resolve(neutral_asset.relative_path).unlink()

    retriever = SqliteTiAssetRetriever(engine, file_store)
    with pytest.raises(TiAssetMissingFileError):
        retriever.get_asset(TiState.NEUTRAL)


def test_get_asset_never_falls_back_to_a_different_state(tmp_path, engine):
    file_store = TiAssetFileStore(tmp_path)
    _write_full_asset_set(engine, file_store)
    retriever = SqliteTiAssetRetriever(engine, file_store)
    asset = retriever.get_asset(TiState.SERIOUS)
    assert asset.state is TiState.SERIOUS
