"""Deterministic, exact-match TiState -> TiAsset lookup.

No fuzzy matching, no LLM-based selection, no silent fallback -- an unknown
or missing state always raises rather than returning a default/placeholder
asset. Kept independent of TiAssetSet's own construction-time completeness
guarantee so the "missing state" failure mode stays directly testable rather
than provably unreachable.
"""

from __future__ import annotations

from app.models.ti_assets import TiAsset, TiState
from app.ti_assets.errors import TiAssetStateNotFoundError


def index_assets_by_state(assets: list[TiAsset]) -> dict[TiState, TiAsset]:
    """Build an exact TiState -> TiAsset index. Raises ValueError if two
    assets claim the same state -- the same rule TiAssetSet enforces at
    construction time, re-checked here defensively."""
    index: dict[TiState, TiAsset] = {}
    for asset in assets:
        if asset.state in index:
            raise ValueError(f"duplicate TiState in asset list: {asset.state.value}")
        index[asset.state] = asset
    return index


def get_asset_by_state(assets: list[TiAsset], state: TiState) -> TiAsset:
    """Look up the one TiAsset for state. Raises TiAssetStateNotFoundError,
    never None, if it is absent."""
    index = index_assets_by_state(assets)
    asset = index.get(state)
    if asset is None:
        raise TiAssetStateNotFoundError(f"No canonical Tí asset registered for state {state.value}")
    return asset
