"""Feasibility Engine input/output contracts.

FeasibilityReport remains the single approved business output -- these are
thin wrappers around it, not a competing schema.
"""

from __future__ import annotations

from uuid import UUID

from app.llm.models import TokenUsage
from app.models.common import MotilyModel
from app.models.feasibility import FeasibilityReport

FEASIBILITY_REPORT_ARTIFACT_TYPE = "feasibility_report"


class FeasibilityEngineInput(MotilyModel):
    project_id: UUID
    additional_context: str | None = None


class FeasibilityEngineResult(MotilyModel):
    feasibility: FeasibilityReport
    module_run_id: UUID
    generation_attempts: int
    provider: str
    model: str
    token_usage: TokenUsage | None = None
