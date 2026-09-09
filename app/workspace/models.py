"""Phase 36 typed contracts for the project workspace / asset-ingestion
layer -- deterministic local IO only, no AI classification, no network,
no OCR (verified by tests/test_workspace_purity.py).

WorkspaceAsset is immutable once created (requirement #8): ingestion
never edits an existing row, it only ever inserts a new one. The same
uploaded bytes MAY be referenced by more than one WorkspaceAsset row
(requirement #9, policy A) -- each row is an independent logical/typed
view over one physical blob, identified by `sha256` + `stored_relative_path`.
"""

from __future__ import annotations

import re
from datetime import datetime
from enum import Enum
from uuid import UUID, uuid4

from pydantic import Field, model_validator

from app.models.common import MotilyModel, non_blank

_SHA256_RE = re.compile(r"^[0-9a-f]{64}$")


class AssetOrigin(str, Enum):
    """Where an asset's own CONTENT came from -- never who/what wrote the
    stored bytes to disk (that is always this package, for every origin)."""

    USER_PROVIDED = "USER_PROVIDED"
    GENERATED = "GENERATED"
    SYSTEM = "SYSTEM"


class WorkspaceAssetType(str, Enum):
    """Explicit, deterministic categories (requirement #4) -- exactly the
    ones this project's real production needs use today, never a
    speculative catch-all list. SOURCE_DOCUMENT (factual/research input)
    and REFERENCE_DOCUMENT/REFERENCE_IMAGE (style/context, NOT a factual
    source-of-truth) are kept deliberately distinct (requirement #21), as
    are EVIDENCE_MEDIA (footage/imagery used AS evidence in the final
    production) and REFERENCE_IMAGE/BACKGROUND_ASSET (requirement #22)."""

    PROJECT_BRIEF = "PROJECT_BRIEF"
    SOURCE_DOCUMENT = "SOURCE_DOCUMENT"
    REFERENCE_DOCUMENT = "REFERENCE_DOCUMENT"
    REFERENCE_IMAGE = "REFERENCE_IMAGE"
    CHARACTER_ASSET = "CHARACTER_ASSET"
    TI_CANONICAL_ASSET = "TI_CANONICAL_ASSET"
    BACKGROUND_ASSET = "BACKGROUND_ASSET"
    EVIDENCE_MEDIA = "EVIDENCE_MEDIA"
    AUDIO_REFERENCE = "AUDIO_REFERENCE"
    MUSIC_ASSET = "MUSIC_ASSET"
    SFX_ASSET = "SFX_ASSET"
    VIDEO_REFERENCE = "VIDEO_REFERENCE"
    OTHER = "OTHER"


class WorkspaceAsset(MotilyModel):
    id: UUID = Field(default_factory=uuid4)
    project_id: UUID
    asset_type: WorkspaceAssetType
    origin: AssetOrigin
    original_filename: str
    stored_relative_path: str
    media_type: str
    """The file extension without a leading dot, lowercased (e.g. "pdf",
    "png") -- a deterministic, cheap classification signal, never a
    content-sniffed MIME guess."""
    file_size_bytes: int
    sha256: str
    created_at: datetime
    note: str | None = None
    tags: list[str] = Field(default_factory=list)
    logical_name: str | None = None

    @model_validator(mode="after")
    def _check_invariants(self) -> "WorkspaceAsset":
        non_blank(self.original_filename, "original_filename")
        non_blank(self.media_type, "media_type")
        if not _SHA256_RE.match(self.sha256):
            raise ValueError(f"sha256 must be 64 lowercase hex characters, got {self.sha256!r}")
        if self.file_size_bytes <= 0:
            raise ValueError("file_size_bytes must be > 0 -- an empty file is never a valid asset")
        non_blank(self.stored_relative_path, "stored_relative_path")
        if self.created_at.tzinfo is None:
            raise ValueError("created_at must be timezone-aware")
        return self


class WorkspaceAssetLink(MotilyModel):
    """Lineage only (requirement #24) -- e.g. "this ProductionRun used
    this brief asset" or "this canonical Tí import came from this ZIP".
    Deliberately narrow: a (workspace_asset_id, target_artifact_type,
    target_artifact_id) triple, never a generic graph-database edge with
    typed relationships."""

    id: UUID = Field(default_factory=uuid4)
    workspace_asset_id: UUID
    target_artifact_type: str
    target_artifact_id: str
    created_at: datetime

    @model_validator(mode="after")
    def _check_invariants(self) -> "WorkspaceAssetLink":
        non_blank(self.target_artifact_type, "target_artifact_type")
        non_blank(self.target_artifact_id, "target_artifact_id")
        if self.created_at.tzinfo is None:
            raise ValueError("created_at must be timezone-aware")
        return self


class WorkspaceAssetSelection(MotilyModel):
    """The one active/current asset for a role that needs exactly one
    (requirement #20) -- today only `"PROJECT_BRIEF"` is exercised, but
    `role` is a plain string rather than a closed enum so a future role
    (e.g. an active Tí canonical set import) needs no schema change."""

    project_id: UUID
    role: str
    asset_id: UUID
    updated_at: datetime

    @model_validator(mode="after")
    def _check_invariants(self) -> "WorkspaceAssetSelection":
        non_blank(self.role, "role")
        if self.updated_at.tzinfo is None:
            raise ValueError("updated_at must be timezone-aware")
        return self


PROJECT_BRIEF_ROLE = "PROJECT_BRIEF"
