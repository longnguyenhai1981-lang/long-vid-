"""IdeaCandidate: a single candidate topic surfaced during idea discovery."""

from __future__ import annotations

from uuid import UUID, uuid4

from pydantic import Field, model_validator

from app.models.common import GateEvaluation, MotilyModel, PrimaryPayoff, non_blank


class ABT(MotilyModel):
    """And-But-Therefore framing: context, complication, investigation."""

    and_context: str
    but_complication: str
    therefore_investigation: str

    @model_validator(mode="after")
    def _check_complication(self) -> "ABT":
        non_blank(self.but_complication, "abt.but_complication")
        return self


class IdeaCandidate(MotilyModel):
    id: UUID = Field(default_factory=uuid4)
    topic: str
    central_question: str
    abt: ABT
    primary_payoff: PrimaryPayoff
    secondary_payoffs: list[PrimaryPayoff] = Field(default_factory=list)
    physics_core: str
    audience_prerequisite: str
    brand_fit: GateEvaluation
    general_audience_gate: GateEvaluation
    longform_potential: GateEvaluation
    research_questions: list[str] = Field(default_factory=list)
    risks: list[str] = Field(default_factory=list)

    @model_validator(mode="after")
    def _check_required_text(self) -> "IdeaCandidate":
        non_blank(self.topic, "topic")
        non_blank(self.central_question, "central_question")
        non_blank(self.physics_core, "physics_core")
        return self
