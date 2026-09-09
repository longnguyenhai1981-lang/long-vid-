"""The Voice Planning Engine's business prompt. Owned here -- the LLM layer
stays generic."""

from __future__ import annotations

import json

from app.config.loader import GlobalConfig
from app.llm.models import LLMRequest
from app.models.script import ScriptPlan
from app.models.voice import VoicePlan


def build_system_prompt(global_config: GlobalConfig, script_plan: ScriptPlan) -> str:
    schema_json = json.dumps(VoicePlan.model_json_schema())
    character = global_config.character

    return "\n".join(
        [
            f'You are planning vocal performance for "{character.name}", the host '
            f'of "{global_config.brand.name}". You are NOT rewriting the script --',
            "you own delivery instructions only. Wording, claims, line order, and "
            "line count belong entirely to the locked ScriptPlan below; you may "
            "never alter, add, remove, or reorder a line, and you may never "
            "invent a line_id that isn't in it.",
            "",
            f'CHARACTER: core trait "{character.core_trait}", addresses the '
            f'viewer as "{character.language.audience_address}", may '
            f'self-reference as "{character.language.self_reference}".',
            "",
            "VOICE TARGET",
            "Young male feel, early-20s energy, natural conversational "
            "delivery, slightly stylized. NOT an announcer, NOT a documentary "
            "narrator, NOT overly theatrical, NOT cartoon-squeaky by default. "
            "Priority: NATURALNESS over extreme expressiveness.",
            "",
            "NATURALNESS GUARD -- explicitly avoid",
            "- Exaggerated every-line acting.",
            "- Constant high energy.",
            "- Fake YouTuber shouting.",
            "- Robotic equal pacing across every line.",
            "- Every sentence ending dramatically.",
            "Performance should have contrast: neutral, unremarkable delivery "
            "is allowed and necessary for the exciting moments to actually "
            "land as exciting.",
            "",
            "YOUR JOB",
            "For each ScriptLine, decide: voice_state, pace, energy, and "
            "music_state. Decide take_count (see below). Optionally note an "
            "sfx_opportunity. Group adjacent lines that share the same "
            "delivery treatment into one VoiceChunk -- a chunk is a coherent "
            "performance unit for future TTS generation, not necessarily one "
            "audio request per sentence.",
            "",
            "VOICE STATES -- use exactly these, no others",
            "NEUTRAL, CURIOUS, SKEPTICAL, EXCITED, SERIOUS, DEADPAN, PANIC, "
            "LOW_ENERGY.",
            "",
            "PACE -- a small typed scale, not words-per-minute",
            "SLOW: important reveal, serious explanation, emphasis. "
            "NORMAL: default conversational delivery. "
            "FAST: escalation, excited reaction, comedic rush.",
            "",
            "ENERGY -- LOW, MEDIUM, HIGH. Never a numeric score.",
            "",
            "PAUSE",
            "Every ScriptLine already carries its own pause_after intent "
            "(NONE/SHORT/MEDIUM/LONG) from the locked script -- you do not "
            "set or override pause timing; that stays exactly as written.",
            "",
            "TAKE COUNT -- selective, not uniform",
            "Hard limit: 1 <= take_count <= 3. Default is 1 for an ordinary "
            "line. Use 2 for a genuinely important line. Use 2-3 only for a "
            "hook, reveal, joke, or major payoff line, and only when the "
            "delivery is actually difficult to nail in one take (emotional "
            "difficulty, tricky joke timing, a line carrying the whole "
            "payoff). Do NOT mechanically give every REVEAL a 3, and do not "
            "request extra takes for lines that don't need them -- wasteful "
            "over-recording is a real cost.",
            "",
            "LINE FUNCTION TENDENCIES -- guidance, not hard rules; judge each "
            "line's actual content",
            "- QUESTION: often CURIOUS or SKEPTICAL.",
            "- REVEAL: often SERIOUS or EXCITED.",
            "- ESCALATE: often FAST and HIGH energy.",
            "- REACT: follow the line's own emotion field.",
            "- JOKE: often DEADPAN, sometimes EXCITED depending on the joke.",
            "- CLARIFY: often NORMAL pace, MEDIUM energy, and sometimes a "
            "DUCK in music so the clarification reads clearly.",
            "- EMPHASIZE: slower or higher-energy depending on what's being "
            "emphasized.",
            "Also use ScriptBeat.micro_hook and narrative_node as signals of "
            "delivery importance -- a beat carrying a micro-hook usually "
            "deserves more deliberate pacing than routine connective tissue.",
            "",
            "MUSIC STATES -- use exactly these, no others",
            "BED: normal background presence (the default for most lines). "
            "DUCK: music reduced so important or dense speech lands clearly "
            "(e.g. a dense physics explanation, or a punchline that needs "
            "silence around it). LIFT: music rises to support a reveal, "
            "transition, payoff, or emotional beat. No fixed quota for any "
            "state -- most of the script should simply be BED.",
            "",
            "SFX OPPORTUNITY -- optional, sparing",
            "sfx_opportunity is a plain string naming a possible sound "
            "effect moment (e.g. \"mouse squeak\", \"tonal hit\", \"record "
            "scratch\", \"tiny impact\", \"whoosh\") -- an opportunity for a "
            "future SFX pass, not something generated now. Leave it null on "
            "most chunks; do not overload every line with an SFX idea. A "
            "signature motif like a mouse squeak or a compact tonal hit may "
            "be considered for a recurring brand beat, but never force one "
            "onto every video.",
            "",
            "OUTPUT DISCIPLINE -- do NOT produce any of the following",
            "- Actual audio, TTS output, or a voice-cloning reference.",
            "- Exact timestamps or millisecond timing.",
            "- A full music composition plan (only BED/DUCK/LIFT per chunk).",
            "- A rewritten line, a reordered line, a deleted line, an added "
            "line, or an invented line_id.",
            "",
            "CHUNKING RULES",
            "Every ScriptLine.line_id must appear in exactly one chunk's "
            "line_ids, in the exact order it appears in the script below. A "
            "chunk's line_ids must be a contiguous run of the script's line "
            "order -- never skip a line and come back to it in a later "
            "chunk. The chunks list, read in order and concatenated, must "
            "reconstruct the script's exact line order with nothing missing, "
            "nothing extra, and nothing duplicated.",
            "",
            "OUTPUT FORMAT",
            "Return ONLY a single JSON object matching this schema exactly -- "
            "no prose, no markdown code fences, no explanation:",
            schema_json,
            "",
            f"script_plan_id to use: {script_plan.id}",
        ]
    )


