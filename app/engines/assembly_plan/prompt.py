"""The Timing/Assembly Planning Engine's business prompt. Owned here -- the
LLM layer stays generic."""

from __future__ import annotations

import json

from app.config.loader import GlobalConfig
from app.llm.models import LLMRequest
from app.models.assembly import AssemblyPlan
from app.models.script import ScriptPlan
from app.models.visual import VisualPlan
from app.models.voice import VoicePlan


def build_system_prompt(
    global_config: GlobalConfig,
    script_plan: ScriptPlan,
    voice_plan: VoicePlan,
    visual_plan: VisualPlan,
) -> str:
    schema_json = json.dumps(AssemblyPlan.model_json_schema())

    return "\n".join(
        [
            f'You are planning temporal assembly for "{global_config.brand.name}". '
            "You are NOT editing video. You are NOT generating media. You "
            "are NOT rewriting narration. Use the existing ScriptPlan, "
            "VoicePlan, and VisualPlan below to estimate a coherent "
            "timeline.",
            "",
            "OWNERSHIP BOUNDARIES",
            "ResearchPackage is factual truth. NarrativePlan is story "
            "architecture. ScriptPlan is spoken wording. VoicePlan is "
            "vocal delivery strategy. VisualPlan is visual representation "
            "strategy. You own temporal/assembly strategy only -- "
            "estimated timing, segment sequence, voice/visual alignment, "
            "transition intent, and assembly metadata. You may never alter "
            "any upstream content.",
            "",
            "SEGMENT STRUCTURE -- a hard rule for this phase",
            "Create EXACTLY one AssemblySegment per VisualBeat listed "
            "below, in the exact same order. Each segment's "
            "script_line_ids must be copied exactly from that VisualBeat's "
            "script_line_ids -- do not regroup, split, merge, or reorder "
            "visual coverage; this phase is temporal, not editorial.",
            "",
            "VOICE CHUNK IDS -- do not compute this yourself",
            "voice_chunk_ids is a deterministic relationship the engine "
            "derives automatically from VoicePlan after you respond -- "
            "leave it as an empty list or a best-effort guess; it will be "
            "silently corrected either way, so do not spend effort on it.",
            "",
            "MUSIC STATE",
            "Resolve one music_state per segment from VoicePlan's chunks. "
            "If every VoiceChunk overlapping the segment shares one "
            "MusicState, use it. If more than one state overlaps, choose "
            "the dominant or intended state for that segment based on the "
            "delivery context -- never invent a new state and never mutate "
            "VoicePlan itself.",
            "",
            "TRANSITION INTENT -- use exactly these five, no others",
            "CUT: a direct visual change. "
            "DISSOLVE: a soft temporal/emotional transition. "
            "MATCH: visual continuity or matched composition. "
            "PUSH: a simple directional transition or attention shift. "
            "NONE: the visual continues, no transition. "
            "Transitions support clarity, a location change, a time "
            "change, a conceptual shift, or a reveal -- they are not "
            "decorative editing. Most segments should simply use CUT or "
            "NONE; do not give every segment a flashy transition.",
            "",
            "TIMING -- estimated, not final render timing",
            "start_seconds and end_seconds must be non-negative, with "
            "end_seconds strictly greater than start_seconds. Segments "
            "must form one continuous timeline: the first segment starts "
            "at 0, and every next segment's start_seconds must equal the "
            "previous segment's end_seconds exactly (no gaps, no "
            "overlaps). estimated_total_duration_seconds must equal the "
            "final segment's end_seconds. The total should stay reasonably "
            "aligned with ScriptPlan's own estimated_duration_seconds "
            "below -- this is planning tolerance, not an exact render "
            "duration.",
            "",
            "DURATION ESTIMATION -- holistic, not a formula",
            "Estimate each segment's duration from the amount of spoken "
            "text, the underlying VoiceState and Pace, the pause intent on "
            "the underlying ScriptLines, and visual comprehension needs -- "
            "an L3 relationship diagram or a major reveal may need more "
            "viewing time than the words alone would take; a fast, "
            "escalating reaction may compress. Do NOT apply a fixed "
            "words-per-minute formula, and do NOT deterministically convert "
            "pause intent into fixed millisecond values -- exact pacing "
            "and pause timing remain a future renderer's job.",
            "",
            "VISUAL HOLD",
            "Certain visuals may need hold time beyond pure speech -- an "
            "L3 relationship diagram, evidence media, a major reveal, or a "
            "visual gag. Allocate time accordingly, but do not add fake "
            "silent filler everywhere.",
            "",
            "PACING RHYTHM",
            "A segment may progress quickly (more cuts, tighter timing) or "
            "breathe (a longer hold, fewer changes) depending on its "
            "narrative and delivery context -- there is no fixed ratio "
            "between the two.",
            "",
            "CAPCUT-ORIENTED METADATA",
            "This plan should be friendly to later CapCut execution: "
            "segment ordering, estimated start/end, the active visual "
            "beat, voice-chunk relationship, music state, and transition "
            "intent are all useful. Do not implement or reference any "
            "CapCut-specific API or file format -- this is planning "
            "metadata only.",
            "",
            "REQUIRED ASSETS",
            "Summarize production dependencies as short descriptive "
            "identifiers (e.g. \"bridge_side_view\", \"ti_skeptical_state\", "
            "\"earth_geometry_diagram\", \"evidence:S003\") drawn from "
            "VisualPlan's reusable_assets, reuse_keys, and any "
            "EVIDENCE_MEDIA beats' evidence_source_ids. Never invent a "
            "filesystem path, and never fetch or generate the actual "
            "asset.",
            "",
            "PRODUCTION NOTES",
            "production_notes may optionally include a concise complexity "
            "summary (e.g. \"C0/C1-heavy, 2 C2 diagrams, 1 C3 hero beat\") "
            "-- VisualPlan already owns complexity classification; do not "
            "invent a second complexity model here.",
            "",
            "OUTPUT DISCIPLINE -- do NOT produce any of the following",
            "- Image prompts, image generation, or video generation.",
            "- TTS, music generation, or sound-design generation.",
            "- Exact edit commands, animation keyframes, or frame numbers.",
            "",
            "SCRIPTPLAN",
            f"- estimated_duration_seconds: {script_plan.estimated_duration_seconds}",
            _script_lines_summary(script_plan),
            "",
            "VOICEPLAN",
            _voice_chunks_summary(voice_plan),
            "",
            "VISUALPLAN",
            _visual_beats_summary(visual_plan),
            "",
            "OUTPUT FORMAT",
            "Return ONLY a single JSON object matching this schema exactly "
            "-- no prose, no markdown code fences, no explanation:",
            schema_json,
            "",
            f"script_plan_id to use: {script_plan.id}",
            f"voice_plan_id to use: {voice_plan.id}",
            f"visual_plan_id to use: {visual_plan.id}",
        ]
    )


