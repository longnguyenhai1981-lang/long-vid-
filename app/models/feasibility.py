"""FeasibilityReport: go/no-go/reframe evaluation across pipeline dimensions."""

from __future__ import annotations

from uuid import UUID, uuid4

from pydantic import Field

from app.models.common import GateStatus, MotilyModel


class SubEvaluation(MotilyModel):
    status: GateStatus
    reason: str


class ProductionEvaluation(MotilyModel):
    status: GateStatus
    estimated_complexity: str
    reason: str


class FeasibilityReport(MotilyModel):
    id: UUID = Field(default_factory=uuid4)
    status: GateStatus
    audience: SubEvaluation
    science: SubEvaluation
    narrative: SubEvaluation
    visual: SubEvaluation
    production: ProductionEvaluation
    likely_reusable_assets: list[str] = Field(default_factory=list)
    likely_expensive_scenes: list[str] = Field(default_factory=list)
    suggested_reframes: list[str] = Field(default_factory=list)
