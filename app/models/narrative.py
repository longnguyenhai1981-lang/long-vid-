"""NarrativePlan: the question-ladder structure a script will be built from."""

from __future__ import annotations

from uuid import UUID, uuid4

from pydantic import Field, model_validator

from app.models.common import MotilyModel, OpeningType, non_blank


class SCQA(MotilyModel):
    """Situation-Complication-Question-Answer framing for the narrative plan."""

    situation: str
    complication: str
    question: str
    answer: str


class QuestionLadderNode(MotilyModel):
    id: str
    question: str
    why_viewer_cares: str
    partial_answer: str
    claim_ids: list[str] = Field(default_factory=list)
    creates_next_question: str | None = None
    information_gap: str

    @model_validator(mode="after")
    def _check_required_text(self) -> "QuestionLadderNode":
        non_blank(self.id, "id")
        non_blank(self.question, "question")
        return self


class NarrativePlan(MotilyModel):
    id: UUID = Field(default_factory=uuid4)
    central_question: str
    scqa: SCQA
    opening: OpeningType
    question_ladder: list[QuestionLadderNode] = Field(default_factory=list)
    ti_role: str
    physics_entry_points: list[str] = Field(default_factory=list)
    deep_dive_points: list[str] = Field(default_factory=list)
    breath_moments: list[str] = Field(default_factory=list)
    ending: str
    claim_ids_used: list[str] = Field(default_factory=list)

    @model_validator(mode="after")
    def _check_required_text(self) -> "NarrativePlan":
        non_blank(self.central_question, "central_question")
        return self
