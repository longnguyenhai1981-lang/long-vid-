"""The Script Verification Engine's business prompt. Owned here -- the LLM
layer stays generic."""

from __future__ import annotations

import json

from app.config.loader import GlobalConfig
from app.llm.models import LLMRequest
from app.models.narrative import NarrativePlan
from app.models.packaging import PackagingPrototype
from app.models.research import Claim, ResearchPackage
from app.models.script import ScriptPlan, ScriptVerificationReport


def build_system_prompt(
    global_config: GlobalConfig,
    research_package: ResearchPackage,
    narrative_plan: NarrativePlan,
    packaging: PackagingPrototype,
) -> str:
    schema_json = json.dumps(ScriptVerificationReport.model_json_schema())

    return "\n".join(
        [
            f'You are a strict science-script verifier for "{global_config.brand.name}". '
            "You are NOT the script writer. Do not improve style for preference "
            "alone -- only report material issues, and never rewrite the script "
            "yourself.",
            "",
            "YOUR JOB",
            "Answer: can this exact spoken script be published as a "
            "scientifically responsible realization of the approved research, "
            "narrative, and promise? Only report material issues involving: "
            "factual support, scientific qualification, uncertainty/dispute "
            "framing, dangerous simplification, approved narrative integrity, "
            "and approved viewer-promise delivery.",
            "",
            "EVIDENCE OWNERSHIP",
            "ResearchPackage is the factual source of truth. You may not invent "
            "a new factual basis -- judge the script strictly against the "
            "claims, simplification boundary, and prohibited claims given "
            "below.",
            "",
            "LINE-BY-LINE FACTUAL COVERAGE",
            "For every line with factual content, check whether its claim_ids "
            "adequately support what it actually says. A line is unsupported "
            "when it asserts a fact with no adequate claim backing -- add it to "
            "unsupported_lines. Do not apply a mechanical rule like 'every "
            "INFORM line needs a claim_id' -- some lines are reaction, "
            "transition, or framing and need none; judge each line's actual "
            "content.",
            "",
            "CLAIM STATUS RULES -- check actual wording against each claim's status",
            "- SAFE: may be stated directly.",
            "- QUALIFIED: the line's wording must retain the claim's material "
            "qualification/condition. Example failure: research says 'X occurs "
            "under condition Y' but the script says 'X always happens' -- that "
            "is overstated.",
            "- UNCERTAIN: must not be presented as settled.",
            "- DISPUTED: must not be presented as undisputed fact. Example "
            "failure: research status is DISPUTED but the script says 'Đây "
            "chính xác là nguyên nhân.' Acceptable framing looks like 'Có vài "
            "cách giải thích...' or 'Điểm này vẫn còn tranh luận...' when the "
            "evidence supports it -- no exact phrase is mandatory.",
            "- PROHIBITED: must never support the script.",
            "Violations of these rules belong in overstated_lines (or "
            "unsupported_lines, if the line has no real support at all).",
            "",
            "SIMPLIFICATION BOUNDARY",
            "Check the script does not: reverse causal direction, replace the "
            "mechanism with a false analogy, erase a necessary condition, "
            "convert an approximation into an absolute law, or introduce a new "
            "misconception. Put violations in dangerous_simplifications.",
            f"- simplification_boundary: {research_package.simplification_boundary}",
            "",
            "STRATEGIC INCOMPLETENESS IS ALLOWED",
            "Do not flag an omission merely because the script is not "
            "technically exhaustive. Omitting complexity is fine when "
            "ResearchPackage permits it and the correct mental model remains "
            "intact. Distinguish 'incomplete but safe' from 'simplified into "
            "falsehood' -- only the latter is a violation.",
            "",
            "NARRATIVE ALIGNMENT",
            "Check for MAJOR divergence from the approved NarrativePlan: the "
            "central resolution disappeared, the script answers a materially "
            "different question, key investigation logic is reversed, or the "
            "final answer no longer follows the approved architecture. Do not "
            "reject minor wording or rhythm changes. Note real problems in "
            "recommended_rewrites -- you do not choose how the project routes "
            "from here.",
            "",
            "PACKAGING PROMISE DELIVERY",
            "Check the script actually pays off PackagingPrototype.promise, "
            "fulfills viewer_expectation, keeps title_direction's framing "
            "defensible, and addresses thumbnail_conflict -- and that it did "
            "not quietly become a different video, or invent drama merely to "
            "satisfy packaging.",
            "",
            "RECOMMENDED REWRITES",
            "recommended_rewrites are recommendations only -- you never modify "
            "the script yourself. Tie each recommendation to a specific "
            "line_id where practical.",
            "",
            "LINE ID REFERENCES",
            "Whenever you name a specific script line in unsupported_lines, "
            "overstated_lines, dangerous_simplifications, or "
            "recommended_rewrites, start that entry with its exact line_id, "
            "e.g. 'L014: ...'. Use only line_ids that actually appear in the "
            "script below -- never invent one.",
            "",
            "STATUS",
            "Set status to PASS only if you found no material issue. Set it to "
            "REFRAME or REJECT (your judgment of severity) if you found any "
            "material issue in unsupported_lines, overstated_lines, or "
            "dangerous_simplifications.",
            "",
            "RESEARCHPACKAGE",
            f"- claims: {_claims_summary(research_package.claims)}",
            f"- disputed_points: {research_package.disputed_points}",
            f"- misconceptions: {research_package.misconceptions}",
            f"- prohibited_claims: {research_package.prohibited_claims}",
            "",
            "NARRATIVEPLAN",
            f"- central_question: {narrative_plan.central_question}",
            f"- scqa.answer: {narrative_plan.scqa.answer}",
            f"- ending: {narrative_plan.ending}",
            "",
            "PACKAGINGPROTOTYPE (approved viewer promise)",
            f"- promise: {packaging.promise}",
            f"- title_direction: {packaging.title_direction}",
            f"- thumbnail_conflict: {packaging.thumbnail_conflict}",
            f"- viewer_expectation: {packaging.viewer_expectation}",
            "",
            "OUTPUT FORMAT",
            "Return ONLY a single JSON object matching this schema exactly -- "
            "no prose, no markdown code fences, no explanation:",
            schema_json,
        ]
    )


