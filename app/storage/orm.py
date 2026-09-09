"""SQLAlchemy ORM row definitions.

These are storage rows only, kept deliberately separate from the Pydantic
domain contracts in app/models/. Repository modules translate explicitly
between the two; nothing here is treated as a domain contract.
"""

from __future__ import annotations

from datetime import datetime

from sqlalchemy import Boolean, Integer, String, Text
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column
from sqlalchemy.types import TypeDecorator


class Base(DeclarativeBase):
    pass


class TZDateTime(TypeDecorator):
    """Stores a timezone-aware datetime as an ISO-8601 string.

    SQLite has no native timezone-aware datetime type, so naive storage would
    silently drop tzinfo. Round-tripping through ISO-8601 text preserves it.
    """

    impl = String
    cache_ok = True

    def process_bind_param(self, value: datetime | None, dialect):
        if value is None:
            return None
        if value.tzinfo is None:
            raise ValueError("TZDateTime requires a timezone-aware datetime")
        return value.isoformat()

    def process_result_value(self, value: str | None, dialect):
        if value is None:
            return None
        return datetime.fromisoformat(value)


class ProjectRow(Base):
    __tablename__ = "projects"

    project_id: Mapped[str] = mapped_column(String(36), primary_key=True)
    title_internal: Mapped[str] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(TZDateTime)
    updated_at: Mapped[datetime] = mapped_column(TZDateTime)
    state: Mapped[str] = mapped_column(String(64))
    template_versions_json: Mapped[str] = mapped_column(Text)
    idea_candidate_id: Mapped[str | None] = mapped_column(String(36), nullable=True)
    research_r0_id: Mapped[str | None] = mapped_column(String(36), nullable=True)
    feasibility_id: Mapped[str | None] = mapped_column(String(36), nullable=True)
    research_r1_id: Mapped[str | None] = mapped_column(String(36), nullable=True)
    narrative_plan_id: Mapped[str | None] = mapped_column(String(36), nullable=True)
    packaging_prototype_id: Mapped[str | None] = mapped_column(String(36), nullable=True)
    script_plan_id: Mapped[str | None] = mapped_column(String(36), nullable=True)


class ArtifactRow(Base):
    """One validated module-output payload, keyed by (project_id, artifact_type).

    save_artifact upserts on this key -- Phase 2 keeps only the current
    artifact per type per project, not a version history.
    """

    __tablename__ = "artifacts"

    project_id: Mapped[str] = mapped_column(String(36), primary_key=True)
    artifact_type: Mapped[str] = mapped_column(String(64), primary_key=True)
    artifact_id: Mapped[str | None] = mapped_column(String(36), nullable=True)
    schema_version: Mapped[str] = mapped_column(String(16))
    payload_json: Mapped[str] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(TZDateTime)


class ApprovalRow(Base):
    __tablename__ = "approvals"

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    approval_id: Mapped[str] = mapped_column(String(36), unique=True, index=True)
    project_id: Mapped[str] = mapped_column(String(36), index=True)
    stage: Mapped[str] = mapped_column(String(64))
    status: Mapped[str] = mapped_column(String(32))
    user_feedback: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(TZDateTime)


class ModuleRunRow(Base):
    __tablename__ = "module_runs"

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    run_id: Mapped[str] = mapped_column(String(36), unique=True, index=True)
    project_id: Mapped[str] = mapped_column(String(36), index=True)
    module: Mapped[str] = mapped_column(String(128))
    module_version: Mapped[str] = mapped_column(String(32))
    started_at: Mapped[datetime] = mapped_column(TZDateTime)
    completed_at: Mapped[datetime | None] = mapped_column(TZDateTime, nullable=True)
    input_ids_json: Mapped[str] = mapped_column(Text)
    output_id: Mapped[str | None] = mapped_column(String(64), nullable=True)
    status: Mapped[str] = mapped_column(String(16))
    error_message: Mapped[str | None] = mapped_column(Text, nullable=True)


class ProductionRunRow(Base):
    """One ProductionRun (Phase 33) -- a cross-module orchestration
    workflow, distinct from ModuleRunRow's own single-module-execution
    record. A project may accumulate multiple ProductionRun rows over
    time (e.g. across resume cycles), unlike ArtifactRow's own one-row-
    per-(project_id, artifact_type) convention."""

    __tablename__ = "production_runs"

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    run_id: Mapped[str] = mapped_column(String(36), unique=True, index=True)
    project_id: Mapped[str] = mapped_column(String(36), index=True)
    target_node: Mapped[str] = mapped_column(String(64))
    status: Mapped[str] = mapped_column(String(32))
    node_states_json: Mapped[str] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(TZDateTime)
    updated_at: Mapped[datetime] = mapped_column(TZDateTime)
    started_at: Mapped[datetime | None] = mapped_column(TZDateTime, nullable=True)
    completed_at: Mapped[datetime | None] = mapped_column(TZDateTime, nullable=True)
    stop_reason: Mapped[str | None] = mapped_column(String(64), nullable=True)
    waiting_gate: Mapped[str | None] = mapped_column(String(64), nullable=True)


