"""Narrative Engine input/output contracts.

NarrativePlan remains the single approved business output -- these are thin
wrappers around it, not a competing schema.
"""

from __future__ import annotations

from uuid import UUID

from app.llm.models import TokenUsage
from app.models.common import MotilyModel
from app.models.narrative import NarrativePlan

NARRATIVE_PLAN_ARTIFACT_TYPE = "narrative_plan"


class NarrativeEngineInput(MotilyModel):
    project_id: UUID
    additional_context: str | None = None


class NarrativeEngineResult(MotilyModel):
    narrative: NarrativePlan
    module_run_id: UUID
    generation_attempts: int
    provider: str
    model: str
    token_usage: TokenUsage | None = None
    business_correction_used: bool
