from __future__ import annotations

from app.engines.research_r0.queries import MAX_R0_SEARCH_QUERIES, build_search_queries
from app.models.common import GateEvaluation, PrimaryPayoff
from app.models.idea import ABT, IdeaCandidate


def _idea(**overrides) -> IdeaCandidate:
    kwargs = dict(
        topic="Tacoma Narrows Bridge",
        central_question="Why did a sturdy bridge collapse in mild wind?",
        abt=ABT(
            and_context="Engineers believed the bridge was safe",
            but_complication="It oscillated violently and collapsed in moderate wind",
            therefore_investigation="Investigate the hidden aerodynamic mechanism",
        ),
        primary_payoff=PrimaryPayoff.REVERSAL,
        physics_core="Self-excited aeroelastic flutter",
        audience_prerequisite="none",
        brand_fit=GateEvaluation(status="PASS", reason="ok"),
        general_audience_gate=GateEvaluation(status="PASS", reason="ok"),
        longform_potential=GateEvaluation(status="PASS", reason="ok"),
    )
    kwargs.update(overrides)
    return IdeaCandidate(**kwargs)


def test_minimum_queries_cover_topic_central_question_and_physics():
    queries = build_search_queries(_idea())
    texts = [q.query for q in queries]
    assert "Tacoma Narrows Bridge" in texts
    assert "Why did a sturdy bridge collapse in mild wind?" in texts
    assert "Self-excited aeroelastic flutter" in texts


def test_query_count_never_exceeds_max():
    idea = _idea(
        research_questions=[
            "How does flutter differ from resonance?",
            "What wind speed triggered the collapse?",
            "Were there earlier warning signs?",
            "How do modern bridges avoid this?",
            "What is the Tacoma Narrows Bridge replacement design?",
        ]
    )
    queries = build_search_queries(idea)
    assert len(queries) <= MAX_R0_SEARCH_QUERIES
    assert len(queries) == MAX_R0_SEARCH_QUERIES


def test_research_questions_fill_remaining_budget_in_order():
    idea = _idea(
        research_questions=[
            "How does flutter differ from resonance?",
            "What wind speed triggered the collapse?",
        ]
    )
    queries = [q.query for q in build_search_queries(idea)]
    assert queries[:3] == [idea.topic, idea.central_question, idea.physics_core]
    assert queries[3] == "How does flutter differ from resonance?"
    assert "What wind speed triggered the collapse?" not in queries


def test_duplicate_text_is_deduplicated():
    idea = _idea(central_question="Tacoma Narrows Bridge")
    queries = [q.query for q in build_search_queries(idea)]
    assert queries.count("Tacoma Narrows Bridge") == 1


def test_no_research_questions_still_produces_three_queries():
    queries = build_search_queries(_idea())
    assert len(queries) == 3


def test_queries_are_deterministic_and_pure():
    idea = _idea()
    first = [q.query for q in build_search_queries(idea)]
    second = [q.query for q in build_search_queries(idea)]
    assert first == second
