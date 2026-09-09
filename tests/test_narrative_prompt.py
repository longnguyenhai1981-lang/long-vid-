from __future__ import annotations

from app.config.loader import load_global_config
from app.engines.narrative.prompt import build_system_prompt, build_user_prompt
from app.models.common import GateEvaluation, PrimaryPayoff
from app.models.idea import ABT, IdeaCandidate
from app.models.research import Claim, ResearchPackage


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


def _research_package() -> ResearchPackage:
    return ResearchPackage(
        central_question="Why did a sturdy bridge collapse in mild wind?",
        executive_summary="Flutter caused the collapse.",
        physics_core="Self-excited aeroelastic flutter",
        simplification_boundary=(
            "1. safe_model: ... 2. allowed_simplifications: ... "
            "3. omitted_complexity: ... 4. dangerous_oversimplifications: ..."
        ),
        claims=[Claim(claim_id="C001", claim="Flutter is self-excited", status="SAFE", confidence="HIGH")],
    )


def _system_prompt() -> str:
    return build_system_prompt(_global_config(), _idea(), _research_package())


def test_mentions_scqa_framework():
    prompt = _system_prompt()
    assert "SCQA" in prompt
    assert "situation" in prompt.lower()


def test_mentions_question_ladder():
    assert "question ladder" in _system_prompt().lower()


def test_mentions_information_gap():
    assert "information gap" in _system_prompt().lower()


def test_mentions_just_in_time_explanation():
    assert "just-in-time" in _system_prompt().lower()


def test_states_medium_physics_depth():
    assert "MEDIUM" in _system_prompt()


def test_describes_ti_as_investigator():
    prompt = _system_prompt().lower()
    assert "investigator" in prompt
    assert "tí" in prompt


def test_prefers_callback_ending():
    assert "callback" in _system_prompt().lower()


def test_declares_research_package_factual_ownership():
    prompt = _system_prompt().lower()
    assert "factual" in prompt
    assert "trace back to a claim_id" in prompt


def test_includes_simplification_boundary_content():
    prompt = _system_prompt()
    assert "safe_model" in prompt or "simplification_boundary" in prompt.lower()
    assert "reverse causality" in prompt.lower()


def test_explains_claim_status_caution():
    prompt = _system_prompt()
    for status in ("SAFE", "QUALIFIED", "DISPUTED", "UNCERTAIN", "PROHIBITED"):
        assert status in prompt


def test_forbids_script_packaging_and_visuals():
    prompt = _system_prompt().lower()
    assert "script" in prompt
    assert "packaging" in prompt
    assert "visual" in prompt


def test_user_prompt_includes_additional_context_when_supplied():
    prompt = build_user_prompt("Focus on the engineering timeline.")
    assert "Focus on the engineering timeline." in prompt


def test_user_prompt_omits_additional_context_when_absent():
    prompt = build_user_prompt(None)
    assert "Additional context" not in prompt
