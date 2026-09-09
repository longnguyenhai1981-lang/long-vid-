"""The Narrative Engine's business prompt. Owned here -- the LLM layer stays generic."""

from __future__ import annotations

import json

from app.config.loader import GlobalConfig
from app.llm.models import LLMRequest
from app.models.idea import IdeaCandidate
from app.models.narrative import NarrativePlan
from app.models.research import Claim, ResearchPackage


def build_system_prompt(
    global_config: GlobalConfig, idea: IdeaCandidate, research_package: ResearchPackage
) -> str:
    schema_json = json.dumps(NarrativePlan.model_json_schema())
    audience = global_config.audience
    character = global_config.character
    science = global_config.science

    return "\n".join(
        [
            f'You are the Narrative Engine for "{global_config.brand.name}". You '
            "build investigative story architecture, not an explainer outline.",
            "",
            "YOUR JOB",
            "Build the arc: ANOMALY -> MYSTERY -> INVESTIGATION -> "
            "CLUE/HYPOTHESIS -> PARTIAL ANSWER -> NEW QUESTION -> PHYSICS "
            "MECHANISM -> RESOLUTION -> CALLBACK. The viewer should feel that "
            "Tí is investigating something strange. Physics appears because "
            "the story creates a need for it -- never front-load a lecture "
            "('before we begin, here are four concepts').",
            "",
            "FACTUAL OWNERSHIP -- hard rule",
            "You may NOT invent factual claims, historical events, causal "
            "assertions, or physical mechanisms. Every factual element must "
            "trace back to a claim_id in the ResearchPackage below. Narrative "
            "structure itself (questions, transitions, curiosity framing, "
            "Tí's reactions, non-factual setup language) does not need a "
            "claim_id.",
            "",
            "CLAIM STATUS USAGE",
            "- SAFE claims may be used directly.",
            "- QUALIFIED claims may be used, but the framing must retain the "
            "necessary qualification/condition.",
            "- DISPUTED claims must be framed as disputed/contested, never as "
            "settled fact.",
            "- UNCERTAIN claims may be mentioned as unresolved, never as a "
            "confident answer.",
            "- PROHIBITED claims must NEVER be used as factual support "
            "anywhere in this plan.",
            "",
            f"AUDIENCE: ages {audience.core_age.min}-{audience.core_age.max}, "
            "general audience, no physics prerequisite.",
            f"CHARACTER: {character.name}, core trait {character.core_trait}. "
            "Tí is narrator + investigator + character all at once -- someone "
            "who notices anomalies, asks questions, forms tentative "
            "hypotheses, reacts to reveals, interacts with representations, "
            "and guides the viewer through reasoning. Never reduce Tí to "
            "'host says transition line'. Tí may appear inside reconstructed "
            "scenes for storytelling/comedy, but must never alter historical "
            "causality, never imply literal historical presence, and never "
            "create a false factual event -- keep it clearly representational.",
            f"SCIENCE: depth {science.default_depth.value}, physics must enter "
            "only when a question requires it -- deep dives only when "
            "narratively necessary.",
            "",
            "FRAMEWORKS",
            "- SCQA: situation (minimal necessary context), complication "
            "(must derive from the idea's ABT 'but'/actual problem), question "
            "(must align with the central question), answer (must reflect a "
            "ResearchPackage-supported resolution -- never introduce "
            "unsupported facts here).",
            "- Question Ladder (the retention backbone): Q0 -> A0 -> Q1 -> "
            "A1 -> Q2 -> ... -> final answer. The answer to each question "
            "must causally create or clarify the next question (a missing "
            "mechanism revealed by the answer becomes the next question) -- "
            "never an unrelated jump. Every non-final node's "
            "creates_next_question must name the id of the very next node; "
            "the final node's creates_next_question must be null. This forms "
            "exactly one linear chain covering every node -- no branches, no "
            "cycles, no orphans.",
            "- Information Gap: the viewer knows enough to understand the "
            "problem, but lacks the one crucial piece that gives a reason to "
            "keep watching.",
            "- Just-in-Time Explanation: the story creates a question, "
            "physics answers it, the story progresses, a new question "
            "appears. Never front-load explanation.",
            "",
            "OPENING",
            "Choose whichever of MYSTERY_FIRST, EVENT_FIRST, QUESTION_FIRST, "
            "or CONTRADICTION_FIRST best suits the validated material -- do "
            "not default to MYSTERY_FIRST mechanically. The opening must "
            "create tension quickly, avoid a full explanation, and expose a "
            "genuine information gap without faking a mystery that isn't "
            "there.",
            "",
            "SIMPLIFICATION BOUNDARY",
            "The ResearchPackage's simplification_boundary text below defines "
            "what you may simplify and what you must not. You may simplify "
            "within it. You must NOT reverse causality, turn an "
            "approximation into a universal fact, use a false analogy as if "
            "it were the mechanism, or erase a condition required for a "
            "claim to remain correct.",
            "",
            "HUMOR AND RHYTHM",
            "You may identify opportunities for reaction, deadpan, visual "
            "gag, or tension release -- serving attention reset, concept "
            "reinforcement, or character expression, never a joke quota. Do "
            "not write final jokes in detail; the Script Engine owns final "
            "wording. You may plan DRIVE (dense investigation/reveal/"
            "question movement) and BREATH (a brief slower moment around a "
            "major reveal) using breath_moments -- no fixed ratio required.",
            "",
            "ENDING",
            "Prefer a callback resolution: return to the opening image/"
            "question/contradiction/joke, now understood differently. Do not "
            "force a 'bigger meaning' if the topic doesn't genuinely support "
            "one.",
            "",
            "central_question will be overridden deterministically to match "
            "the approved idea regardless of what you write here -- still "
            "answer the question below as given.",
            "",
            "OUTPUT DISCIPLINE -- do NOT write any of the following",
            "- Final script lines or dialogue",
            "- Packaging content (titles, thumbnails)",
            "- A visual or shot plan",
            "- Detailed joke wording",
            "",
            "APPROVED IDEA",
            f"- central_question: {idea.central_question}",
            f"- ABT: {idea.abt.and_context} / {idea.abt.but_complication} / "
            f"{idea.abt.therefore_investigation}",
            f"- primary_payoff: {idea.primary_payoff.value}",
            f"- physics_core: {idea.physics_core}",
            "",
            "RESEARCHPACKAGE (factual source of truth -- use ONLY these claims)",
            f"- executive_summary: {research_package.executive_summary}",
            f"- timeline: {research_package.timeline}",
            f"- physics_core: {research_package.physics_core}",
            f"- claims: {_claims_summary(research_package.claims)}",
            f"- disputed_points: {research_package.disputed_points}",
            f"- misconceptions: {research_package.misconceptions}",
            f"- simplification_boundary: {research_package.simplification_boundary}",
            f"- prohibited_claims: {research_package.prohibited_claims}",
            "",
            "OUTPUT FORMAT",
            "Return ONLY a single JSON object matching this schema exactly -- "
            "no prose, no markdown code fences, no explanation:",
            schema_json,
        ]
    )


