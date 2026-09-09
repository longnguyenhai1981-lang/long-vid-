"""Script Verification Engine input/output contracts.

ScriptVerificationReport remains the single approved business output -- these
are thin wrappers around it, not a competing schema.
"""

from __future__ import annotations

from uuid import UUID

from app.llm.models import TokenUsage
from app.models.common import MotilyModel
from app.models.script import ScriptVerificationReport

SCRIPT_VERIFICATION_REPORT_ARTIFACT_TYPE = "script_verification_report"


class ScriptVerificationInput(MotilyModel):
    project_id: UUID
    additional_context: str | None = None


class ScriptVerificationResult(MotilyModel):
    report: ScriptVerificationReport
    module_run_id: UUID
    generation_attempts: int
    provider: str
    model: str
    token_usage: TokenUsage | None = None
    business_correction_used: bool
