"""Deterministic canonical Tí asset retrieval -- the clean future
integration boundary.

TiAssetRetriever is the Protocol a future VisualRenderer/compositor
integration should depend on, exactly the way app/visual/provider.py's
VisualProvider decouples VisualRenderer from any concrete provider. Phase 21
deliberately stops at defining and implementing this boundary: nothing here
is wired into app/renderers/visual/renderer.py, app/visual/router.py, or any
VisualBeat/VisualMediaType.TI_STATE handling. That wiring -- almost
certainly resolving a beat's existing "ti_state:<VisualTiState value>"
REUSE_ONLY reference (see app/renderers/visual/renderer.py's
_reuse_only_requirement) into a get_asset(...) call -- is left to a future
phase.

Every lookup here is exact-match and fails loudly: no fuzzy state matching,
no LLM-based selection, and no silent fallback to placeholder or
AI-generated mascot imagery.
"""

from __future__ import annotations

from pathlib import Path
from typing import Protocol, runtime_checkable

from sqlalchemy import Engine

from app.models.ti_assets import TiAsset, TiAssetSet, TiState
from app.storage.ti_assets import get_active_ti_asset_set
from app.ti_assets.errors import TiAssetMissingFileError
from app.ti_assets.lookup import get_asset_by_state
from app.ti_assets.storage import TiAssetFileStore


@runtime_checkable
class TiAssetRetriever(Protocol):
    def get_active_asset_set(self) -> TiAssetSet:
        ...

    def get_asset(self, state: TiState) -> TiAsset:
        ...

    def list_available_states(self) -> list[TiState]:
        ...

    def resolve_path(self, asset: TiAsset) -> Path:
        ...


class SqliteTiAssetRetriever:
    """The one concrete TiAssetRetriever Phase 21 ships: active-set metadata
    from SQLite (app/storage/ti_assets.py), files from a TiAssetFileStore."""

    def __init__(self, engine: Engine, file_store: TiAssetFileStore):
        self._engine = engine
        self._file_store = file_store

    def get_active_asset_set(self) -> TiAssetSet:
        return get_active_ti_asset_set(self._engine)

    def get_asset(self, state: TiState) -> TiAsset:
        """Look up the active set's asset for state, then confirm its file
        actually exists on disk -- catches metadata/filesystem drift rather
        than handing back a reference to nothing."""
        active_set = self.get_active_asset_set()
        asset = get_asset_by_state(active_set.assets, state)
        if not self._file_store.exists(asset.relative_path):
            raise TiAssetMissingFileError(
                f"Canonical Tí asset for state {state.value} is registered at "
                f"{asset.relative_path!r} but no file exists there"
            )
        return asset

    def list_available_states(self) -> list[TiState]:
        active_set = self.get_active_asset_set()
        return sorted((asset.state for asset in active_set.assets), key=lambda s: s.value)

    def resolve_path(self, asset: TiAsset) -> Path:
        return self._file_store.resolve(asset.relative_path)
