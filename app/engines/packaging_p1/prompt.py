"""The Packaging P1 Engine's business prompt. Owned here -- the LLM layer
stays generic."""

from __future__ import annotations

import json

from app.config.loader import GlobalConfig
from app.llm.models import LLMRequest
from app.models.assembly import AssemblyPlan
from app.models.narrative import NarrativePlan
from app.models.packaging import PackagingPrototype
from app.models.packaging_p1 import FinalPackagingPlan
from app.models.research import ResearchPackage
from app.models.script import ScriptPlan
from app.models.visual import VisualPlan


def build_system_prompt(
    global_config: GlobalConfig,
    packaging_prototype: PackagingPrototype,
    research_package: ResearchPackage,
    narrative_plan: NarrativePlan,
    script_plan: ScriptPlan,
    visual_plan: VisualPlan,
    assembly_plan: AssemblyPlan,
) -> str:
    schema_json = json.dumps(FinalPackagingPlan.model_json_schema())

    return "\n".join(
        [
            f'You are the final packaging editor for "{global_config.brand.name}". '
            "The video already exists conceptually -- the script, voice "
            "delivery, visual strategy, and assembly timing are all "
            "already planned and locked. Your job is NOT to invent a "
            "better video. Your job is to package THIS video as strongly "
            "and honestly as possible: decide the final title, the final "
            "thumbnail creative direction, and the final promise "
            "statement.",
            "",
            "P0 -> P1 RELATIONSHIP",
            "Packaging P0 already answered whether this video's promise "
            "was worth scripting at all. You are answering a different "
            "question: now that the video is fully planned, what should "
            "the final title and thumbnail promise actually be? You may "
            "sharpen, simplify, or make P0's wording more dramatic, and "
            "you should normally stay recognizably connected to P0's "
            "promise and viewer_expectation below. If your final packaging "
            "materially changes the promise instead of refining it, treat "
            "that as a real risk and reflect it honestly in "
            "risk_of_misleading -- do not silently turn this into a "
            "different video's promise.",
            "",
            "ONE FINAL RECOMMENDATION ONLY",
            "Return exactly one title, one thumbnail_concept, and one "
            "thumbnail_text choice. Do not return Option A/B/C, do not "
            "list alternatives in notes, and do not create any internal "
            "candidate-list structure. You may reason through options "
            "privately, but the output is your single final "
            "recommendation.",
            "",
            "FINAL TITLE",
            "Write real, final Vietnamese title wording now -- curiosity-"
            "driving, drama-first, understandable without any physics "
            "prerequisite, reasonably concise, and specific enough to "
            "imply a real question or tension. It must be defensible by "
            "the final script below. Do not optimize for keyword "
            "stuffing. Do not mechanically force a \"Vì sao...\" question "
            "framing onto every title -- a strong declarative or dramatic "
            "statement is equally valid; use whichever framing this "
            "specific video actually earns. There is no hard character "
            "limit, but prefer wording a viewer can scan quickly.",
            "",
            "NO FALSE TITLE -- hard principle",
            "The title must never imply: an event that did not happen; a "
            "mechanism the research does not support; certainty where "
            "ResearchPackage is uncertain or disputed; a payoff the "
            "ScriptPlan does not actually deliver; or a scale/drama "
            "materially stronger than the evidence below actually "
            "supports.",
            "",
            "PAYOFF OWNERSHIP",
            "Only sell a payoff that genuinely exists in the NarrativePlan "
            "and ScriptPlan below. Do not promise a reversal if the "
            "script contains no real reversal. Do not claim \"scientists "
            "were wrong\" or similar unless ResearchPackage actually "
            "supports that framing.",
            "",
            "RESEARCH SAFETY",
            "Respect ResearchPackage's claims, disputed_points, "
            "prohibited_claims, and simplification_boundary below. Never "
            "invent a factual detail. Never turn a claim whose status is "
            "QUALIFIED into an absolute, unqualified statement in the "
            "title or thumbnail if that qualification is material to the "
            "claim's honesty.",
            "",
            "THUMBNAIL ROLE",
            "The thumbnail should communicate exactly ONE visual conflict "
            "-- it does not summarize the whole video. Preferred "
            "hierarchy: one dominant subject, plus one visual "
            "contradiction/conflict, plus an optional Tí reaction, plus a "
            "few emphasis words only if useful.",
            "",
            "THUMBNAIL TEXT",
            "Prefer 0-4 words; shorter is usually better, and this is "
            "guidance, not a hard limit -- do not fail a genuinely strong "
            "thumbnail_text over a single extra word if Vietnamese "
            "phrasing needs it. Never write paragraph-length text.",
            "",
            "TITLE / THUMBNAIL COMPLEMENT",
            "The title and thumbnail_text must not merely repeat each "
            "other -- deliberately make them complementary, each adding "
            "information the other doesn't. Bad: title \"Gió phá cây cầu "
            "như thế nào?\" paired with thumbnail_text \"GIÓ PHÁ CẦU?\" "
            "(pure repetition). Better: title \"Chiếc cầu tự xé nát "
            "chính nó\" paired with thumbnail_text \"CHỈ VÌ GIÓ?\" (the "
            "thumbnail adds a disbelieving question the title doesn't "
            "ask).",
            "",
            "TÍ IN THE THUMBNAIL",
            "Tí appears in most thumbnails by brand preference, but there "
            "is no hard quota -- include Tí only when the reaction "
            "improves attention, clarifies the emotional framing, or "
            "strengthens brand recognition. Never let Tí visually cover "
            "or obscure the actual visual conflict.",
            "",
            "VISUAL FEASIBILITY",
            "The thumbnail concept must be feasible using the actual "
            "VisualPlan language below -- its dominant objects, generated-"
            "still/diagram concepts, Tí states, hero visuals, and "
            "reusable assets. Do not invent a completely unrelated visual "
            "subject merely because it sounds clickable.",
            "",
            "OPENING-HOOK CONSISTENCY",
            "Using AssemblyPlan and ScriptPlan below, check that the "
            "video's opening quickly begins paying off the promise you're "
            "making -- packaging should not require several minutes of "
            "unrelated setup before the viewer understands why they "
            "clicked. This is holistic judgment, not a rigid timing "
            "formula.",
            "",
            "THUMBNAIL CONCEPT FORMAT -- creative direction, NOT an image prompt",
            "thumbnail_concept should describe the dominant subject, the "
            "conflict, Tí's presence/state if any, and visual emphasis -- "
            "in plain creative-direction language. Good example: "
            "\"Bridge deck twisting sharply while the towers remain "
            "upright; Tí small in lower-right looking confused; wind "
            "represented only as minimal directional streaks.\" Never "
            "write a production image-generation prompt: no Midjourney or "
            "Stable Diffusion syntax, no aspect-ratio parameters, no lens "
            "or camera terms, no rendering-engine terms, no negative "
            "prompts, and no generation parameters of any kind. A "
            "renderer handles that later; you are not that renderer.",
            "",
            "OUTPUT DISCIPLINE -- do NOT produce any of the following",
            "- An actual thumbnail image or any image-generation prompt.",
            "- Multiple title/thumbnail options or an A/B menu.",
            "- YouTube upload metadata beyond the packaging fields "
            "themselves (no tags, no description, no category).",
            "",
            "RISK ASSESSMENT",
            "risk_of_misleading: LOW means the packaging is strong and "
            "fully defensible by the evidence and script. MEDIUM means "
            "usable, but the wording or visual needs care. HIGH means "
            "this packaging should not actually be used as final "
            "packaging -- report HIGH honestly when it applies; a HIGH "
            "result is still recorded, not rejected.",
            "",
            "PACKAGING P0 (starting point -- refine, don't silently replace)",
            f"- promise: {packaging_prototype.promise}",
            f"- title_direction: {packaging_prototype.title_direction}",
            f"- thumbnail_conflict: {packaging_prototype.thumbnail_conflict}",
            f"- viewer_expectation: {packaging_prototype.viewer_expectation}",
            f"- risk_of_misleading: {packaging_prototype.risk_of_misleading.value}",
            "",
            "RESEARCHPACKAGE",
            f"- central_question: {research_package.central_question}",
            f"- claims: {_claims_summary(research_package)}",
            f"- disputed_points: {research_package.disputed_points}",
            f"- prohibited_claims: {research_package.prohibited_claims}",
            f"- simplification_boundary: {research_package.simplification_boundary}",
            "",
            "NARRATIVEPLAN",
            f"- central_question: {narrative_plan.central_question}",
            f"- opening: {narrative_plan.opening.value}",
            f"- ending: {narrative_plan.ending}",
            "",
            "SCRIPTPLAN",
            f"- estimated_duration_seconds: {script_plan.estimated_duration_seconds}",
            f"- beat count: {len(script_plan.beats)}",
            _script_highlights_summary(script_plan),
            "",
            "VISUALPLAN",
            _visual_beats_summary(visual_plan),
            "",
            "ASSEMBLYPLAN",
            f"- estimated_total_duration_seconds: {assembly_plan.estimated_total_duration_seconds}",
            _assembly_segments_summary(assembly_plan),
            "",
            "OUTPUT FORMAT",
            "Return ONLY a single JSON object matching this schema exactly "
            "-- no prose, no markdown code fences, no explanation:",
            schema_json,
        ]
    )


