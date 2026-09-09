from __future__ import annotations

from uuid import uuid4

from app.config.loader import load_global_config
from app.engines.assembly_plan.prompt import build_correction_request, build_system_prompt, build_user_prompt
from app.llm.models import LLMRequest
from app.models.script import ScriptBeat, ScriptLine, ScriptPlan
from app.models.visual import VisualBeat, VisualPlan
from app.models.voice import VoiceChunk, VoicePlan


def _global_config():
    return load_global_config()


def _script_plan() -> ScriptPlan:
    return ScriptPlan(
        estimated_duration_seconds=480,
        beats=[
            ScriptBeat(
                beat_id="B001", narrative_node="Q0", narrative_function="INFORM",
                lines=[
                    ScriptLine(line_id="L001", text="Tí kể chuyện.", function="INFORM", pause_after="SHORT"),
                ],
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


def _visual_plan() -> VisualPlan:
    return VisualPlan(
        script_plan_id=uuid4(), voice_plan_id=uuid4(),
        beats=[
            VisualBeat(
                beat_id="VB001", script_line_ids=["L001"], narrative_node="Q0",
                visual_level="L1_ESTABLISH", visual_function="STORY", media_type="ASSET_REUSE",
                complexity="C0", concept="An establishing shot.", primary_focus="The bridge",
                reuse_key="bridge_side_view",
            )
        ],
    )


def _system_prompt() -> str:
    return build_system_prompt(_global_config(), _script_plan(), _voice_plan(), _visual_plan())


# ---------------------------------------------------------------------------
# Role / ownership
# ---------------------------------------------------------------------------


def test_declares_temporal_assembly_role():
    prompt = _system_prompt()
    assert "planning temporal assembly" in prompt


def test_declares_not_editing_or_generating_or_rewriting():
    prompt = _system_prompt()
    assert "NOT editing video" in prompt
    assert "NOT generating media" in prompt
    assert "NOT rewriting narration" in prompt


def test_states_script_voice_visual_ownership():
    prompt = _system_prompt()
    assert "ScriptPlan is spoken wording" in prompt
    assert "VoicePlan is vocal delivery strategy" in prompt
    assert "VisualPlan is visual representation strategy" in prompt


# ---------------------------------------------------------------------------
# Timing
# ---------------------------------------------------------------------------


def test_states_estimated_not_final_timing():
    prompt = _system_prompt()
    assert "estimated, not final render timing" in prompt


def test_mentions_pause_intent_context():
    prompt = _system_prompt()
    assert "pause intent" in prompt.lower()


def test_mentions_voice_pace_and_energy():
    prompt = _system_prompt()
    assert "VoiceState and Pace" in prompt


def test_mentions_visual_hold():
    prompt = _system_prompt()
    assert "VISUAL HOLD" in prompt
    assert "relationship diagram" in prompt.lower()


def test_no_words_per_minute_formula():
    prompt = _system_prompt()
    assert "Do NOT apply a fixed" in prompt
    assert "words-per-minute" in prompt.lower()


def test_no_fixed_pause_milliseconds():
    prompt = _system_prompt().lower()
    assert "millisecond" in prompt


def test_mentions_pacing_rhythm_without_fixed_ratio():
    prompt = _system_prompt()
    assert "PACING RHYTHM" in prompt
    assert "no fixed ratio" in prompt.lower()


# ---------------------------------------------------------------------------
# Transitions
# ---------------------------------------------------------------------------


def test_all_five_transition_intents_present():
    prompt = _system_prompt()
    for transition in ("CUT", "DISSOLVE", "MATCH", "PUSH", "NONE"):
        assert transition in prompt


def test_transitions_not_decorative():
    prompt = _system_prompt()
    assert "not decorative editing" in prompt


# ---------------------------------------------------------------------------
# CapCut orientation / assets
# ---------------------------------------------------------------------------


def test_capcut_oriented_metadata_mentioned():
    prompt = _system_prompt()
    assert "CAPCUT-ORIENTED METADATA" in prompt
    assert "friendly to later CapCut execution" in prompt


def test_required_assets_guidance_present():
    prompt = _system_prompt()
    assert "REQUIRED ASSETS" in prompt
    assert "evidence:S003" in prompt


# ---------------------------------------------------------------------------
# Output discipline
# ---------------------------------------------------------------------------


def test_forbids_image_and_video_generation():
    prompt = _system_prompt()
    assert "Image prompts, image generation, or video generation." in prompt


def test_forbids_tts_and_music_generation():
    prompt = _system_prompt()
    assert "TTS, music generation, or sound-design generation." in prompt


def test_forbids_exact_edit_commands_and_keyframes():
    prompt = _system_prompt()
    assert "Exact edit commands, animation keyframes, or frame numbers." in prompt


def test_voice_chunk_ids_not_computed_by_llm():
    prompt = _system_prompt()
    assert "do not compute this yourself".lower() in prompt.lower()


# ---------------------------------------------------------------------------
# Injected content
# ---------------------------------------------------------------------------


def test_includes_script_line_and_pause_intent():
    prompt = _system_prompt()
    assert "L001" in prompt
    assert "pause_after=SHORT" in prompt


def test_includes_voice_chunk_context():
    prompt = _system_prompt()
    assert "voice_state=NEUTRAL" in prompt
    assert "music_state=BED" in prompt


def test_includes_visual_beat_context():
    prompt = _system_prompt()
    assert "VB001" in prompt
    assert "bridge_side_view" in prompt


# ---------------------------------------------------------------------------
# User prompt
# ---------------------------------------------------------------------------


def test_user_prompt_includes_additional_context_when_supplied():
    prompt = build_user_prompt("Keep the reveal beat punchy.")
    assert "Keep the reveal beat punchy." in prompt


def test_user_prompt_omits_additional_context_when_absent():
    prompt = build_user_prompt(None)
    assert "Additional context" not in prompt


# ---------------------------------------------------------------------------
# Correction request
# ---------------------------------------------------------------------------


def test_correction_request_lists_issues_and_preserves_original_task():
    original = LLMRequest(system_prompt="sys", user_prompt="original task text", model="fake-model")
    corrected = build_correction_request(original, ["timeline gap between S1 and S2"])
    assert "timeline gap between S1 and S2" in corrected.user_prompt
    assert "original task text" in corrected.user_prompt
