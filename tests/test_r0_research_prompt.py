from __future__ import annotations

from app.engines.research_r0.prompt import build_system_prompt, build_user_prompt
from app.models.common import GateEvaluation, PrimaryPayoff
from app.models.idea import ABT, IdeaCandidate
from app.research.models import RetrievedSource


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


def test_system_prompt_disclaims_final_truth():
    prompt = build_system_prompt(_idea()).lower()
    assert "not establishing final factual truth" in prompt
    assert "not" in prompt and "exhaustive" in prompt


def test_system_prompt_forbids_inventing_sources():
    prompt = build_system_prompt(_idea()).lower()
    assert "do not invent" in prompt or "not invent" in prompt


def test_system_prompt_includes_idea_id_instruction():
    idea = _idea()
    prompt = build_system_prompt(idea)
    assert str(idea.id) in prompt


def test_system_prompt_forbids_narrative_and_script_content():
    prompt = build_system_prompt(_idea()).lower()
    assert "script" in prompt
    assert "narrative structure" in prompt


def test_user_prompt_lists_retrieved_sources():
    evidence = [RetrievedSource(title="Nature paper", url="https://example.com/paper")]
    prompt = build_user_prompt(evidence, additional_context=None)
    assert "Nature paper" in prompt
    assert "https://example.com/paper" in prompt


def test_user_prompt_handles_empty_evidence_honestly():
    prompt = build_user_prompt([], additional_context=None).lower()
    assert "no sources were found" in prompt


def test_user_prompt_includes_additional_context_when_supplied():
    prompt = build_user_prompt([], additional_context="Focus on engineering history.")
    assert "Focus on engineering history." in prompt
