from __future__ import annotations

from uuid import uuid4

from app.config.loader import load_global_config
from app.engines.visual_plan.prompt import build_correction_request, build_system_prompt, build_user_prompt
from app.llm.models import LLMRequest
from app.models.narrative import SCQA, NarrativePlan, QuestionLadderNode
from app.models.research import ResearchPackage, Source
from app.models.script import ScriptBeat, ScriptLine, ScriptPlan
from app.models.voice import VoiceChunk, VoicePlan


def _global_config():
    return load_global_config()


def _narrative_plan() -> NarrativePlan:
    return NarrativePlan(
        central_question="Why did a sturdy bridge collapse in mild wind?",
        scqa=SCQA(situation="s", complication="c", question="q", answer="a"),
        opening="MYSTERY_FIRST",
        question_ladder=[
            QuestionLadderNode(
                id="Q0", question="Why?", why_viewer_cares="matters", partial_answer="flutter",
                information_gap="none",
            )
        ],
        ti_role="Investigator",
        ending="Callback to the opening image",
    )


def _research_package() -> ResearchPackage:
    return ResearchPackage(
        central_question="Why?",
        executive_summary="s",
        physics_core="Flutter",
        simplification_boundary="Do not replace flutter with resonance.",
        sources=[Source(source_id="S001", title="1940 collapse film", type="video", quality_tier=1, authoritative=True)],
    )


def _script_plan() -> ScriptPlan:
    return ScriptPlan(
        estimated_duration_seconds=480,
        beats=[
            ScriptBeat(
                beat_id="B001", narrative_node="Q0", narrative_function="INFORM",
                lines=[ScriptLine(line_id="L001", text="Tí kể chuyện.", function="INFORM")],
            )
        ],
        qa_status="PASS",
    )


def _voice_plan() -> VoicePlan:
    return VoicePlan(
        script_plan_id=uuid4(),
        chunks=[
            VoiceChunk(
                chunk_id="C001", line_ids=["L001"], voice_state="NEUTRAL", pace="NORMAL",
                energy="MEDIUM", take_count=1, music_state="BED",
            )
        ],
    )


def _system_prompt() -> str:
    return build_system_prompt(_global_config(), _narrative_plan(), _research_package(), _script_plan(), _voice_plan())


# ---------------------------------------------------------------------------
# Visual grammar
# ---------------------------------------------------------------------------


def test_l1_establish_feel_semantics():
    prompt = _system_prompt()
    assert "L1_ESTABLISH" in prompt
    assert "establish place, era, atmosphere" in prompt


def test_l2_watch_action_semantics():
    prompt = _system_prompt()
    assert "L2_ACTION" in prompt
    assert "viewer watches the phenomenon/mechanism happen" in prompt


def test_l3_understand_relationship_semantics():
    prompt = _system_prompt()
    assert "L3_RELATIONSHIP" in prompt
    assert "explain a relationship the viewer cannot" in prompt


def test_l2_attention_path_guidance():
    prompt = _system_prompt()
    assert "attention should land on the mechanism" in prompt
    assert "never arbitrarily" in prompt.lower() and "blur" in prompt.lower()


def test_l3_hierarchy_primary_secondary_context():
    prompt = _system_prompt()
    assert "PRIMARY" in prompt and "SECONDARY" in prompt and "CONTEXT" in prompt


# ---------------------------------------------------------------------------
# Visual functions / media router
# ---------------------------------------------------------------------------


def test_all_five_visual_functions_present():
    prompt = _system_prompt()
    for fn in ("STORY", "EVIDENCE", "MECHANISM", "METAPHOR", "EMPHASIS"):
        assert fn in prompt


def test_all_seven_media_types_present():
    prompt = _system_prompt()
    for media in (
        "ASSET_REUSE", "TI_STATE", "DIAGRAM", "GENERATED_STILL",
        "LIMITED_MOTION", "EVIDENCE_MEDIA", "AI_HERO_VIDEO",
    ):
        assert media in prompt


def test_evidence_not_wallpaper_rule():
    prompt = _system_prompt()
    assert "not wallpaper" in prompt.lower()


# ---------------------------------------------------------------------------
# Complexity / movement
# ---------------------------------------------------------------------------