def build_user_prompt(script_plan: ScriptPlan, additional_context: str | None) -> str:
    lines = ["SCRIPT TO PLAN DELIVERY FOR (in exact order; do not reorder):"]
    for beat in script_plan.beats:
        micro_hook = f", micro_hook={beat.micro_hook!r}" if beat.micro_hook else ""
        lines.append(f"Beat {beat.beat_id} (narrative_node={beat.narrative_node}{micro_hook}):")
        for line in beat.lines:
            lines.append(
                f"  {line.line_id} [{line.function.value}] "
                f"emotion={line.emotion!r} emphasis={line.emphasis!r} "
                f"pause_after={line.pause_after.value}: {line.text}"
            )

    if additional_context:
        lines += ["", "Additional context from the user:", additional_context]

    lines += ["", "Return your VoicePlan now."]
    return "\n".join(lines)


def build_correction_request(original_request: LLMRequest, issues: list[str]) -> LLMRequest:
    """One bounded business-correction request addressing every violation
    together. Does not touch the LLM layer's own JSON/schema correction logic
    (Phase 3, locked) -- this is the Voice Planning Engine's own business
    rule, exactly like every prior engine's business-correction step."""
    lines = [
        "Your previous response failed business validation. Problems found:",
        "\n".join(f"- {issue}" for issue in issues),
        "",
        "Fix ALL of the above together in your corrected response:",
        "- Every ScriptLine.line_id from the original script must appear in "
        "exactly one chunk's line_ids, in the script's exact order.",
        "- Never invent a line_id that isn't in the original script.",
        "- Chunk line_ids must be a contiguous run of the script's line "
        "order -- do not split a chunk's lines apart and let another "
        "chunk's lines fall between them.",
        "",
        "Return a corrected JSON object matching the required schema exactly.",
        "",
        "Original task:",
        original_request.user_prompt,
    ]
    return original_request.model_copy(update={"user_prompt": "\n".join(lines)})
