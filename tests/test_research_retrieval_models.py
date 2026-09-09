from __future__ import annotations

from datetime import datetime, timezone

import pytest
from pydantic import ValidationError

from app.research.errors import ResearchRetrieverError
from app.research.fake import FakeResearchRetriever
from app.research.models import ResearchQuery, ResearchSearchResponse, RetrievedSource


def test_valid_research_query():
    query = ResearchQuery(query="Tacoma Narrows Bridge collapse")
    assert query.max_results == 5


def test_blank_query_rejected():
    with pytest.raises(ValidationError):
        ResearchQuery(query="   ")


def test_non_positive_max_results_rejected():
    with pytest.raises(ValidationError):
        ResearchQuery(query="x", max_results=0)


def test_valid_retrieved_source():
    source = RetrievedSource(title="Nature paper", url="https://example.com/paper")
    assert source.snippet is None


def test_blank_title_rejected():
    with pytest.raises(ValidationError):
        RetrievedSource(title="  ", url="https://example.com")


def test_blank_url_rejected():
    with pytest.raises(ValidationError):
        RetrievedSource(title="Title", url="  ")


def test_naive_published_at_rejected():
    with pytest.raises(ValidationError):
        RetrievedSource(title="Title", url="https://example.com", published_at=datetime.now())


def test_tz_aware_published_at_allowed():
    source = RetrievedSource(
        title="Title", url="https://example.com", published_at=datetime.now(timezone.utc)
    )
    assert source.published_at.tzinfo is not None


def test_research_search_response_round_trip():
    query = ResearchQuery(query="physics of bridges")
    response = ResearchSearchResponse(
        query=query,
        results=[RetrievedSource(title="A", url="https://a.example")],
    )
    dumped = response.model_dump_json()
    restored = ResearchSearchResponse.model_validate_json(dumped)
    assert restored == response


def test_fake_research_retriever_returns_responses_in_sequence():
    r1 = ResearchSearchResponse(query=ResearchQuery(query="q1"), results=[])
    r2 = ResearchSearchResponse(query=ResearchQuery(query="q2"), results=[])
    retriever = FakeResearchRetriever([r1, r2])

    assert retriever.search(ResearchQuery(query="q1")) is r1
    assert retriever.search(ResearchQuery(query="q2")) is r2
    assert retriever.call_count == 2
    assert [q.query for q in retriever.received_queries] == ["q1", "q2"]


def test_fake_research_retriever_raises_configured_exception():
    retriever = FakeResearchRetriever([ResearchRetrieverError("network down")])
    with pytest.raises(ResearchRetrieverError, match="network down"):
        retriever.search(ResearchQuery(query="q1"))


def test_fake_research_retriever_exhaustion_raises():
    retriever = FakeResearchRetriever([ResearchSearchResponse(query=ResearchQuery(query="q1"), results=[])])
    retriever.search(ResearchQuery(query="q1"))
    with pytest.raises(ResearchRetrieverError):
        retriever.search(ResearchQuery(query="q2"))
