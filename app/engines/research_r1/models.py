"""R1 Research Engine input/output contracts.

ResearchPackage remains the single approved business output -- these are
thin wrappers around it, not a competing schema.
"""

from __future__ import annotations

from uuid import UUID

from app.llm.models import TokenUsage
from app.models.common import MotilyModel
from app.models.research import ResearchPackage

RESEARCH_R1_ARTIFACT_TYPE = "research_r1"


class R1ResearchInput(MotilyModel):
    project_id: UUID
    additional_context: str | None = None


class R1ResearchResult(MotilyModel):
    research: ResearchPackage
    module_run_id: UUID
    generation_attempts: int
    provider: str
    model: str
    token_usage: TokenUsage | None = None
    retrieval_query_count: int
    retrieved_source_count: int
    business_correction_used: bool
