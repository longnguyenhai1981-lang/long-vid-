from __future__ import annotations

from app.config.loader import load_global_config
from app.engines.packaging_p0.prompt import build_system_prompt, build_user_prompt
from app.models.common import GateEvaluation, PrimaryPayoff
from app.models.idea import ABT, IdeaCandidate
from app.models.narrative import SCQA, NarrativePlan, QuestionLadderNode
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
        simplification_boundary="boundary text",
        claims=[Claim(claim_id="C001", claim="Flutter is self-excited", status="SAFE", confidence="HIGH")],
    )


def _narrative_plan() -> NarrativePlan:
    return NarrativePlan(
        central_question="Why did a sturdy bridge collapse in mild wind?",
        scqa=SCQA(situation="s", complication="c", question="q", answer="Self-excited flutter, not resonance"),
        opening="MYSTERY_FIRST",
        question_ladder=[
            QuestionLadderNode(
                id="Q0", question="Why?", why_viewer_cares="matters", partial_answer="flutter",
                claim_ids=["C001"], creates_next_question=None, information_gap="none",
            )
        ],
        ti_role="Investigator",
        ending="Callback to the opening image",
        claim_ids_used=["C001"],
    )


def _system_prompt() -> str:
    return build_system_prompt(_global_config(), _idea(), _research_package(), _narrative_plan())


def test_declares_drama_first_style():
    assert "drama-first" in _system_prompt().lower()


def test_requires_one_recommendation():
    prompt = _system_prompt().lower()
    assert "one recommend" in prompt or "exactly one" in prompt
    assert "a/b/c" in prompt or "not 10 titles" in prompt


def test_states_general_audience():
    prompt = _system_prompt()
    assert "general audience" in prompt.lower()
    assert "16" in prompt and "30" in prompt


def test_requires_central_question_alignment():
    idea = _idea()
    prompt = _system_prompt()
    assert idea.central_question in prompt
    assert "silently sell a different video" in prompt.lower()


def test_states_promise_integrity_rule():
    prompt = _system_prompt().lower()
    assert "promise integrity" in prompt
    assert "stronger than researchpackage supports" in prompt


def test_declares_research_package_factual_ownership():
    prompt = _system_prompt().lower()
    assert "researchpackage (factual source of truth)" in prompt


def test_declares_narrative_plan_payoff_ownership():
    prompt = _system_prompt().lower()
    assert "narrativeplan" in prompt
    assert "payoff you may promise" in prompt


def test_forbids_misleading_framing():
    prompt = _system_prompt().lower()
    assert "misleading" in prompt
    assert "false framing is not" in prompt


def test_requires_title_thumbnail_complementarity():
    prompt = _system_prompt().lower()
    assert "complement" in prompt
    assert "not duplicate" in prompt


def test_does_not_require_final_title_or_thumbnail():
    prompt = _system_prompt().lower()
    assert "do not produce any of the following" in prompt
    assert "final title" in prompt
    assert "final thumbnail" in prompt or "thumbnail image" in prompt


def test_forbids_script_generation():
    assert "script or narrative content" in _system_prompt().lower()


def test_user_prompt_includes_additional_context_when_supplied():
    prompt = build_user_prompt("Lean into the engineering mystery angle.")
    assert "Lean into the engineering mystery angle." in prompt


def test_user_prompt_omits_additional_context_when_absent():
    prompt = build_user_prompt(None)
    assert "Additional context" not in prompt
