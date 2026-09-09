"""Idea Engine input/output contracts.

IdeaCandidate itself remains the single approved business output -- these
are thin wrappers around it, not a competing schema.
"""

from __future__ import annotations

from enum import Enum
from uuid import UUID

from pydantic import model_validator

from app.llm.models import TokenUsage
from app.models.common import MotilyModel, non_blank
from app.models.idea import IdeaCandidate

IDEA_CANDIDATE_ARTIFACT_TYPE = "idea_candidate"


class DiscoveryMode(str, Enum):
    OPEN = "OPEN"
    EXPAND = "EXPAND"


class IdeaEngineInput(MotilyModel):
    project_id: UUID
    seed: str | None = None
    domain: str = "physics"
    discovery_mode: DiscoveryMode
    additional_context: str | None = None

    @model_validator(mode="after")
    def _check_invariants(self) -> "IdeaEngineInput":
        non_blank(self.domain, "domain")
        if self.discovery_mode is DiscoveryMode.EXPAND and (
            self.seed is None or not self.seed.strip()
        ):
            raise ValueError("seed must be non-blank when discovery_mode is EXPAND")
        return self


class IdeaEngineResult(MotilyModel):
    idea: IdeaCandidate
    module_run_id: UUID
    generation_attempts: int
    provider: str
    model: str
    token_usage: TokenUsage | None = None
