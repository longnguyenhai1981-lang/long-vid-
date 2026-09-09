"""Research contracts: R0 screening and the R1 ResearchPackage with claims/sources."""

from __future__ import annotations

from typing import Literal
from uuid import UUID, uuid4

from pydantic import Field, model_validator

from app.models.common import (
    ClaimStatus,
    Confidence,
    MotilyModel,
    ResearchRecommendation,
    non_blank,
)


class ResearchR0(MotilyModel):
    id: UUID = Field(default_factory=uuid4)
    idea_id: UUID
    topic_valid: bool
    credible_sources_available: bool
    story_material_available: bool
    physics_material_available: bool
    initial_findings: list[str] = Field(default_factory=list)
    candidate_sources: list[str] = Field(default_factory=list)
    major_risks: list[str] = Field(default_factory=list)
    recommendation: ResearchRecommendation


class Claim(MotilyModel):
    claim_id: str
    claim: str
    status: ClaimStatus
    confidence: Confidence
    source_ids: list[str] = Field(default_factory=list)
    qualification: str | None = None
    notes: str | None = None

    @model_validator(mode="after")
    def _check_required_text(self) -> "Claim":
        non_blank(self.claim_id, "claim_id")
        non_blank(self.claim, "claim")
        return self


class Source(MotilyModel):
    source_id: str
    title: str
    url: str | None = None
    type: str
    quality_tier: Literal[1, 2, 3]
    authoritative: bool
    supports_claims: list[str] = Field(default_factory=list)

    @model_validator(mode="after")
    def _check_required_text(self) -> "Source":
        non_blank(self.source_id, "source_id")
        non_blank(self.title, "title")
        return self


class ResearchPackage(MotilyModel):
    """The R1 research contract: everything downstream stages draw claims from."""

    id: UUID = Field(default_factory=uuid4)
    central_question: str
    executive_summary: str
    timeline: list[str] = Field(default_factory=list)
    physics_core: str
    claims: list[Claim] = Field(default_factory=list)
    disputed_points: list[str] = Field(default_factory=list)
    misconceptions: list[str] = Field(default_factory=list)
    simplification_boundary: str
    prohibited_claims: list[str] = Field(default_factory=list)
    sources: list[Source] = Field(default_factory=list)

    @model_validator(mode="after")
    def _check_required_text(self) -> "ResearchPackage":
        non_blank(self.central_question, "central_question")
        return self
