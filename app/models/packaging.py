"""PackagingPrototype: title/thumbnail promise interface (P0). No generation logic."""

from __future__ import annotations

from uuid import UUID, uuid4

from pydantic import Field

from app.models.common import MotilyModel, RiskLevel


class PackagingPrototype(MotilyModel):
    id: UUID = Field(default_factory=uuid4)
    promise: str
    title_direction: str
    thumbnail_conflict: str
    viewer_expectation: str
    risk_of_misleading: RiskLevel