def test_c0_c1_majority_and_rare_c3():
    prompt = _system_prompt()
    assert "70-80%" in prompt
    assert "5-10%" in prompt
    assert "at most 20%" in prompt


def test_do_not_animate_everything():
    prompt = _system_prompt().lower()
    assert "do not animate everything" in prompt


# ---------------------------------------------------------------------------
# Tí usage
# ---------------------------------------------------------------------------


def test_ti_investigator_and_reactor_roles():
    prompt = _system_prompt()
    assert "investigator" in prompt.lower()
    assert "reaction character" in prompt.lower()


def test_no_ti_quota():
    prompt = _system_prompt().lower()
    assert "no tí quota" in prompt or "there is no tí quota" in prompt


def test_historical_causality_guard():
    prompt = _system_prompt()
    assert "REPRESENTATIONAL_GAG" in prompt
    assert "did not cause" not in prompt  # sanity: not asserting exact phrasing
    assert "changed history" in prompt.lower()


# ---------------------------------------------------------------------------
# Beat granularity / event density / asset reuse
# ---------------------------------------------------------------------------


def test_visual_event_density_guidance():
    prompt = _system_prompt().lower()
    assert "never merely every n seconds" in prompt or "every n seconds" in prompt


def test_one_sentence_does_not_imply_one_shot():
    prompt = _system_prompt().lower()
    assert "does not equal one new shot" in prompt or "one sentence equals one new shot" in prompt


def test_reuse_key_and_reusable_assets_mentioned():
    prompt = _system_prompt()
    assert "reuse_key" in prompt
    assert "reusable_assets" in prompt


# ---------------------------------------------------------------------------
# Design style
# ---------------------------------------------------------------------------


def test_design_style_painterly_editorial_2d():
    prompt = _system_prompt()
    assert "Painterly/editorial 2D" in prompt
    assert "NOT hard cel-shading" in prompt
    assert "NOT photorealistic" in prompt
    assert "NOT chibi" in prompt


def test_avoids_decorative_clutter():
    prompt = _system_prompt().lower()
    assert "decorative clutter" in prompt


# ---------------------------------------------------------------------------
# Output discipline
# ---------------------------------------------------------------------------


def test_not_generating_images_or_prompts_or_storyboards():
    prompt = _system_prompt()
    assert "NOT generating images" in prompt
    assert "NOT writing image prompts" in prompt
    assert "NOT storyboarding frame-by-frame" in prompt


def test_no_exact_timing_in_motion_intent():
    prompt = _system_prompt().lower()
    assert "exact durations" in prompt
    assert "frame numbers" in prompt


# ---------------------------------------------------------------------------
# Injected content
# ---------------------------------------------------------------------------


def test_includes_simplification_boundary():
    prompt = _system_prompt()
    assert "Do not replace flutter with resonance." in prompt


def test_includes_narrative_node_ids():
    prompt = _system_prompt()
    assert "['Q0']" in prompt


def test_includes_voice_chunk_rhythm_context():
    prompt = _system_prompt()
    assert "energy=MEDIUM" in prompt
    assert "pace=NORMAL" in prompt


def test_includes_evidence_sources():
    prompt = _system_prompt()
    assert "S001" in prompt


# ---------------------------------------------------------------------------
# User prompt
# ---------------------------------------------------------------------------


def test_user_prompt_lists_script_lines():
    prompt = build_user_prompt(_script_plan(), None)
    assert "L001" in prompt
    assert "Tí kể chuyện." in prompt


def test_user_prompt_includes_additional_context_when_supplied():
    prompt = build_user_prompt(_script_plan(), "Lean into the mystery framing.")
    assert "Lean into the mystery framing." in prompt


def test_user_prompt_omits_additional_context_when_absent():
    prompt = build_user_prompt(_script_plan(), None)
    assert "Additional context" not in prompt


# ---------------------------------------------------------------------------
# Correction request
# ---------------------------------------------------------------------------


def test_correction_request_lists_issues_and_preserves_original_task():
    original = LLMRequest(system_prompt="sys", user_prompt="original task text", model="fake-model")
    corrected = build_correction_request(original, ["missing line L002"])
    assert "missing line L002" in corrected.user_prompt
    assert "original task text" in corrected.user_prompt
