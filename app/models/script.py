"""Script contracts: ScriptPlan built from beats made of lines, plus verification."""

from __future__ import annotations

from uuid import UUID, uuid4

from pydantic import Field, model_validator

from app.models.common import (
    GateStatus,
    MotilyModel,
    NarrativeFunction,
    PauseIntent,
    ScriptLineFunction,
    non_blank,
)


class ScriptLine(MotilyModel):
    line_id: str
    text: str
    function: ScriptLineFunction
    emotion: str | None = None
    emphasis: str | None = None
    pause_after: PauseIntent = PauseIntent.NONE
    claim_ids: list[str] = Field(default_factory=list)
    visual_opportunity: str | None = None

    @model_validator(mode="after")
    def _check_required_text(self) -> "ScriptLine":
        non_blank(self.line_id, "line_id")
        non_blank(self.text, "text")
        return self


class ScriptBeat(MotilyModel):
    beat_id: str
    narrative_node: str
    narrative_function: NarrativeFunction
    lines: list[ScriptLine] = Field(default_factory=list)
    micro_hook: str | None = None


class ScriptPlan(MotilyModel):
    id: UUID = Field(default_factory=uuid4)
    estimated_duration_seconds: int
    beats: list[ScriptBeat] = Field(default_factory=list)
    claim_coverage: list[str] = Field(default_factory=list)
    unmapped_claims: list[str] = Field(default_factory=list)
    qa_status: GateStatus

    @model_validator(mode="after")
    def _check_duration(self) -> "ScriptPlan":
        if self.estimated_duration_seconds <= 0:
            raise ValueError("estimated_duration_seconds must be positive")
        return self


class ScriptVerificationReport(MotilyModel):
    status: GateStatus
    unsupported_lines: list[str] = Field(default_factory=list)
    overstated_lines: list[str] = Field(default_factory=list)
    dangerous_simplifications: list[str] = Field(default_factory=list)
    recommended_rewrites: list[str] = Field(default_factory=list)
