from __future__ import annotations

from app.config.loader import load_global_config
from app.engines.research_r1.prompt import build_system_prompt, build_user_prompt
from app.models.common import GateEvaluation, PrimaryPayoff
from app.models.feasibility import FeasibilityReport, ProductionEvaluation, SubEvaluation
from app.models.idea import ABT, IdeaCandidate
from app.models.research import ResearchR0
from app.research.models import RetrievedSource


def _global_config():
    return load_global_config()


def _idea() -> IdeaCandidate:
    return IdeaCandidate(
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


def _research_r0(idea_id) -> ResearchR0:
    return ResearchR0(
        idea_id=idea_id,
        topic_valid=True,
        credible_sources_available=True,
        story_material_available=True,
        physics_material_available=True,
        recommendation="CONTINUE",
    )


def _feasibility() -> FeasibilityReport:
    return FeasibilityReport(
        status="PASS",
        audience=SubEvaluation(status="PASS", reason="r"),
        science=SubEvaluation(status="PASS", reason="r"),
        narrative=SubEvaluation(status="PASS", reason="r"),
        visual=SubEvaluation(status="PASS", reason="r"),
        production=ProductionEvaluation(status="PASS", estimated_complexity="LOW", reason="r"),
    )


def _system_prompt() -> str:
    idea = _idea()
    return build_system_prompt(_global_config(), idea, _research_r0(idea.id), _feasibility())


def test_declares_factual_source_of_truth():
    prompt = _system_prompt().lower()
    assert "factual source of truth" in prompt


def test_includes_evidence_hierarchy_tiers():
    prompt = _system_prompt()
    assert "TIER 1" in prompt and "TIER 2" in prompt and "TIER 3" in prompt


def test_states_snippet_only_limitation():
    prompt = _system_prompt().lower()
    assert "do not claim to have read full documents" in prompt or "never full page" in prompt


def test_defines_all_five_claim_statuses():
    prompt = _system_prompt()
    for status in ("SAFE", "QUALIFIED", "UNCERTAIN", "DISPUTED", "PROHIBITED"):
        assert status in prompt


def test_requires_simplification_boundary_structure():
    prompt = _system_prompt().lower()
    assert "safe_model" in prompt
    assert "allowed_simplifications" in prompt
    assert "omitted_complexity" in prompt
    assert "dangerous_oversimplifications" in prompt


def test_warns_against_false_balance():
    prompt = _system_prompt().lower()
    assert "false balance" in prompt


def test_forbids_source_invention():
    prompt = _system_prompt().lower()
    assert "do not invent sources" in prompt


def test_forbids_script_narrative_and_packaging():
    prompt = _system_prompt().lower()
    assert "script" in prompt
    assert "narrative outline" in prompt
    assert "packaging" in prompt


def test_user_prompt_lists_retrieved_sources():
    evidence = [RetrievedSource(title="Nature paper", url="https://example.com/paper")]
    prompt = build_user_prompt(evidence, additional_context=None)
    assert "Nature paper" in prompt
    assert "https://example.com/paper" in prompt


def test_user_prompt_handles_empty_evidence_honestly():
    prompt = build_user_prompt([], additional_context=None).lower()
    assert "no sources were retrieved" in prompt


def test_user_prompt_includes_additional_context_when_supplied():
    prompt = build_user_prompt([], additional_context="Focus on structural engineering.")
    assert "Focus on structural engineering." in prompt
