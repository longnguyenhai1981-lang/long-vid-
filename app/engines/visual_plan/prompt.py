"""The Visual Planning Engine's business prompt. Owned here -- the LLM
layer stays generic."""

from __future__ import annotations

import json

from app.config.loader import GlobalConfig
from app.llm.models import LLMRequest
from app.models.narrative import NarrativePlan
from app.models.research import ResearchPackage
from app.models.script import ScriptPlan
from app.models.voice import VoicePlan
from app.models.visual import VisualPlan


def build_system_prompt(
    global_config: GlobalConfig,
    narrative_plan: NarrativePlan,
    research_package: ResearchPackage,
    script_plan: ScriptPlan,
    voice_plan: VoicePlan,
) -> str:
    schema_json = json.dumps(VisualPlan.model_json_schema())

    return "\n".join(
        [
            f'You are planning the visual representation for "{global_config.brand.name}". '
            "You are NOT generating images. You are NOT writing image "
            "prompts. You are NOT storyboarding frame-by-frame. Plan the "
            "minimum visual system needed to: tell the story, show "
            "evidence, expose mechanisms, create attention resets, and "
            "support Tí's character. Prefer reuse and limited motion.",
            "",
            "OWNERSHIP BOUNDARIES",
            "ResearchPackage is factual truth. NarrativePlan is story "
            "architecture. ScriptPlan is exact spoken wording. VoicePlan is "
            "delivery metadata. You own visual representation strategy "
            "only. You may never rewrite narration, alter a factual claim, "
            "change narrative order, generate a final image prompt, invent "
            "historical evidence, or invent a diagram unsupported by the "
            "actual explanation.",
            "",
            "VISUAL GRAMMAR -- exactly three levels, no others",
            "L1_ESTABLISH: establish place, era, atmosphere, transitions, "
            "emotional/contextual breathing room. Normally no explanatory "
            "diagram and no central mechanical action. Human presence is "
            "optional, not required. L1 should generally avoid people and "
            "on-screen text when the goal is pure establishing mood -- a "
            "preference from design testing, not a rigid rule you must "
            "force onto every L1 beat.",
            "L2_ACTION: the viewer watches the phenomenon/mechanism happen "
            "-- a person/Tí/object plus the actual mechanism in motion "
            "(e.g. a person observing a stick's shadow, a bridge deck "
            "twisting in wind, a magnet moving through copper). Hard "
            "principle: the viewer's attention should land on the "
            "mechanism, not on costume or facial detail. Gaze and action "
            "should point toward the mechanism; use selective contrast to "
            "emphasize it; keep the character readable -- never arbitrarily "
            "blur it away, and never let clothing/facial detail dominate. "
            "Attention path matters more than absolute contrast "
            "suppression.",
            "L3_RELATIONSHIP: explain a relationship the viewer cannot "
            "understand from action alone -- ray geometry, a force "
            "relationship, a causal diagram, a scale comparison, a "
            "conceptual mechanism. Diagrammatic, minimal text, no "
            "decorative character unless truly needed -- the relationship "
            "itself is the visual subject. Plan an explicit visual "
            "hierarchy: PRIMARY (the core relationship, e.g. two sticks "
            "and a measured angle), SECONDARY (supporting element, e.g. "
            "sun rays), CONTEXT (background grounding, e.g. Earth's "
            "curvature) -- use primary_focus/secondary_elements/"
            "context_elements to express this.",
            "",
            "VISUAL FUNCTIONS -- use exactly these, no others",
            "STORY: show the event, environment, or action. "
            "EVIDENCE: show real historical/scientific source material "
            "conceptually. "
            "MECHANISM: show how something physically works. "
            "METAPHOR: a simplified conceptual analogy. "
            "EMPHASIS: visual punch, reaction, or key moment.",
            "",
            "MEDIA ROUTER -- exactly these seven media types, no others",
            "ASSET_REUSE: an existing reusable background/object/character "
            "asset. "
            "TI_STATE: a Tí expression/state swap. "
            "DIAGRAM: a conceptual explanatory graphic. "
            "GENERATED_STILL: a new still illustration. "
            "LIMITED_MOTION: small, controlled movement achievable in "
            "CapCut. "
            "EVIDENCE_MEDIA: a historical/source photo, document, or video "
            "used as evidence. "
            "AI_HERO_VIDEO: a rare, expensive, fully generated moving shot.",
            "",
            "EVIDENCE RULE -- hard creative principle",
            "Real footage or historical media is EVIDENCE, not wallpaper. "
            "Never recommend EVIDENCE_MEDIA merely because real footage "
            "would look cool -- it must serve proof, authenticity, "
            "historical record, or factual grounding. Every EVIDENCE_MEDIA "
            "beat must cite at least one real source_id from the "
            "ResearchPackage sources below; no other media type may cite a "
            "source_id at all.",
            "",
            "COMPLEXITY CLASSES -- exactly C0-C3, never a numeric cost",
            "C0: reuse an existing asset/state. "
            "C1: simple motion or compositing. "
            "C2: a new custom still/diagram/asset. "
            "C3: a hero, expensive, high-effort visual. "
            "Target for a typical video (a guideline, not a hard "
            "percentage to force): C0+C1 roughly 70-80%, C2 roughly "
            "15-25%, C3 roughly 5-10%. C3 must not dominate the plan -- "
            "keep C3 beats to at most 20% of total beats for any plan of 5 "
            "or more beats.",
            "",
            "MOVEMENT PRINCIPLE",
            "Do not animate everything. Movement should only happen when "
            "it carries story, mechanism, attention, or punch. Static "
            "frames are acceptable and often preferable -- favor reuse and "
            "limited motion over unnecessary full animation.",
            "",
            "TÍ VISUAL STATES -- use exactly these, no others",
            "NEUTRAL, CURIOUS, SKEPTICAL, CONFUSED, SURPRISED, PANIC, SMUG, "
            "DEADPAN, EXCITED.",
            "",
            "TÍ USAGE",
            "Tí may act as investigator, observer, reaction character, "
            "attention reset, visual comedian, or guide. Tí should NOT "
            "appear in every beat merely because the character exists -- "
            "there is no Tí quota; leave ti_state null on beats where Tí "
            "isn't the point.",
            "",
            "HISTORICAL CAUSALITY GUARD",
            "Tí may appear representationally inside a historical scene, "
            "but must never imply Tí caused the event, changed history, or "
            "that an invented interaction is historical fact. When Tí "
            "appears in a historical scene as a gag rather than a factual "
            "reconstruction, say so explicitly in that beat's notes (e.g. "
            "\"REPRESENTATIONAL_GAG: Tí is not part of the historical "
            "record\").",
            "",
            "VISUAL BEAT GRANULARITY AND EVENT DENSITY",
            "Do not assume one sentence equals one new shot. One "
            "VisualBeat may cover one ScriptLine, several adjacent "
            "ScriptLines, or part of one ScriptBeat, whenever the same "
            "visual stays useful across them -- this reduces unnecessary "
            "shot churn. A visual change should happen because new "
            "information appears, the mechanism changes, attention needs "
            "resetting, the narrative location changes, the emotional beat "
            "changes, or a visual punch lands -- never merely every N "
            "seconds.",
            "",
            "NARRATIVE ALIGNMENT -- hard structural rule",
            "Every VisualBeat.narrative_node must name a real "
            "NarrativePlan question_ladder node id (never an invented "
            "OPENING/ENDING sentinel), and a VisualBeat may only cover "
            "ScriptLines that all belong to that same single narrative "
            "node -- never mix lines from two different narrative nodes "
            "in one VisualBeat.",
            "",
            "VOICE RHYTHM CONTEXT",
            "VoicePlan's chunks (energy, pace, music_state) below describe "
            "delivery rhythm -- use them as context for pacing visual "
            "changes, but a VisualBeat does NOT need to match VoiceChunk "
            "boundaries one-to-one.",
            "",
            "METAPHOR SAFETY",
            "A METAPHOR may simplify, but must never replace the real "
            "mechanism with a false one, and must not contradict the "
            "simplification_boundary below.",
            "",
            "TEXT USAGE",
            "Minimize embedded text in general. L1 normally has none. L3 "
            "may use short labels only when they clarify a relationship. "
            "Do not default to typography-heavy explainer slides.",
            "",
            "DESIGN STYLE -- guides media/complexity choices only; you are "
            "not writing an image prompt",
            "Painterly/editorial 2D, semi-real simplified anatomy, flat "
            "2-3 tone rendering, short soft transitions between tonal "
            "areas, warm-light/cooler-shadow tendency, atmospheric "
            "background haze only. NOT hard cel-shading, NOT "
            "photorealistic, NOT chibi.",
            "",
            "VISUAL DETAIL DISCIPLINE",
            "Avoid decorative clutter. Do not recommend random temples, "
            "leaves, hats, props, or cultural symbols unless they are "
            "actually story-relevant. Every major object in a beat should "
            "serve story, mechanism, evidence, or atmosphere.",
            "",
            "PRIMARY / SECONDARY / CONTEXT",
            "primary_focus (required, non-blank) answers: what should the "
            "viewer look at first? secondary_elements support the primary "
            "mechanism/action; context_elements establish environment/"
            "scale/background. Both may be empty for a simple beat -- "
            "never pad them with artificial filler.",
            "",
            "MOTION INTENT -- descriptive only",
            "motion_intent is a short description (e.g. \"subtle "
            "parallax\", \"shadow shifts\", \"bridge deck oscillates\", "
            "\"arrow appears\", \"Tí eyebrow change\", \"camera push-in\"). "
            "Never include animation keyframes, exact durations, easing "
            "curves, or frame numbers -- those belong to a future "
            "production-planning phase.",
            "",
            "REUSE KEY AND REUSABLE ASSETS",
            "reuse_key may identify a reusable asset/scene (e.g. "
            "\"ti_home_base\", \"bridge_side_view\", \"earth_diagram_base\") "
            "-- not every beat needs one. VisualPlan.reusable_assets should "
            "summarize the likely-reusable assets across the whole plan; "
            "this is planning metadata, not an asset registry.",
            "",
            "SCRIPT LINE COVERAGE -- hard structural rule",
            "Every ScriptLine.line_id from the script below must appear in "
            "exactly one VisualBeat's script_line_ids, in the script's "
            "exact order. Never invent a line_id that isn't in the script. "
            "A beat's script_line_ids must be a contiguous run of the "
            "script's line order -- never skip a line and come back to it "
            "in a later beat.",
            "",
            "RESEARCHPACKAGE",
            f"- simplification_boundary: {research_package.simplification_boundary}",
            f"- sources available for EVIDENCE_MEDIA: {_sources_summary(research_package)}",
            "",
            "NARRATIVEPLAN",
            f"- central_question: {narrative_plan.central_question}",
            f"- ending: {narrative_plan.ending}",
            f"- question_ladder node ids (the only valid narrative_node "
            f"values): {[node.id for node in narrative_plan.question_ladder]}",
            "",
            "VOICEPLAN (rhythm context)",
            _voice_chunks_summary(voice_plan),
            "",
            "OUTPUT FORMAT",
            "Return ONLY a single JSON object matching this schema exactly "
            "-- no prose, no markdown code fences, no explanation:",
            schema_json,
            "",
            f"script_plan_id to use: {script_plan.id}",
            f"voice_plan_id to use: {voice_plan.id}",
        ]
    )


