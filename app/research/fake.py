"""A deterministic fake research retriever for tests. No network calls, ever."""

from __future__ import annotations

from collections.abc import Sequence

from app.research.errors import ResearchRetrieverError
from app.research.models import ResearchQuery, ResearchSearchResponse


class FakeResearchRetriever:
    """Returns/raises a fixed sequence of ResearchSearchResponse/Exception values, in
    order. Records every ResearchQuery it receives."""

    def __init__(self, responses: Sequence[ResearchSearchResponse | Exception]):
        self._responses = list(responses)
        self.received_queries: list[ResearchQuery] = []

    def search(self, query: ResearchQuery) -> ResearchSearchResponse:
        self.received_queries.append(query)
        call_number = len(self.received_queries)
        if call_number > len(self._responses):
            raise ResearchRetrieverError(
                f"FakeResearchRetriever exhausted: no response configured for call {call_number}"
            )
        item = self._responses[call_number - 1]
        if isinstance(item, Exception):
            raise item
        return item

    @property
    def call_count(self) -> int:
        return len(self.received_queries)
