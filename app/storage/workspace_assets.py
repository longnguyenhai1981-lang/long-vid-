"""WorkspaceAsset/WorkspaceAssetLink/WorkspaceAssetSelection persistence
(Phase 36). Same database file as every other repository module -- no
second database.

WorkspaceAssetRow is inserted once and never updated (WorkspaceAsset is
immutable, requirement #8); WorkspaceAssetSelectionRow is the one
upserted "current pointer" row per (project_id, role).
"""

from __future__ import annotations

import json
from uuid import UUID

from sqlalchemy import Engine, select
from sqlalchemy.orm import Session

from app.storage.errors import WorkspaceAssetNotFoundError
from app.storage.orm import WorkspaceAssetLinkRow, WorkspaceAssetRow, WorkspaceAssetSelectionRow
from app.workspace.models import AssetOrigin, WorkspaceAsset, WorkspaceAssetLink, WorkspaceAssetSelection, WorkspaceAssetType


def _to_domain(row: WorkspaceAssetRow) -> WorkspaceAsset:
    return WorkspaceAsset(
        id=UUID(row.asset_id),
        project_id=UUID(row.project_id),
        asset_type=WorkspaceAssetType(row.asset_type),
        origin=AssetOrigin(row.origin),
        original_filename=row.original_filename,
        stored_relative_path=row.stored_relative_path,
        media_type=row.media_type,
        file_size_bytes=row.file_size_bytes,
        sha256=row.sha256,
        created_at=row.created_at,
        note=row.note,
        tags=json.loads(row.tags_json) if row.tags_json else [],
        logical_name=row.logical_name,
    )


def save_workspace_asset(engine: Engine, asset: WorkspaceAsset) -> None:
    """Always inserts -- WorkspaceAsset rows are never updated in place."""
    with Session(engine) as session, session.begin():
        row = WorkspaceAssetRow(
            asset_id=str(asset.id),
            project_id=str(asset.project_id),
            asset_type=asset.asset_type.value,
            origin=asset.origin.value,
            original_filename=asset.original_filename,
            stored_relative_path=asset.stored_relative_path,
            media_type=asset.media_type,
            file_size_bytes=asset.file_size_bytes,
            sha256=asset.sha256,
            created_at=asset.created_at,
            note=asset.note,
            tags_json=json.dumps(asset.tags),
            logical_name=asset.logical_name,
        )
        session.add(row)


def get_workspace_asset(engine: Engine, asset_id: UUID) -> WorkspaceAsset:
    with Session(engine) as session:
        row = session.execute(
            select(WorkspaceAssetRow).where(WorkspaceAssetRow.asset_id == str(asset_id))
        ).scalar_one_or_none()
        if row is None:
            raise WorkspaceAssetNotFoundError(f"WorkspaceAsset not found: {asset_id}")
        return _to_domain(row)


def list_workspace_assets(
    engine: Engine,
    project_id: UUID,
    *,
    asset_type: WorkspaceAssetType | None = None,
    origin: AssetOrigin | None = None,
    limit: int | None = None,
) -> list[WorkspaceAsset]:
    """Newest-first (by insertion order, i.e. row id) -- documented,
    deterministic ordering (requirement #28)."""
    with Session(engine) as session:
        stmt = select(WorkspaceAssetRow).where(WorkspaceAssetRow.project_id == str(project_id))
        if asset_type is not None:
            stmt = stmt.where(WorkspaceAssetRow.asset_type == asset_type.value)
        if origin is not None:
            stmt = stmt.where(WorkspaceAssetRow.origin == origin.value)
        stmt = stmt.order_by(WorkspaceAssetRow.id.desc())
        if limit is not None:
            stmt = stmt.limit(limit)
        rows = session.execute(stmt).scalars().all()
        return [_to_domain(row) for row in rows]


def find_assets_by_type(engine: Engine, project_id: UUID, asset_type: WorkspaceAssetType) -> list[WorkspaceAsset]:
    return list_workspace_assets(engine, project_id, asset_type=asset_type)


def find_assets_by_sha256(engine: Engine, project_id: UUID, sha256: str) -> list[WorkspaceAsset]:
    with Session(engine) as session:
        rows = session.execute(
            select(WorkspaceAssetRow)
            .where(WorkspaceAssetRow.project_id == str(project_id), WorkspaceAssetRow.sha256 == sha256)
            .order_by(WorkspaceAssetRow.id.desc())
        ).scalars().all()
        return [_to_domain(row) for row in rows]


def save_workspace_asset_link(engine: Engine, link: WorkspaceAssetLink) -> None:
    with Session(engine) as session, session.begin():
        session.add(
            WorkspaceAssetLinkRow(
                link_id=str(link.id),
                workspace_asset_id=str(link.workspace_asset_id),
                target_artifact_type=link.target_artifact_type,
                target_artifact_id=link.target_artifact_id,
                created_at=link.created_at,
            )
        )


def list_links_for_asset(engine: Engine, workspace_asset_id: UUID) -> list[WorkspaceAssetLink]:
    with Session(engine) as session:
        rows = session.execute(
            select(WorkspaceAssetLinkRow)
            .where(WorkspaceAssetLinkRow.workspace_asset_id == str(workspace_asset_id))
            .order_by(WorkspaceAssetLinkRow.id)
        ).scalars().all()
        return [
            WorkspaceAssetLink(
                id=UUID(row.link_id), workspace_asset_id=UUID(row.workspace_asset_id),
                target_artifact_type=row.target_artifact_type, target_artifact_id=row.target_artifact_id,
                created_at=row.created_at,
            )
            for row in rows
        ]


def save_workspace_asset_selection(engine: Engine, selection: WorkspaceAssetSelection) -> None:
    """Upsert by (project_id, role) -- the one active selection row."""
    with Session(engine) as session, session.begin():
        row = session.get(WorkspaceAssetSelectionRow, (str(selection.project_id), selection.role))
        if row is None:
            row = WorkspaceAssetSelectionRow(project_id=str(selection.project_id), role=selection.role)
            session.add(row)
        row.asset_id = str(selection.asset_id)
        row.updated_at = selection.updated_at


def get_workspace_asset_selection(engine: Engine, project_id: UUID, role: str) -> WorkspaceAssetSelection | None:
    with Session(engine) as session:
        row = session.get(WorkspaceAssetSelectionRow, (str(project_id), role))
        if row is None:
            return None
        return WorkspaceAssetSelection(
            project_id=UUID(row.project_id), role=row.role, asset_id=UUID(row.asset_id), updated_at=row.updated_at,
        )
