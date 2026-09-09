"""FinalPackagingPlan: the final title/thumbnail packaging pass, made after
the script, visuals, and assembly are fully planned.

FinalPackagingPlan owns final title wording, thumbnail creative direction,
and the final viewer-promise statement only -- it never owns the script,
scientific claims, or actual rendered media (see
docs/TECHNICAL_SPEC_v0.1.md). No thumbnail image is generated or
referenced here; thumbnail_concept is creative direction for a future
rendering pass, never an image-generation prompt.

Deliberately no non-blank model_validator on the text fields below (unlike
most other Motily contracts): Phase 16's business validation treats a
blank required field as a *business* problem eligible for the engine's
one bounded correction attempt, not a Pydantic/structured-retry problem --
see app/engines/packaging_p1/validation.py.
"""

from __future__ import annotations

from uuid import UUID, uuid4

from pydantic import Field

from app.models.common import MotilyModel, RiskLevel


class FinalPackagingPlan(MotilyModel):
    id: UUID = Field(default_factory=uuid4)
    packaging_prototype_id: UUID
    script_plan_id: UUID
    visual_plan_id: UUID
    assembly_plan_id: UUID
    title: str
    thumbnail_text: str | None = None
    thumbnail_concept: str
    final_promise: str
    expected_payoff: str
    viewer_expectation: str
    rationale: str
    risk_of_misleading: RiskLevel
    notes: str | None = None
