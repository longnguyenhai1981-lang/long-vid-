from __future__ import annotations

from app.config.loader import load_global_config
from app.engines.feasibility.prompt import build_system_prompt, build_user_prompt
from app.models.common import GateEvaluation, PrimaryPayoff
from app.models.idea import ABT, IdeaCandidate
from app.models.research import ResearchR0
from uuid import uuid4


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


def _research(idea_id) -> ResearchR0:
    return ResearchR0(
        idea_id=idea_id,
        topic_valid=True,
        credible_sources_available=True,
        story_material_available=True,
        physics_material_available=True,
        initial_findings=["Flutter is a documented failure mode"],
        candidate_sources=["https://real.example/source"],
        major_risks=["Risk of conflating flutter with resonance"],
        recommendation="CONTINUE",
    )


def test_system_prompt_includes_audience_range():
    prompt = build_system_prompt(_global_config())
    assert "16" in prompt and "30" in prompt


def test_system_prompt_includes_five_exact_axes():
    prompt = build_system_prompt(_global_config())
    for axis in ("AUDIENCE", "SCIENCE", "NARRATIVE", "VISUAL", "PRODUCTION"):
        assert axis in prompt


def test_system_prompt_includes_production_constraints():
    prompt = build_system_prompt(_global_config())
    assert "8" in prompt and "10" in prompt
    assert "CapCut" in prompt


def test_system_prompt_includes_physics_gravity_rule():
    prompt = build_system_prompt(_global_config()).lower()
    assert "physics must create or resolve" in prompt


def test_system_prompt_forbids_deep_research():
    prompt = build_system_prompt(_global_config()).lower()
    assert "deep research" in prompt or "not perform any deeper research" in prompt


def test_system_prompt_forbids_script_and_narrative_outline():
    prompt = build_system_prompt(_global_config()).lower()
    assert "script" in prompt
    assert "narrative outline" in prompt


def test_system_prompt_forbids_visual_generation():
    prompt = build_system_prompt(_global_config()).lower()
    assert "image prompts" in prompt or "storyboard" in prompt


def test_user_prompt_includes_r0_evidence():
    idea = _idea()
    prompt = build_user_prompt(idea, _research(idea.id), additional_context=None)
    assert "topic_valid" in prompt
    assert "credible_sources_available" in prompt
    assert "https://real.example/source" in prompt
    assert "CONTINUE" in prompt


def test_user_prompt_includes_additional_context_when_supplied():
    idea = _idea()
    prompt = build_user_prompt(idea, _research(idea.id), additional_context="Focus on engineering history.")
    assert "Focus on engineering history." in prompt
