"""VisualRenderManifest: metadata describing rendered visual asset files, and
unrendered-requirement bookkeeping, for an already-locked ScriptPlan/
VoicePlan/VisualPlan trio.

The manifest stores metadata only -- render job identity, which beat it
covers, its file path, measured dimensions, and provider-request identity
for a rendered asset; or a status/reference/notes record for a beat that
was not rendered by a VisualProvider at all. It never stores raw asset
bytes (see app/visual/models.py's VisualRenderResponse docstring for why)
and never stores a machine-specific absolute path -- file_path is relative
to whatever visual-output root the VisualFileStore that wrote it was
configured with (see docs/TECHNICAL_SPEC_v0.1.md, Phase 19).
"""

from __future__ import annotations

from datetime import datetime
from uuid import UUID, uuid4

from pydantic import Field, model_validator

from app.models.common import (
    MotilyModel,
    VisualMediaType,
    VisualOutputFormat,
    VisualRequirementStatus,
    non_blank,
)
from app.models.ti_assets import TiState


class RenderedVisualAsset(MotilyModel):
    render_job_id: str
    beat_id: str
    media_type: VisualMediaType
    file_path: str
    width: int | None = None
    height: int | None = None
    provider_request_id: str | None = None
    resolved_ti_state: TiState | None = None
    """Set only for a TI_STATE beat composited onto a background (Phase 23)
    -- the canonical TiState resolved from the beat's VisualTiState via
    to_ti_state(), never a provider output. None for every other media
    type, and None for a TI_STATE beat that was not composited (see
    VisualRenderRequirement.resolved_ti_state for that case instead)."""
    notes: str | None = None

    @model_validator(mode="after")
    def _check_invariants(self) -> "RenderedVisualAsset":
        non_blank(self.render_job_id, "render_job_id")
        non_blank(self.beat_id, "beat_id")
        non_blank(self.file_path, "file_path")
        if self.width is not None and self.width <= 0:
            raise ValueError("width must be > 0 when supplied")
        if self.height is not None and self.height <= 0:
            raise ValueError("height must be > 0 when supplied")
        if self.resolved_ti_state is not None and self.media_type is not VisualMediaType.TI_STATE:
            raise ValueError("resolved_ti_state may only be set when media_type is TI_STATE")
        return self


class VisualRenderRequirement(MotilyModel):
    beat_id: str
    media_type: VisualMediaType
    status: VisualRequirementStatus
    reference: str | None = None
    notes: str | None = None
    resolved_ti_state: TiState | None = None
    """Set only for a TI_STATE beat's CANONICAL_ASSET_READY requirement
    (Phase 23) -- the canonical TiState resolved from the beat's
    VisualTiState via to_ti_state(). None for every other status/media
    type."""

    @model_validator(mode="after")
    def _check_invariants(self) -> "VisualRenderRequirement":
        non_blank(self.beat_id, "beat_id")
        if self.status is VisualRequirementStatus.RENDERED:
            raise ValueError(
                "VisualRenderRequirement.status must not be RENDERED -- rendered "
                "beats belong in VisualRenderManifest.assets instead"
            )
        if self.resolved_ti_state is not None and self.media_type is not VisualMediaType.TI_STATE:
            raise ValueError("resolved_ti_state may only be set when media_type is TI_STATE")
        return self


class VisualRenderManifest(MotilyModel):
    id: UUID = Field(default_factory=uuid4)
    script_plan_id: UUID
    voice_plan_id: UUID
    visual_plan_id: UUID
    provider: str
    output_format: VisualOutputFormat
    assets: list[RenderedVisualAsset] = Field(default_factory=list)
    requirements: list[VisualRenderRequirement] = Field(default_factory=list)
    created_at: datetime

    @model_validator(mode="after")
    def _check_invariants(self) -> "VisualRenderManifest":
        non_blank(self.provider, "provider")
        if self.created_at.tzinfo is None:
            raise ValueError("created_at must be timezone-aware")
        if not self.assets and not self.requirements:
            raise ValueError("a VisualRenderManifest must cover at least one beat")
        return self
