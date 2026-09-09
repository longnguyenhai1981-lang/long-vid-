"""Provider-independent research retrieval contracts.

Retrieval reports what was found; it does not classify evidence quality --
that judgment belongs to the research engine consuming these results.
"""

from __future__ import annotations

from datetime import datetime

from pydantic import Field, model_validator

from app.models.common import MotilyModel, non_blank


class ResearchQuery(MotilyModel):
    query: str
    max_results: int = 5

    @model_validator(mode="after")
    def _check_invariants(self) -> "ResearchQuery":
        non_blank(self.query, "query")
        if self.max_results <= 0:
            raise ValueError("max_results must be > 0")
        return self


class RetrievedSource(MotilyModel):
    title: str
    url: str
    snippet: str | None = None
    source_name: str | None = None
    published_at: datetime | None = None
    metadata: dict[str, str] = Field(default_factory=dict)

    @model_validator(mode="after")
    def _check_invariants(self) -> "RetrievedSource":
        non_blank(self.title, "title")
        non_blank(self.url, "url")
        if self.published_at is not None and self.published_at.tzinfo is None:
            raise ValueError("published_at must be timezone-aware")
        return self


class ResearchSearchResponse(MotilyModel):
    query: ResearchQuery
    results: list[RetrievedSource] = Field(default_factory=list)
