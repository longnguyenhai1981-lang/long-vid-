"""Timing / Assembly Planning Engine input/output contracts.

AssemblyPlan (app/models/assembly.py) is the single approved business
output -- these are thin wrappers around it, not a competing schema.
"""

from __future__ import annotations

from uuid import UUID

from app.llm.models import TokenUsage
from app.models.assembly import AssemblyPlan
from app.models.common import MotilyModel

ASSEMBLY_PLAN_ARTIFACT_TYPE = "assembly_plan"


class AssemblyPlanningInput(MotilyModel):
    project_id: UUID
    additional_context: str | None = None


class AssemblyPlanningResult(MotilyModel):
    assembly_plan: AssemblyPlan
    module_run_id: UUID
    generation_attempts: int
    provider: str
    model: str
    token_usage: TokenUsage | None = None
    business_correction_used: bool
