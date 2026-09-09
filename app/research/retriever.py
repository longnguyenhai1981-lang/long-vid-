"""The provider-independent research retrieval interface. No Google/Bing/Tavily/
SerpAPI/browser call ever appears above this boundary."""

from __future__ import annotations

from typing import Protocol, runtime_checkable

from app.research.models import ResearchQuery, ResearchSearchResponse


@runtime_checkable
class ResearchRetriever(Protocol):
    def search(self, query: ResearchQuery) -> ResearchSearchResponse:
        ...
