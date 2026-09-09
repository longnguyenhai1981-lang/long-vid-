from __future__ import annotations

from uuid import uuid4

from app.config.loader import load_global_config
from app.engines.packaging_p1.prompt import build_correction_request, build_system_prompt, build_user_prompt
from app.llm.models import LLMRequest
from app.models.assembly import AssemblyPlan, AssemblySegment
from app.models.narrative import SCQA, NarrativePlan, QuestionLadderNode
from app.models.packaging import PackagingPrototype
from app.models.research import Claim, ResearchPackage
from app.models.script import ScriptBeat, ScriptLine, ScriptPlan
from app.models.visual import VisualBeat, VisualPlan


def _global_config():
    return load_global_config()


def _packaging_prototype() -> PackagingPrototype:
    return PackagingPrototype(
        promise="A sturdy bridge tore itself apart in ordinary wind",
        title_direction="The bridge that shook itself to pieces",
        thumbnail_conflict="CAN WIND REALLY DO THIS?",
        viewer_expectation="An investigation into a real aerodynamic mechanism",
        risk_of_misleading="LOW",
    )


def _research_package() -> ResearchPackage:
    return ResearchPackage(
        central_question="Why did a sturdy bridge collapse in mild wind?",
        executive_summary="s", physics_core="Flutter",
        simplification_boundary="Do not replace flutter with resonance.",
        claims=[Claim(claim_id="C001", claim="Flutter is self-excited", status="SAFE", confidence="HIGH")],
        disputed_points=["Exact onset wind speed is debated"],
        prohibited_claims=["The bridge was sabotaged"],
    )


def _narrative_plan() -> NarrativePlan:
    return NarrativePlan(
        central_question="Why did a sturdy bridge collapse in mild wind?",
        scqa=SCQA(situation="s", complication="c", question="q", answer="a"),
        opening="MYSTERY_FIRST",
        question_ladder=[
            QuestionLadderNode(id="Q0", question="Why?", why_viewer_cares="matters", partial_answer="flutter", information_gap="none")
        ],
        ti_role="Investigator", ending="Callback to the opening image",
    )


def _script_plan() -> ScriptPlan:
    return ScriptPlan(
        estimated_duration_seconds=480,
        beats=[
            ScriptBeat(
                beat_id="B001", narrative_node="Q0", narrative_function="INFORM",
                lines=[ScriptLine(line_id="L001", text="Cây cầu này từng an toàn.", function="INFORM")],
            ),
            ScriptBeat(
                beat_id="B002", narrative_node="Q0", narrative_function="REVEAL",
                lines=[ScriptLine(line_id="L002", text="Đó là hiện tượng flutter.", function="REVEAL")],
            ),
        ],
        qa_status="PASS",
    )


def _visual_plan() -> VisualPlan:
    return VisualPlan(
        script_plan_id=uuid4(), voice_plan_id=uuid4(),
        beats=[
            VisualBeat(
                beat_id="VB001", script_line_ids=["L001"], narrative_node="Q0",
                visual_level="L1_ESTABLISH", visual_function="STORY", media_type="ASSET_REUSE",
                complexity="C0", concept="Establishing shot.", primary_focus="The bridge",
                reuse_key="bridge_side_view",
            ),
            VisualBeat(
                beat_id="VB002", script_line_ids=["L002"], narrative_node="Q0",
                visual_level="L3_RELATIONSHIP", visual_function="MECHANISM", media_type="DIAGRAM",
                complexity="C2", concept="Flutter diagram.", primary_focus="The twisting deck",
                ti_state="SURPRISED",
            ),
        ],
    )


def _assembly_plan() -> AssemblyPlan:
    return AssemblyPlan(
        script_plan_id=uuid4(), voice_plan_id=uuid4(), visual_plan_id=uuid4(),
        estimated_total_duration_seconds=480.0,
        segments=[
            AssemblySegment(
                segment_id="S001", script_line_ids=["L001"], visual_beat_id="VB001",
                start_seconds=0.0, end_seconds=240.0, music_state="BED",
                transition_in="CUT", transition_out="CUT",
            ),
            AssemblySegment(
                segment_id="S002", script_line_ids=["L002"], visual_beat_id="VB002",
                start_seconds=240.0, end_seconds=480.0, music_state="LIFT",
                transition_in="DISSOLVE", transition_out="CUT",
            ),
        ],
    )


def _system_prompt() -> str:
    return build_system_prompt(
        _global_config(), _packaging_prototype(), _research_package(), _narrative_plan(),
        _script_plan(), _visual_plan(), _assembly_plan(),
    )


# ---------------------------------------------------------------------------
# Role
# ---------------------------------------------------------------------------


def test_declares_final_packaging_editor_role():
    prompt = _system_prompt()
    assert "final packaging editor" in prompt


def test_states_not_inventing_a_better_video():
    prompt = _system_prompt()
    assert "NOT to invent a better video" in prompt
    assert "package THIS video" in prompt


