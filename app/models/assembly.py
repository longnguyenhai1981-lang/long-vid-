"""AssemblyPlan: temporal/assembly strategy for an already-locked
ScriptPlan/VoicePlan/VisualPlan triple.

AssemblyPlan owns estimated timing, segment sequence, voice/visual
alignment, and transition intent only -- spoken wording, visual concept
creation, and factual truth stay ScriptPlan/VisualPlan/ResearchPackage's
alone (see docs/TECHNICAL_SPEC_v0.1.md). Timing here is estimated planning
metadata, never final rendered timing; no media path, keyframe, or frame
number is recorded.
"""

from __future__ import annotations

from uuid import UUID, uuid4

from pydantic import Field, model_validator

from app.models.common import MotilyModel, MusicState, TransitionIntent, non_blank


class AssemblySegment(MotilyModel):
    """The smallest useful temporal unit for Phase 15: exactly one
    VisualBeat's worth of the timeline, carrying estimated start/end and
    transition intent around it."""

    segment_id: str
    script_line_ids: list[str] = Field(min_length=1)
    voice_chunk_ids: list[str] = Field(default_factory=list)
    visual_beat_id: str
    start_seconds: float = Field(ge=0)
    end_seconds: float = Field(ge=0)
    music_state: MusicState
    transition_in: TransitionIntent
    transition_out: TransitionIntent
    emphasis_note: str | None = None
    assembly_note: str | None = None

    @model_validator(mode="after")
    def _check_invariants(self) -> "AssemblySegment":
        non_blank(self.segment_id, "segment_id")
        non_blank(self.visual_beat_id, "visual_beat_id")
        if self.end_seconds <= self.start_seconds:
            raise ValueError("end_seconds must be greater than start_seconds")
        return self


class AssemblyPlan(MotilyModel):
    id: UUID = Field(default_factory=uuid4)
    script_plan_id: UUID
    voice_plan_id: UUID
    visual_plan_id: UUID
    estimated_total_duration_seconds: float = Field(ge=0)
    segments: list[AssemblySegment] = Field(min_length=1)
    required_assets: list[str] = Field(default_factory=list)
    production_notes: str | None = None

    @model_validator(mode="after")
    def _check_required_assets_non_blank(self) -> "AssemblyPlan":
        for asset in self.required_assets:
            non_blank(asset, "required_assets entry")
        return self
