"""Canonical Tí asset set persistence: validated TiAssetSet JSON payloads,
one row per (asset_set_id, version). Mirrors app/storage/artifacts.py's
validate-and-store-as-JSON pattern, plus the single-active-row invariant
Phase 21 requires: activating one version always deactivates every other
row first, in the same transaction, so at most one TiAssetSetRow can ever
have is_active=True.
"""

from __future__ import annotations

from uuid import UUID

from pydantic import ValidationError
from sqlalchemy import Engine, select, update
from sqlalchemy.orm import Session

from app.models.ti_assets import TiAssetSet
from app.storage.errors import (
    MultipleActiveTiAssetSetsError,
    TiAssetSetNotFoundError,
    TiAssetSetValidationError,
)
from app.storage.orm import TiAssetSetRow


def _to_domain(row: TiAssetSetRow) -> TiAssetSet:
    """Reconstruct a TiAssetSet from its stored JSON payload.

    row.is_active -- not whatever is_active happens to be embedded in the
    JSON -- is authoritative: activate_ti_asset_set() flips the column
    directly (so the single-active-row invariant can be enforced with one
    UPDATE across every row) without rewriting every other row's JSON blob,
    so the two would otherwise drift apart.
    """
    try:
        asset_set = TiAssetSet.model_validate_json(row.payload_json)
    except ValidationError as exc:
        raise TiAssetSetValidationError(
            f"Stored TiAssetSet payload for ({row.asset_set_id}, {row.version}) "
            f"does not validate as TiAssetSet"
        ) from exc
    return asset_set.model_copy(update={"is_active": row.is_active})


def save_ti_asset_set(engine: Engine, asset_set: TiAssetSet) -> None:
    """Insert-or-update the (asset_set_id, version) row for asset_set.

    If asset_set.is_active is True, every other row in the table is set
    inactive first, in the same transaction -- the one place the
    single-active-set invariant is enforced.
    """
    with Session(engine) as session, session.begin():
        if asset_set.is_active:
            session.execute(update(TiAssetSetRow).values(is_active=False))

        key = (str(asset_set.id), asset_set.version)
        row = session.get(TiAssetSetRow, key)
        if row is None:
            row = TiAssetSetRow(asset_set_id=key[0], version=key[1])
            session.add(row)
        row.is_active = asset_set.is_active
        row.payload_json = asset_set.model_dump_json()
        row.created_at = asset_set.created_at


def get_ti_asset_set(engine: Engine, asset_set_id: UUID, version: str) -> TiAssetSet:
    with Session(engine) as session:
        row = session.get(TiAssetSetRow, (str(asset_set_id), version))
        if row is None:
            raise TiAssetSetNotFoundError(
                f"No TiAssetSet stored for asset_set_id={asset_set_id} version={version!r}"
            )
        return _to_domain(row)


def list_ti_asset_sets(engine: Engine) -> list[TiAssetSet]:
    with Session(engine) as session:
        rows = (
            session.execute(select(TiAssetSetRow).order_by(TiAssetSetRow.created_at))
            .scalars()
            .all()
        )
        return [_to_domain(row) for row in rows]


def get_active_ti_asset_set(engine: Engine) -> TiAssetSet:
    """Fetch the one TiAssetSet with is_active=True. Never guesses or falls
    back to the most recent version -- no active set is a hard error."""
    with Session(engine) as session:
        rows = (
            session.execute(select(TiAssetSetRow).where(TiAssetSetRow.is_active.is_(True)))
            .scalars()
            .all()
        )

    if not rows:
        raise TiAssetSetNotFoundError("No active canonical TiAssetSet is configured")
    if len(rows) > 1:
        raise MultipleActiveTiAssetSetsError(
            f"Found {len(rows)} TiAssetSetRow(s) marked active; expected at most 1"
        )
    return _to_domain(rows[0])


def activate_ti_asset_set(engine: Engine, asset_set_id: UUID, version: str) -> TiAssetSet:
    """Mark the (asset_set_id, version) row active, deactivating every other
    row first in the same transaction."""
    with Session(engine) as session, session.begin():
        key = (str(asset_set_id), version)
        row = session.get(TiAssetSetRow, key)
        if row is None:
            raise TiAssetSetNotFoundError(
                f"No TiAssetSet stored for asset_set_id={asset_set_id} version={version!r}"
            )
        session.execute(update(TiAssetSetRow).values(is_active=False))
        row.is_active = True
        session.flush()
        return _to_domain(row)