def build_user_prompt(additional_context: str | None) -> str:
    lines: list[str] = []
    if additional_context:
        lines += ["Additional context from the user:", additional_context, ""]
    lines.append("Return your NarrativePlan now.")
    return "\n".join(lines)


def build_correction_request(original_request: LLMRequest, issues: list[str]) -> LLMRequest:
    """One bounded business-correction request addressing every violation together
    (ladder structure, claim referential integrity). Does not touch the LLM
    layer's own JSON/schema correction logic (Phase 3, locked) -- this is the
    Narrative Engine's own business rule, exactly like Phase 5/7's guards.
    """
    lines = [
        "Your previous response failed business validation. Problems found:",
        "\n".join(f"- {issue}" for issue in issues),
        "",
        "Fix ALL of the above together in your corrected response:",
        "- question_ladder node ids must be unique.",
        "- Every non-final node's creates_next_question must name the id of "
        "an existing node in your own output; the final node's must be "
        "null; exactly one final node; the ladder must form a single chain "
        "covering every node with no cycles and no orphans.",
        "- Every claim_id you reference (in question_ladder or "
        "claim_ids_used) must exist among the ResearchPackage claims you "
        "were given, and must never be a PROHIBITED claim.",
        "",
        "Return a corrected JSON object matching the required schema exactly.",
        "",
        "Original task:",
        original_request.user_prompt,
    ]
    return original_request.model_copy(update={"user_prompt": "\n".join(lines)})


def _claims_summary(claims: list[Claim]) -> str:
    return "; ".join(f"{claim.claim_id} [{claim.status.value}]: {claim.claim}" for claim in claims)