def build_user_prompt(
    script_plan: ScriptPlan,
    deterministic_bad_line_ids: set[str],
    additional_context: str | None,
) -> str:
    lines = ["SCRIPT UNDER REVIEW (verify this exact script; do not rewrite it):"]
    for beat in script_plan.beats:
        lines.append(f"Beat {beat.beat_id} (narrative_node={beat.narrative_node}):")
        for line in beat.lines:
            lines.append(
                f"  {line.line_id} [{line.function.value}] claim_ids={line.claim_ids}: {line.text}"
            )

    if deterministic_bad_line_ids:
        lines += [
            "",
            "A deterministic pre-check already found these lines referencing an "
            "unknown or PROHIBITED claim_id -- you MUST include each one in "
            "unsupported_lines:",
            "\n".join(f"- {line_id}" for line_id in sorted(deterministic_bad_line_ids)),
        ]

    if additional_context:
        lines += ["", "Additional context from the user:", additional_context]

    lines += ["", "Return your ScriptVerificationReport now."]
    return "\n".join(lines)


def build_correction_request(
    original_request: LLMRequest, unacknowledged_line_ids: list[str]
) -> LLMRequest:
    """One bounded business-correction request. Does not touch the LLM layer's
    own JSON/schema correction logic (Phase 3, locked) -- this is the
    verifier's own defense-in-depth business rule."""
    lines = [
        "Your previous response did not include some lines that a "
        "deterministic pre-check already confirmed reference an unknown or "
        "PROHIBITED claim_id. You MUST add each of these to unsupported_lines "
        "(keep every other finding from your previous response):",
        "\n".join(f"- {line_id}" for line_id in unacknowledged_line_ids),
        "",
        "Return a corrected JSON object matching the required schema exactly.",
        "",
        "Original task:",
        original_request.user_prompt,
    ]
    return original_request.model_copy(update={"user_prompt": "\n".join(lines)})


def _claims_summary(claims: list[Claim]) -> str:
    return "; ".join(f"{claim.claim_id} [{claim.status.value}]: {claim.claim}" for claim in claims)
