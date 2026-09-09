"""Packaging P1 Engine input/output contracts.

FinalPackagingPlan (app/models/packaging_p1.py) is the single approved
business output -- these are thin wrappers around it, not a competing
schema.
"""

from __future__ import annotations

from uuid import UUID

from app.llm.models import TokenUsage
from app.models.common import MotilyModel
from app.models.packaging_p1 import FinalPackagingPlan

PACKAGING_P1_ARTIFACT_TYPE = "packaging_p1"


class PackagingP1Input(MotilyModel):
    project_id: UUID
    additional_context: str | None = None


class PackagingP1Result(MotilyModel):
    final_packaging_plan: FinalPackagingPlan
    module_run_id: UUID
    generation_attempts: int
    provider: str
    model: str
    token_usage: TokenUsage | None = None
    business_correction_used: bool
