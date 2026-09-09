"""VoicePlan: delivery-performance metadata for an already-locked ScriptPlan.

VoicePlan owns delivery metadata only -- wording, claims, and factual
content stay ScriptPlan's alone (see docs/TECHNICAL_SPEC_v0.1.md). No audio
is generated or referenced here; exact timing/milliseconds are explicitly
out of scope and remain a future rendering concern.
"""

from __future__ import annotations

from uuid import UUID, uuid4

from pydantic import Field, model_validator

from app.models.common import Energy, MotilyModel, MusicState, Pace, VoiceState, non_blank


class VoiceChunk(MotilyModel):
    """One coherent performance unit -- one or more adjacent ScriptLines
    sharing a single delivery treatment. Not one audio request per sentence;
    see docs/TECHNICAL_SPEC_v0.1.md, Phase 13."""

    chunk_id: str
    line_ids: list[str] = Field(min_length=1)
    voice_state: VoiceState
    pace: Pace
    energy: Energy
    take_count: int = Field(ge=1, le=3)
    music_state: MusicState
    sfx_opportunity: str | None = None
    notes: str | None = None

    @model_validator(mode="after")
    def _check_required_text(self) -> "VoiceChunk":
        non_blank(self.chunk_id, "chunk_id")
        return self


class VoicePlan(MotilyModel):
    id: UUID = Field(default_factory=uuid4)
    script_plan_id: UUID
    chunks: list[VoiceChunk] = Field(min_length=1)