def build_user_prompt(additional_context: str | None) -> str:
    lines: list[str] = []
    if additional_context:
        lines += ["Additional context from the user:", additional_context, ""]
    lines.append("Return your FinalPackagingPlan now.")
    return "\n".join(lines)


def build_correction_request(original_request: LLMRequest, issues: list[str]) -> LLMRequest:
    """One bounded business-correction request. Does not touch the LLM
    layer's own JSON/schema correction logic (Phase 3, locked) -- this is
    the Packaging P1 Engine's own business rule. Deterministic ids (the
    four upstream-artifact references) are never the reason a correction
    is needed -- the engine normalizes those itself -- so this request
    only ever lists genuinely blank required fields."""
    lines = [
        "Your previous response failed business validation. Problems found:",
        "\n".join(f"- {issue}" for issue in issues),
        "",
        "Fix ALL of the above together: every listed field must contain "
        "real, non-blank content.",
        "",
        "Return a corrected JSON object matching the required schema exactly.",
        "",
        "Original task:",
        original_request.user_prompt,
    ]
    return original_request.model_copy(update={"user_prompt": "\n".join(lines)})


def _claims_summary(research_package: ResearchPackage) -> str:
    return "; ".join(
        f"{claim.claim_id} [{claim.status.value}]: {claim.claim}" for claim in research_package.claims
    )


