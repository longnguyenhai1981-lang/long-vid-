from __future__ import annotations

from app.config.loader import load_global_config
from app.engines.script_verification.prompt import (
    build_correction_request,
    build_system_prompt,
    build_user_prompt,
)
from app.llm.models import LLMRequest
from app.models.narrative import SCQA, NarrativePlan, QuestionLadderNode
from app.models.packaging import PackagingPrototype
from app.models.research import Claim, ResearchPackage
from app.models.script import ScriptBeat, ScriptLine, ScriptPlan


def _global_config():
    return load_global_config()


def _research_package() -> ResearchPackage:
    return ResearchPackage(
        central_question="Why did a sturdy bridge collapse in mild wind?",
        executive_summary="Flutter caused the collapse.",
        physics_core="Self-excited aeroelastic flutter",
        simplification_boundary="boundary text",
        claims=[Claim(claim_id="C001", claim="Flutter is self-excited", status="SAFE", confidence="HIGH")],
        disputed_points=["Exact onset wind speed is debated"],
        misconceptions=["It was resonance, not flutter"],
        prohibited_claims=["The bridge was intentionally sabotaged"],
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


def _line(line_id="L001", claim_ids=None, function="INFORM", text="Tí kể chuyện.") -> ScriptLine:
    return ScriptLine(line_id=line_id, text=text, function=function, claim_ids=claim_ids or [])


def _beat(beat_id="B001", narrative_node="Q0", lines=None) -> ScriptBeat:
    return ScriptBeat(
        beat_id=beat_id,
        narrative_node=narrative_node,
        narrative_function="INFORM",
        lines=lines if lines is not None else [_line()],
    )


def _script_plan(beats=None) -> ScriptPlan:
    return ScriptPlan(
        estimated_duration_seconds=480,
        beats=beats if beats is not None else [_beat()],
        qa_status="PASS",
    )


def _system_prompt() -> str:
    return build_system_prompt(_global_config(), _research_package(), _narrative_plan(), _packaging())


# ---------------------------------------------------------------------------
# System prompt: verifier framing
# ---------------------------------------------------------------------------


def test_declares_verifier_not_writer():
    prompt = _system_prompt()
    assert "NOT the script writer" in prompt
    assert "never rewrite the script" in prompt


def test_states_research_package_is_factual_source_of_truth():
    prompt = _system_prompt()
    assert "ResearchPackage is the factual source of truth" in prompt


def test_no_mechanical_inform_requires_claim_rule():
    prompt = _system_prompt()
    assert "Do not apply a mechanical rule" in prompt


# ---------------------------------------------------------------------------
# System prompt: claim status rules
# ---------------------------------------------------------------------------


def test_states_all_five_claim_status_rules():
    prompt = _system_prompt()
    for status in ("SAFE", "QUALIFIED", "UNCERTAIN", "DISPUTED", "PROHIBITED"):
        assert status in prompt


def test_disputed_example_present():
    prompt = _system_prompt()
    assert "Đây chính xác là nguyên nhân" in prompt
    assert "vẫn còn tranh luận" in prompt


def test_qualified_overstatement_example_present():
    prompt = _system_prompt().lower()
    assert "always happens" in prompt


# ---------------------------------------------------------------------------
# System prompt: simplification and incompleteness
# ---------------------------------------------------------------------------


def test_includes_simplification_boundary_content():
    prompt = _system_prompt()
    assert "simplification_boundary: boundary text" in prompt


def test_names_dangerous_simplification_types():
    prompt = _system_prompt().lower()
    assert "reverse causal direction" in prompt
    assert "false analogy" in prompt


def test_strategic_incompleteness_is_allowed():
    prompt = _system_prompt()
    assert "STRATEGIC INCOMPLETENESS IS ALLOWED" in prompt
    assert "incomplete but safe" in prompt


# ---------------------------------------------------------------------------
# System prompt: narrative and packaging alignment
# ---------------------------------------------------------------------------


def test_narrative_alignment_flags_only_major_divergence():
    prompt = _system_prompt()
    assert "MAJOR divergence" in prompt
    assert "Do not reject minor wording or rhythm changes" in prompt


def test_verifier_does_not_choose_routing():
    prompt = _system_prompt()
    assert "you do not choose how the project routes" in prompt


def test_packaging_promise_delivery_checklist_present():
    prompt = _system_prompt()
    assert "pays off PackagingPrototype.promise" in prompt
    assert "viewer_expectation" in prompt
    assert "thumbnail_conflict" in prompt


# ---------------------------------------------------------------------------
# System prompt: line-id convention and status
# ---------------------------------------------------------------------------


def test_line_id_reference_convention_documented():
    prompt = _system_prompt()
    assert "L014: ..." in prompt
    assert "never invent one" in prompt


def test_status_setting_instructions_present():
    prompt = _system_prompt()
    assert "Set status to PASS only if you found no material issue" in prompt


# ---------------------------------------------------------------------------
# System prompt: injected artifact content
# ---------------------------------------------------------------------------


def test_includes_research_package_claims_disputed_and_misconceptions():
    prompt = _system_prompt()
    assert "C001" in prompt
    assert "Exact onset wind speed is debated" in prompt
    assert "It was resonance, not flutter" in prompt
    assert "The bridge was intentionally sabotaged" in prompt


def test_includes_narrative_plan_central_question_and_ending():
    prompt = _system_prompt()
    assert "Why did a sturdy bridge collapse in mild wind?" in prompt
    assert "Callback to the opening image" in prompt


def test_includes_packaging_promise_and_title_direction():
    prompt = _system_prompt()
    assert "A sturdy bridge tore itself apart in ordinary wind" in prompt
    assert "The bridge that shook itself to pieces" in prompt


def test_output_format_includes_json_schema():
    prompt = _system_prompt()
    assert "OUTPUT FORMAT" in prompt
    assert '"unsupported_lines"' in prompt


# ---------------------------------------------------------------------------
# User prompt
# ---------------------------------------------------------------------------


def test_user_prompt_lists_beats_and_lines():
    plan = _script_plan(beats=[_beat("B001", "Q0", lines=[_line("L001", claim_ids=["C001"], text="Câu đầu tiên.")])])
    prompt = build_user_prompt(plan, set(), None)
    assert "B001" in prompt
    assert "L001" in prompt
    assert "Câu đầu tiên." in prompt
    assert "['C001']" in prompt


def test_user_prompt_includes_deterministic_bad_line_ids_when_present():
    plan = _script_plan()
    prompt = build_user_prompt(plan, {"L001"}, None)
    assert "deterministic pre-check" in prompt
    assert "L001" in prompt


def test_user_prompt_omits_deterministic_block_when_absent():
    plan = _script_plan()
    prompt = build_user_prompt(plan, set(), None)
    assert "deterministic pre-check" not in prompt


def test_user_prompt_includes_additional_context_when_supplied():
    plan = _script_plan()
    prompt = build_user_prompt(plan, set(), "Double check the bridge-collapse claim.")
    assert "Double check the bridge-collapse claim." in prompt


def test_user_prompt_omits_additional_context_when_absent():
    plan = _script_plan()
    prompt = build_user_prompt(plan, set(), None)
    assert "Additional context" not in prompt


# ---------------------------------------------------------------------------
# Correction request
# ---------------------------------------------------------------------------


def test_correction_request_names_unacknowledged_line_ids():
    original = LLMRequest(
        system_prompt="system", user_prompt="original task text", model="fake-model"
    )
    corrected = build_correction_request(original, ["L001", "L002"])
    assert "L001" in corrected.user_prompt
    assert "L002" in corrected.user_prompt


def test_correction_request_preserves_original_task():
    original = LLMRequest(
        system_prompt="system", user_prompt="original task text", model="fake-model"
    )
    corrected = build_correction_request(original, ["L001"])
    assert "original task text" in corrected.user_prompt


def test_correction_request_preserves_system_prompt():
    original = LLMRequest(
        system_prompt="system prompt text", user_prompt="original task text", model="fake-model"
    )
    corrected = build_correction_request(original, ["L001"])
    assert corrected.system_prompt == "system prompt text"
