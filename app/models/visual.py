"""VisualPlan: visual representation strategy for an already-locked
ScriptPlan/VoicePlan pair.

VisualPlan owns visual representation strategy only -- narration, claims,
line order, and delivery metadata stay ScriptPlan/VoicePlan's alone (see
docs/TECHNICAL_SPEC_v0.1.md). No image or video is generated or referenced
here; this is planning metadata for a future rendering pass, not an image
prompt.
"""

from __future__ import annotations

from uuid import UUID, uuid4

from pydantic import Field, model_validator

from app.models.common import (
    ComplexityClass,
    MotilyModel,
    VisualFunction,
    VisualLevel,
    VisualMediaType,
    VisualTiState,
    non_blank,
)
from app.models.diagram import DiagramSpec


class VisualBeat(MotilyModel):
    """One planned visual unit. May cover one ScriptLine, several adjacent
    ScriptLines, or part of one ScriptBeat -- one sentence does not imply
    one new shot."""

    beat_id: str
    script_line_ids: list[str] = Field(min_length=1)
    narrative_node: str
    visual_level: VisualLevel
    visual_function: VisualFunction
    media_type: VisualMediaType
    complexity: ComplexityClass
    concept: str
    primary_focus: str
    secondary_elements: list[str] = Field(default_factory=list)
    context_elements: list[str] = Field(default_factory=list)
    ti_state: VisualTiState | None = None
    evidence_source_ids: list[str] = Field(default_factory=list)
    motion_intent: str | None = None
    reuse_key: str | None = None
    diagram_spec: DiagramSpec | None = None
    """Set only for a media_type=DIAGRAM beat (Phase 25) -- a fully
    self-contained deterministic diagram, rendered locally by
    DiagramRenderer, never by a VisualProvider. None for every other
    media type. Like ti_state (TI_STATE) and reuse_key (ASSET_REUSE),
    the required-for-this-media-type check lives where the field is
    actually consumed (app/renderers/visual/renderer.py), not as a
    cross-field validator here -- VisualBeat stays one shared shape
    across every media type."""
    notes: str | None = None

    @model_validator(mode="after")
    def _check_required_text(self) -> "VisualBeat":
        non_blank(self.beat_id, "beat_id")
        non_blank(self.narrative_node, "narrative_node")
        non_blank(self.concept, "concept")
        non_blank(self.primary_focus, "primary_focus")
        return self


class VisualPlan(MotilyModel):
    id: UUID = Field(default_factory=uuid4)
    script_plan_id: UUID
    voice_plan_id: UUID
    beats: list[VisualBeat] = Field(min_length=1)
    reusable_assets: list[str] = Field(default_factory=list)
    notes: str | None = None
