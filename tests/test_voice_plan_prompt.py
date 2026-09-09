from __future__ import annotations

from app.config.loader import load_global_config
from app.engines.voice_plan.prompt import build_correction_request, build_system_prompt, build_user_prompt
from app.llm.models import LLMRequest
from app.models.script import ScriptBeat, ScriptLine, ScriptPlan


def _global_config():
    return load_global_config()


def _line(line_id="L001", function="INFORM", text="Tí kể chuyện.") -> ScriptLine:
    return ScriptLine(line_id=line_id, text=text, function=function)


def _script_plan(beats=None) -> ScriptPlan:
    return ScriptPlan(
        estimated_duration_seconds=480,
        beats=beats
        if beats is not None
        else [
            ScriptBeat(
                beat_id="B001", narrative_node="Q0", narrative_function="INFORM", lines=[_line()]
            )
        ],
        qa_status="PASS",
    )


def _system_prompt() -> str:
    return build_system_prompt(_global_config(), _script_plan())


# ---------------------------------------------------------------------------
# Character / naturalness
# ---------------------------------------------------------------------------


def test_declares_young_male_natural_voice_target():
    prompt = _system_prompt()
    assert "Young male feel, early-20s energy" in prompt
    assert "natural conversational" in prompt


def test_naturalness_prioritized_over_extreme_expressiveness():
    prompt = _system_prompt()
    assert "NATURALNESS over extreme expressiveness" in prompt


def test_naturalness_guard_lists_things_to_avoid():
    prompt = _system_prompt()
    assert "Constant high energy" in prompt
    assert "fake YouTuber shouting".lower() in prompt.lower()
    assert "Robotic equal pacing" in prompt


# ---------------------------------------------------------------------------
# Not rewriting the script
# ---------------------------------------------------------------------------


def test_declares_not_rewriting_the_script():
    prompt = _system_prompt()
    assert "NOT rewriting the script" in prompt


def test_preserves_line_ids_and_never_invents_one():
    prompt = _system_prompt()
    assert "never invent a line_id" in prompt


def test_preserves_exact_line_order():
    prompt = _system_prompt()
    assert "exact order" in prompt.lower() or "exact line order" in prompt.lower()


# ---------------------------------------------------------------------------
# Voice states / pace / energy / take count / music
# ---------------------------------------------------------------------------


def test_states_all_eight_voice_states():
    prompt = _system_prompt()
    for state in (
        "NEUTRAL", "CURIOUS", "SKEPTICAL", "EXCITED", "SERIOUS", "DEADPAN", "PANIC", "LOW_ENERGY",
    ):
        assert state in prompt


def test_states_pace_values_and_semantics():
    prompt = _system_prompt()
    assert "SLOW" in prompt and "NORMAL" in prompt and "FAST" in prompt
    assert "words-per-minute" in prompt.lower() or "not words-per-minute" in prompt.lower()


def test_states_energy_values_and_no_numeric_score():
    prompt = _system_prompt()
    assert "LOW, MEDIUM, HIGH" in prompt
    assert "numeric score" in prompt.lower()


def test_take_count_bounded_one_to_three():
    prompt = _system_prompt()
    assert "1 <= take_count <= 3" in prompt


def test_take_count_not_mechanically_forced_for_reveal():
    prompt = _system_prompt().lower()
    assert "do not mechanically give every reveal a 3" in prompt


def test_music_states_bed_duck_lift_with_semantics():
    prompt = _system_prompt()
    assert "BED" in prompt and "DUCK" in prompt and "LIFT" in prompt
    assert "background presence" in prompt.lower()


# ---------------------------------------------------------------------------
# SFX / motif
# ---------------------------------------------------------------------------


def test_sfx_opportunity_is_selective_not_every_line():
    prompt = _system_prompt()
    assert "do not overload every line with an sfx idea".lower() in prompt.lower()


def test_mouse_squeak_motif_mentioned_as_optional():
    prompt = _system_prompt()
    assert "mouse squeak" in prompt.lower()
    assert "never force one onto every video".lower() in prompt.lower()


# ---------------------------------------------------------------------------
# Chunking / output discipline
# ---------------------------------------------------------------------------


def test_mentions_tts_performance_chunking():
    prompt = _system_prompt()
    assert "coherent performance unit" in prompt.lower()
    assert "not necessarily one audio request per sentence" in prompt.lower()


def test_forbids_actual_audio_and_tts_generation():
    prompt = _system_prompt()
    assert "Actual audio, TTS output" in prompt


def test_forbids_voice_cloning_reference():
    prompt = _system_prompt().lower()
    assert "voice-cloning reference" in prompt


def test_forbids_exact_timestamps():
    prompt = _system_prompt().lower()
    assert "exact timestamps or millisecond timing" in prompt


def test_pause_intent_preserved_not_overridden():
    prompt = _system_prompt()
    assert "pause_after intent" in prompt
    assert "you do not set or override pause timing" in prompt.lower()


# ---------------------------------------------------------------------------
# User prompt
# ---------------------------------------------------------------------------


def test_user_prompt_lists_lines_in_order():
    plan = _script_plan(
        beats=[
            ScriptBeat(
                beat_id="B001",
                narrative_node="Q0",
                narrative_function="INFORM",
                lines=[_line("L001", text="Đầu tiên."), _line("L002", text="Sau đó.")],
            )
        ]
    )
    prompt = build_user_prompt(plan, None)
    assert prompt.index("L001") < prompt.index("L002")
    assert "Đầu tiên." in prompt
    assert "Sau đó." in prompt


def test_user_prompt_includes_additional_context_when_supplied():
    prompt = build_user_prompt(_script_plan(), "Emphasize the mystery tone.")
    assert "Emphasize the mystery tone." in prompt


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