def _script_highlights_summary(script_plan: ScriptPlan) -> str:
    if not script_plan.beats:
        return "- (no beats)"

    lines = ["- opening lines:"]
    for line in script_plan.beats[0].lines:
        lines.append(f"    {line.line_id}: {line.text}")

    reveal_lines = [
        line
        for beat in script_plan.beats
        for line in beat.lines
        if line.function.value == "REVEAL"
    ]
    if reveal_lines:
        lines.append("- key reveal/payoff lines:")
        for line in reveal_lines:
            lines.append(f"    {line.line_id}: {line.text}")

    if len(script_plan.beats) > 1:
        lines.append("- ending lines:")
        for line in script_plan.beats[-1].lines:
            lines.append(f"    {line.line_id}: {line.text}")

    return "\n".join(lines)


def _visual_beats_summary(visual_plan: VisualPlan) -> str:
    return "\n".join(
        f"- {beat.beat_id}: concept={beat.concept!r}, primary_focus={beat.primary_focus!r}, "
        f"media_type={beat.media_type.value}, complexity={beat.complexity.value}, "
        f"ti_state={beat.ti_state.value if beat.ti_state else None}, reuse_key={beat.reuse_key!r}"
        for beat in visual_plan.beats
    )


def _assembly_segments_summary(assembly_plan: AssemblyPlan) -> str:
    return "\n".join(
        f"- {segment.segment_id} (visual_beat={segment.visual_beat_id}, "
        f"{segment.start_seconds}s-{segment.end_seconds}s): "
        f"transition_in={segment.transition_in.value}, transition_out={segment.transition_out.value}"
        for segment in assembly_plan.segments
    )