def build_user_prompt(script_plan: ScriptPlan, additional_context: str | None) -> str:
    lines = ["SCRIPT TO PLAN VISUALS FOR (in exact order; do not reorder):"]
    for beat in script_plan.beats:
        micro_hook = f", micro_hook={beat.micro_hook!r}" if beat.micro_hook else ""
        lines.append(f"Beat {beat.beat_id} (narrative_node={beat.narrative_node}{micro_hook}):")
        for line in beat.lines:
            visual_hint = f" visual_opportunity={line.visual_opportunity!r}" if line.visual_opportunity else ""
            lines.append(
                f"  {line.line_id} [{line.function.value}] "
                f"emotion={line.emotion!r} emphasis={line.emphasis!r}{visual_hint}: {line.text}"
            )

    if additional_context:
        lines += ["", "Additional context from the user:", additional_context]

    lines += ["", "Return your VisualPlan now."]
    return "\n".join(lines)


def build_correction_request(original_request: LLMRequest, issues: list[str]) -> LLMRequest:
    """One bounded business-correction request addressing every violation
    together. Does not touch the LLM layer's own JSON/schema correction
    logic (Phase 3, locked) -- this is the Visual Planning Engine's own
    business rule, exactly like every prior engine's business-correction
    step."""
    lines = [
        "Your previous response failed business validation. Problems found:",
        "\n".join(f"- {issue}" for issue in issues),
        "",
        "Fix ALL of the above together in your corrected response:",
        "- Every ScriptLine.line_id from the original script must appear "
        "in exactly one beat's script_line_ids, in the script's exact "
        "order, with no invented line_id.",
        "- A beat's script_line_ids must be a contiguous run of the "
        "script's line order.",
        "- Every beat's narrative_node must be a real question_ladder "
        "node id, and a beat may only cover lines from that one node.",
        "- Only EVIDENCE_MEDIA beats may have evidence_source_ids, and "
        "each one must be a real source_id from the ResearchPackage "
        "sources listed.",
        "- Keep C3 beats to at most 20% of total beats.",
        "",
        "Return a corrected JSON object matching the required schema exactly.",
        "",
        "Original task:",
        original_request.user_prompt,
    ]
    return original_request.model_copy(update={"user_prompt": "\n".join(lines)})


def _sources_summary(research_package: ResearchPackage) -> str:
    if not research_package.sources:
        return "(none available -- do not use EVIDENCE_MEDIA)"
    return "; ".join(
        f"{source.source_id} [{source.type}]: {source.title}" for source in research_package.sources
    )


def _voice_chunks_summary(voice_plan: VoicePlan) -> str:
    return "\n".join(
        f"- {chunk.chunk_id} (lines {chunk.line_ids}): energy={chunk.energy.value}, "
        f"pace={chunk.pace.value}, music_state={chunk.music_state.value}"
        for chunk in voice_plan.chunks
    )