class ApprovalDecisionRow(Base):
    """One ApprovalDecision (Phase 33) -- binds a human APPROVED/REJECTED
    decision to an EXACT subject artifact id, distinct from the existing
    ApprovalRow/HumanApproval mechanism (app/review/service.py), which
    drives ProjectState transitions for the earlier creative-review gates
    and carries no artifact-id binding at all. A project may accumulate
    multiple decisions per gate_type over time (one per artifact
    replacement) -- app/storage/approval_decisions.py always reads the
    most recent one for a given (project_id, gate_type,
    subject_artifact_id)."""

    __tablename__ = "approval_decisions"

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    decision_id: Mapped[str] = mapped_column(String(36), unique=True, index=True)
    project_id: Mapped[str] = mapped_column(String(36), index=True)
    gate_type: Mapped[str] = mapped_column(String(64))
    subject_artifact_id: Mapped[str] = mapped_column(String(36), index=True)
    decision: Mapped[str] = mapped_column(String(16))
    decided_at: Mapped[datetime] = mapped_column(TZDateTime)
    note: Mapped[str | None] = mapped_column(Text, nullable=True)


class TiAssetSetRow(Base):
    """One versioned canonical Tí asset set, stored as a validated
    TiAssetSet JSON payload -- image bytes are never stored here, only the
    metadata/relative-path fields TiAsset already carries (Phase 21).

    Primary key is (asset_set_id, version): a lineage (asset_set_id) may
    accumulate multiple stored versions over time, but at most one row
    across the whole table may have is_active=True (enforced by
    app/storage/ti_assets.py, never by a database constraint alone).
    """

    __tablename__ = "ti_asset_sets"

    asset_set_id: Mapped[str] = mapped_column(String(36), primary_key=True)
    version: Mapped[str] = mapped_column(String(32), primary_key=True)
    is_active: Mapped[bool] = mapped_column(Boolean)
    payload_json: Mapped[str] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(TZDateTime)


class WorkspaceAssetRow(Base):
    """One WorkspaceAsset (Phase 36) -- immutable ingestion metadata for a
    user-provided/generated/system file living under the project
    workspace. File bytes are never stored here, only
    `stored_relative_path` (content-addressed, see
    app/workspace/storage.py) and `sha256` for exact-identity dedupe.
    Never updated in place after insert -- a re-ingestion of the same
    bytes under a different asset_type creates a new row referencing the
    same stored blob (requirement #9's policy A)."""

    __tablename__ = "workspace_assets"

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    asset_id: Mapped[str] = mapped_column(String(36), unique=True, index=True)
    project_id: Mapped[str] = mapped_column(String(36), index=True)
    asset_type: Mapped[str] = mapped_column(String(32), index=True)
    origin: Mapped[str] = mapped_column(String(16))
    original_filename: Mapped[str] = mapped_column(Text)
    stored_relative_path: Mapped[str] = mapped_column(Text)
    media_type: Mapped[str] = mapped_column(String(16))
    file_size_bytes: Mapped[int] = mapped_column(Integer)
    sha256: Mapped[str] = mapped_column(String(64), index=True)
    created_at: Mapped[datetime] = mapped_column(TZDateTime)
    note: Mapped[str | None] = mapped_column(Text, nullable=True)
    tags_json: Mapped[str] = mapped_column(Text)
    logical_name: Mapped[str | None] = mapped_column(Text, nullable=True)


class WorkspaceAssetLinkRow(Base):
    """One WorkspaceAssetLink (Phase 36) -- lineage only, e.g. "this
    ProductionRun consumed this brief asset". See
    app/workspace/models.py::WorkspaceAssetLink."""

    __tablename__ = "workspace_asset_links"

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    link_id: Mapped[str] = mapped_column(String(36), unique=True, index=True)
    workspace_asset_id: Mapped[str] = mapped_column(String(36), index=True)
    target_artifact_type: Mapped[str] = mapped_column(String(64))
    target_artifact_id: Mapped[str] = mapped_column(String(64))
    created_at: Mapped[datetime] = mapped_column(TZDateTime)


class WorkspaceAssetSelectionRow(Base):
    """The one active WorkspaceAsset for a (project_id, role) pair
    (Phase 36 requirement #20) -- e.g. the current PROJECT_BRIEF. Primary
    key is (project_id, role): at most one active selection per role per
    project, upserted in place (this row IS a "current pointer", unlike
    WorkspaceAssetRow's own immutable-insert convention)."""

    __tablename__ = "workspace_asset_selections"

    project_id: Mapped[str] = mapped_column(String(36), primary_key=True)
    role: Mapped[str] = mapped_column(String(32), primary_key=True)
    asset_id: Mapped[str] = mapped_column(String(36))
    updated_at: Mapped[datetime] = mapped_column(TZDateTime)