def test_declares_drama_first():
    prompt = _system_prompt()
    assert "drama-first" in prompt


def test_one_final_recommendation_only():
    prompt = _system_prompt()
    assert "ONE FINAL RECOMMENDATION ONLY" in prompt
    assert "Option A/B/C" in prompt


# ---------------------------------------------------------------------------
# Title / thumbnail
# ---------------------------------------------------------------------------


def test_requires_real_final_title_wording():
    prompt = _system_prompt()
    assert "Write real, final Vietnamese title wording now" in prompt


def test_thumbnail_concept_guidance_present():
    prompt = _system_prompt()
    assert "one visual conflict" in prompt.lower()


def test_thumbnail_text_zero_to_four_word_preference():
    prompt = _system_prompt()
    assert "Prefer 0-4 words" in prompt


def test_title_thumbnail_complement_example():
    prompt = _system_prompt()
    assert "must not merely repeat each other" in prompt
    assert "CHỈ VÌ GIÓ?" in prompt


def test_ti_usually_present_but_no_quota():
    prompt = _system_prompt()
    assert "no hard quota" in prompt.lower()


# ---------------------------------------------------------------------------
# Research / promise safety
# ---------------------------------------------------------------------------


def test_research_truth_boundary_stated():
    prompt = _system_prompt()
    assert "RESEARCH SAFETY" in prompt
    assert "Never invent a factual detail" in prompt


def test_packaging_p0_continuity_stated():
    prompt = _system_prompt()
    assert "P0 -> P1 RELATIONSHIP" in prompt
    assert "recognizably connected to P0's" in prompt


def test_script_payoff_delivery_stated():
    prompt = _system_prompt()
    assert "PAYOFF OWNERSHIP" in prompt
    assert "reversal if the script contains no real reversal" in prompt


def test_visual_feasibility_stated():
    prompt = _system_prompt()
    assert "VISUAL FEASIBILITY" in prompt
    assert "Do not invent a completely unrelated visual subject" in prompt


def test_opening_hook_consistency_stated():
    prompt = _system_prompt()
    assert "OPENING-HOOK CONSISTENCY" in prompt


def test_no_false_title_principle_stated():
    prompt = _system_prompt()
    assert "NO FALSE TITLE" in prompt


# ---------------------------------------------------------------------------
# Output discipline
# ---------------------------------------------------------------------------


def test_forbids_image_generation_and_prompt_syntax():
    prompt = _system_prompt()
    assert "An actual thumbnail image or any image-generation prompt." in prompt
    assert "Midjourney" in prompt
    assert "Stable Diffusion" in prompt
    assert "no aspect-ratio parameters" in prompt.lower()


def test_forbids_ab_menu():
    prompt = _system_prompt()
    assert "Multiple title/thumbnail options or an A/B menu." in prompt


def test_high_risk_semantics_stated():
    prompt = _system_prompt()
    assert "HIGH means" in prompt
    assert "still recorded, not rejected" in prompt


# ---------------------------------------------------------------------------
# Injected content
# ---------------------------------------------------------------------------


def test_includes_packaging_p0_fields():
    prompt = _system_prompt()
    assert "A sturdy bridge tore itself apart in ordinary wind" in prompt
    assert "CAN WIND REALLY DO THIS?" in prompt


def test_includes_research_claims_and_disputed_points():
    prompt = _system_prompt()
    assert "C001" in prompt
    assert "Exact onset wind speed is debated" in prompt
    assert "The bridge was sabotaged" in prompt


def test_includes_narrative_opening_and_ending():
    prompt = _system_prompt()
    assert "MYSTERY_FIRST" in prompt
    assert "Callback to the opening image" in prompt


def test_includes_script_opening_and_reveal_lines():
    prompt = _system_prompt()
    assert "Cây cầu này từng an toàn." in prompt
    assert "Đó là hiện tượng flutter." in prompt


def test_includes_visual_beat_concepts():
    prompt = _system_prompt()
    assert "bridge_side_view" in prompt
    assert "Flutter diagram." in prompt


def test_includes_assembly_segment_timing():
    prompt = _system_prompt()
    assert "S001" in prompt
    assert "S002" in prompt


# ---------------------------------------------------------------------------
# User prompt
# ---------------------------------------------------------------------------


def test_user_prompt_includes_additional_context_when_supplied():
    prompt = build_user_prompt("Lean into the disbelief angle.")
    assert "Lean into the disbelief angle." in prompt


def test_user_prompt_omits_additional_context_when_absent():
    prompt = build_user_prompt(None)
    assert "Additional context" not in prompt


# ---------------------------------------------------------------------------
# Correction request
# ---------------------------------------------------------------------------


def test_correction_request_lists_issues_and_preserves_original_task():
    original = LLMRequest(system_prompt="sys", user_prompt="original task text", model="fake-model")
    corrected = build_correction_request(original, ["title cannot be blank"])
    assert "title cannot be blank" in corrected.user_prompt
    assert "original task text" in corrected.user_prompt
