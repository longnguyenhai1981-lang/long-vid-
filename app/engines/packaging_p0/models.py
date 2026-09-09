"""Packaging P0 Engine input/output contracts.

PackagingPrototype remains the single approved business output -- these are
thin wrappers around it, not a competing schema.
"""

from __future__ import annotations

from uuid import UUID

from app.llm.models import TokenUsage
from app.models.common import MotilyModel
from app.models.packaging import PackagingPrototype

PACKAGING_PROTOTYPE_ARTIFACT_TYPE = "packaging_prototype"


class PackagingP0Input(MotilyModel):
    project_id: UUID
    additional_context: str | None = None


class PackagingP0Result(MotilyModel):
    packaging: PackagingPrototype
    module_run_id: UUID
    generation_attempts: int
    provider: str
    model: str
    token_usage: TokenUsage | None = None
