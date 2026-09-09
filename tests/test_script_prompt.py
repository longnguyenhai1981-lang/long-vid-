from __future__ import annotations

from app.config.loader import load_global_config
from app.engines.script.prompt import build_system_prompt, build_user_prompt
from app.models.common import GateEvaluation, PrimaryPayoff
from app.models.idea import ABT, IdeaCandidate
from app.models.narrative import SCQA, NarrativePlan, QuestionLadderNode
from app.models.packaging import PackagingPrototype
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
        scqa=SCQA(situation="s", complication="c", question="q", answer="Self-excited flutter"),
        opening="MYSTERY_FIRST",
        question_ladder=[
            QuestionLadderNode(
                id="Q0", question="Why?", why_viewer_cares="matters", partial_answer="flutter",
                claim_ids=["C001"], creates_next_question=None, information_gap="none",
            )
        ],
        ti_role="Investigator",
        ending="Callback to the opening image",
    )


def _packaging() -> PackagingPrototype:
    return PackagingPrototype(
        promise="A sturdy bridge tore itself apart in ordinary wind",
        title_direction="The bridge that shook itself to pieces",
        thumbnail_conflict="CAN WIND REALLY DO THIS?",
        viewer_expectation="An investigation into a real aerodynamic mechanism",
        risk_of_misleading="LOW",
    )


def _system_prompt() -> str:
    return build_system_prompt(_global_config(), _idea(), _research_package(), _narrative_plan(), _packaging())


def test_declares_ti_conversational_voice():
    prompt = _system_prompt()
    assert "conversational character voice" in prompt.lower()
    assert "Tí vừa nghĩ ra" in prompt


def test_requires_direct_ban_address():
    prompt = _system_prompt()
    assert '"bạn"' in prompt
    assert "mọi người" in prompt  # named explicitly as what to avoid


def test_requires_short_spoken_sentences():
    prompt = _system_prompt().lower()
    assert "short spoken sentences" in prompt


def test_states_story_architecture_belongs_to_narrative_plan():
    prompt = _system_prompt().lower()
    assert "story architecture belongs to narrativeplan" in prompt


def test_states_facts_belong_to_research_package():
    prompt = _system_prompt().lower()
    assert "research truth belongs to researchpackage" in prompt


def test_requires_packaging_promise_payoff():
    prompt = _system_prompt().lower()
    assert "must pay off the approved packaging promise" in prompt


def test_mentions_just_in_time_explanation():
    assert "just-in-time explanation" in _system_prompt().lower()


def test_states_medium_physics_depth():
    assert "MEDIUM" in _system_prompt()


def test_includes_simplification_boundary_content():
    prompt = _system_prompt()
    assert "simplification_boundary: boundary text" in prompt


def test_states_all_five_claim_status_rules():
    prompt = _system_prompt()
    for status in ("SAFE", "QUALIFIED", "UNCERTAIN", "DISPUTED", "PROHIBITED"):
        assert status in prompt


def test_mentions_micro_hooks():
    prompt = _system_prompt()
    assert "MICRO-HOOKS" in prompt
    assert "CONTRADICTION" in prompt


def test_allows_light_censored_profanity_only():
    prompt = _system_prompt().lower()
    assert "light" in prompt and "censored" in prompt
    assert "no excessive profanity" in prompt


def test_requires_tts_first_writing():
    prompt = _system_prompt().lower()
    assert "tts-first writing" in prompt


def test_forbids_storyboard_and_visual_production():
    prompt = _system_prompt().lower()
    assert "storyboard" in prompt
    assert "future visual module" in prompt


def test_forbids_voice_delivery_plan():
    prompt = _system_prompt().lower()
    assert "voice/delivery timing plan" in prompt


def test_states_target_duration_8_to_10_minutes():
    prompt = _system_prompt()
    assert "8-10 minutes" in prompt
    assert "480" in prompt and "600" in prompt


def test_user_prompt_includes_additional_context_when_supplied():
    prompt = build_user_prompt("Lean into the engineering mystery angle.")
    assert "Lean into the engineering mystery angle." in prompt


def test_user_prompt_omits_additional_context_when_absent():
    prompt = build_user_prompt(None)
    assert "Additional context" not in prompt