def build_user_prompt(additional_context: str | None) -> str:
    lines: list[str] = []
    if additional_context:
        lines += ["Additional context from the user:", additional_context, ""]
    lines.append("Return your AssemblyPlan now.")
    return "\n".join(lines)


def build_correction_request(original_request: LLMRequest, issues: list[str]) -> LLMRequest:
    """One bounded business-correction request addressing every violation
    together. Does not touch the LLM layer's own JSON/schema correction
    logic (Phase 3, locked) -- this is the Timing/Assembly Planning
    Engine's own business rule, exactly like every prior engine's
    business-correction step. Deterministic fields (the three plan-level
    ids and every segment's voice_chunk_ids) are never the reason a
    correction is needed -- the engine normalizes those itself -- so this
    request only ever lists genuinely LLM-owned problems (segment/beat
    mapping, script-line coverage, or timing)."""
    lines = [
        "Your previous response failed business validation. Problems found:",
        "\n".join(f"- {issue}" for issue in issues),
        "",
        "Fix ALL of the above together in your corrected response:",
        "- Create exactly one segment per VisualBeat, in the same order, "
        "with script_line_ids copied exactly from that VisualBeat.",
        "- The timeline must be continuous: first segment starts at 0, "
        "each next segment's start_seconds equals the previous segment's "
        "end_seconds exactly, and estimated_total_duration_seconds equals "
        "the final segment's end_seconds.",
        "- Keep the total duration reasonably aligned with ScriptPlan's "
        "estimated_duration_seconds.",
        "",
        "Return a corrected JSON object matching the required schema exactly.",
        "",
        "Original task:",
        original_request.user_prompt,
    ]
    return original_request.model_copy(update={"user_prompt": "\n".join(lines)})


def _script_lines_summary(script_plan: ScriptPlan) -> str:
    lines = ["- lines (in exact order):"]
    for beat in script_plan.beats:
        for line in beat.lines:
            lines.append(
                f"  {line.line_id} pause_after={line.pause_after.value}: {line.text}"
            )
    return "\n".join(lines)


def _voice_chunks_summary(voice_plan: VoicePlan) -> str:
    return "\n".join(
        f"- {chunk.chunk_id} (lines {chunk.line_ids}): voice_state={chunk.voice_state.value}, "
        f"pace={chunk.pace.value}, energy={chunk.energy.value}, "
        f"music_state={chunk.music_state.value}"
        for chunk in voice_plan.chunks
    )


def _visual_beats_summary(visual_plan: VisualPlan) -> str:
    return "\n".join(
        f"- {beat.beat_id} (lines {beat.script_line_ids}): visual_level={beat.visual_level.value}, "
        f"media_type={beat.media_type.value}, complexity={beat.complexity.value}, "
        f"motion_intent={beat.motion_intent!r}, reuse_key={beat.reuse_key!r}"
        for beat in visual_plan.beats
    )
