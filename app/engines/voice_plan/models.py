"""Voice Planning Engine input/output contracts.

VoicePlan (app/models/voice.py) is the single approved business output --
these are thin wrappers around it, not a competing schema.
"""

from __future__ import annotations

from uuid import UUID

from app.llm.models import TokenUsage
from app.models.common import MotilyModel
from app.models.voice import VoicePlan

VOICE_PLAN_ARTIFACT_TYPE = "voice_plan"


class VoicePlanningInput(MotilyModel):
    project_id: UUID
    additional_context: str | None = None


class VoicePlanningResult(MotilyModel):
    voice_plan: VoicePlan
    module_run_id: UUID
    generation_attempts: int
    provider: str
    model: str
    token_usage: TokenUsage | None = None
    business_correction_used: bool
