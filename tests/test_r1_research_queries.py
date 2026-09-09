from __future__ import annotations

from app.engines.research_r1.queries import MAX_R1_SEARCH_QUERIES, build_search_queries
from app.models.common import GateEvaluation, PrimaryPayoff
from app.models.idea import ABT, IdeaCandidate
from app.models.research import ResearchR0


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


def _research_r0(idea_id, **overrides) -> ResearchR0:
    kwargs = dict(
        idea_id=idea_id,
        topic_valid=True,
        credible_sources_available=True,
        story_material_available=True,
        physics_material_available=True,
        recommendation="CONTINUE",
    )
    kwargs.update(overrides)
    return ResearchR0(**kwargs)


def test_core_and_authoritative_queries_always_present():
    idea = _idea()
    queries = [q.query for q in build_search_queries(idea, _research_r0(idea.id), _dummy_feasibility())]
    assert idea.central_question in queries
    assert idea.physics_core in queries
    assert any("original paper" in q for q in queries)


def _dummy_feasibility():
    from app.models.feasibility import FeasibilityReport, ProductionEvaluation, SubEvaluation

    return FeasibilityReport(
        status="PASS",
        audience=SubEvaluation(status="PASS", reason="r"),
        science=SubEvaluation(status="PASS", reason="r"),
        narrative=SubEvaluation(status="PASS", reason="r"),
        visual=SubEvaluation(status="PASS", reason="r"),
        production=ProductionEvaluation(status="PASS", estimated_complexity="LOW", reason="r"),
    )


def test_query_count_never_exceeds_max():
    idea = _idea(
        research_questions=[f"Open question {i}" for i in range(10)],
    )
    research_r0 = _research_r0(idea.id, major_risks=[f"Risk {i}" for i in range(10)])
    queries = build_search_queries(idea, research_r0, _dummy_feasibility())
    assert len(queries) <= MAX_R1_SEARCH_QUERIES
    assert len(queries) == MAX_R1_SEARCH_QUERIES


def test_dispute_queries_only_when_r0_has_risks():
    idea = _idea()
    no_risk_queries = [q.query for q in build_search_queries(idea, _research_r0(idea.id), _dummy_feasibility())]
    assert not any("controversy" in q for q in no_risk_queries)

    with_risk = _research_r0(idea.id, major_risks=["Possible confusion with mechanical resonance"])
    with_risk_queries = [q.query for q in build_search_queries(idea, with_risk, _dummy_feasibility())]
    assert any("controversy" in q for q in with_risk_queries)


def test_misconception_query_only_for_reversal_payoff():
    reversal_idea = _idea(primary_payoff=PrimaryPayoff.REVERSAL)
    reversal_queries = [
        q.query for q in build_search_queries(reversal_idea, _research_r0(reversal_idea.id), _dummy_feasibility())
    ]
    assert any("misconception" in q for q in reversal_queries)

    explanation_idea = _idea(primary_payoff=PrimaryPayoff.EXPLANATION)
    explanation_queries = [
        q.query
        for q in build_search_queries(explanation_idea, _research_r0(explanation_idea.id), _dummy_feasibility())
    ]
    assert not any("misconception" in q for q in explanation_queries)


def test_research_questions_included_when_budget_allows():
    idea = _idea(research_questions=["What triggers self-excited flutter specifically?"])
    queries = [q.query for q in build_search_queries(idea, _research_r0(idea.id), _dummy_feasibility())]
    assert "What triggers self-excited flutter specifically?" in queries


def test_duplicate_text_is_deduplicated():
    idea = _idea(research_questions=["Self-excited aeroelastic flutter"])  # duplicates physics_core
    queries = [q.query for q in build_search_queries(idea, _research_r0(idea.id), _dummy_feasibility())]
    assert queries.count("Self-excited aeroelastic flutter") == 1


def test_queries_are_deterministic_and_pure():
    idea = _idea()
    research_r0 = _research_r0(idea.id, major_risks=["Some risk"])
    first = [q.query for q in build_search_queries(idea, research_r0, _dummy_feasibility())]
    second = [q.query for q in build_search_queries(idea, research_r0, _dummy_feasibility())]
    assert first == second
