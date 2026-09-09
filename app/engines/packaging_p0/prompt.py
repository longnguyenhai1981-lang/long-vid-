"""The Packaging P0 Engine's business prompt. Owned here -- the LLM layer stays generic."""

from __future__ import annotations

import json

from app.config.loader import GlobalConfig
from app.llm.models import LLMRequest
from app.models.idea import IdeaCandidate
from app.models.narrative import NarrativePlan
from app.models.packaging import PackagingPrototype
from app.models.research import Claim, ResearchPackage


def build_system_prompt(
    global_config: GlobalConfig,
    idea: IdeaCandidate,
    research_package: ResearchPackage,
    narrative_plan: NarrativePlan,
) -> str:
    schema_json = json.dumps(PackagingPrototype.model_json_schema())
    audience = global_config.audience

    return "\n".join(
        [
            f'You are the Packaging P0 Engine for "{global_config.brand.name}". You '
            "test whether the approved narrative can be expressed as a strong, "
            "honest viewer promise BEFORE script writing begins. This is NOT final "
            "title/thumbnail production -- no final wording, no thumbnail image, no "
            "visual composition.",
            "",
            "YOUR JOB",
            "Answer: What is the core promise? What drama/tension should packaging "
            "sell? What does the viewer expect after clicking? Can the video "
            "actually deliver that promise? Is the framing misleading?",
            "",
            f"AUDIENCE: ages {audience.core_age.min}-{audience.core_age.max}, "
            "general audience.",
            "",
            "PACKAGING STYLE: drama-first. The viewer should think \"what the hell "
            "happened here?\" -- but the final video must actually answer it. "
            "Strong framing is welcome; false framing is not.",
            "",
            "ONE RECOMMENDATION RULE",
            "Return exactly ONE recommended prototype -- not 10 titles, not 5 "
            "thumbnail concepts, not an A/B/C menu. Analyze internally, then commit "
            "to your single best recommendation.",
            "",
            "TITLE / THUMBNAIL COMPLEMENTARITY",
            "title_direction and thumbnail_conflict must complement each other, not "
            "duplicate the same idea in different words. Weak example: title 'Why "
            "did the bridge twist?' + thumbnail 'WHY DID IT TWIST?' (the same "
            "question twice). Better: title direction 'The bridge tore itself "
            "apart' + thumbnail conflict 'CAN WIND REALLY DO THIS?' (two different "
            "angles on the same promise). Both are conceptual directions, not final "
            "wording -- do not write a literal final title string or design a "
            "literal thumbnail image.",
            "",
            "PROMISE INTEGRITY -- hard rule",
            "The promise must be deliverable by the ResearchPackage and "
            "NarrativePlan below. Do NOT: claim something stronger than "
            "ResearchPackage supports; fabricate a contradiction only for clicks; "
            "assert a historical fact absent from ResearchPackage; promise a "
            "payoff that is not present in NarrativePlan; frame a reversal when "
            "the research does not support one. Packaging may dramatize the "
            "central_question's framing but must not silently sell a different "
            "video.",
            "",
            "RISK OF MISLEADING",
            "Set risk_of_misleading honestly: LOW means strong but defensible; "
            "MEDIUM means it requires careful phrasing or qualification to stay "
            "honest; HIGH means this framing should not be approved in its "
            "current form. Do not soften a genuinely HIGH-risk framing to LOW just "
            "to look better -- an honest HIGH result is more useful than a "
            "dishonest LOW.",
            "",
            "APPROVED IDEA",
            f"- central_question: {idea.central_question}",
            f"- primary_payoff: {idea.primary_payoff.value}",
            "",
            "RESEARCHPACKAGE (factual source of truth)",
            f"- executive_summary: {research_package.executive_summary}",
            f"- prohibited_claims: {research_package.prohibited_claims}",
            f"- claims: {_claims_summary(research_package.claims)}",
            "",
            "NARRATIVEPLAN (story architecture -- the payoff you may promise)",
            f"- scqa.answer: {narrative_plan.scqa.answer}",
            f"- opening: {narrative_plan.opening.value}",
            f"- ending: {narrative_plan.ending}",
            "",
            "OUTPUT DISCIPLINE -- do NOT produce any of the following",
            "- A final title string or final thumbnail image/design",
            "- A visual composition or shot plan",
            "- Multiple candidate title/thumbnail options",
            "- Script or narrative content",
            "",
            "OUTPUT FORMAT",
            "Return ONLY a single JSON object matching this schema exactly -- no "
            "prose, no markdown code fences, no explanation:",
            schema_json,
        ]
    )


def build_user_prompt(additional_context: str | None) -> str:
    lines: list[str] = []
    if additional_context:
        lines += ["Additional context from the user:", additional_context, ""]
    lines.append("Return your PackagingPrototype now.")
    return "\n".join(lines)


def build_correction_request(original_request: LLMRequest, issues: list[str]) -> LLMRequest:
    """One bounded business-correction request. Does not touch the LLM layer's
    own JSON/schema correction logic (Phase 3, locked) -- this is Packaging
    P0's own business rule, exactly like Phase 5/7/8's guards."""
    lines = [
        "Your previous response failed business validation. Problems found:",
        "\n".join(f"- {issue}" for issue in issues),
        "",
        "Fix ALL of the above together: promise, title_direction, "
        "thumbnail_conflict, and viewer_expectation must each be a genuine, "
        "non-blank value.",
        "",
        "Return a corrected JSON object matching the required schema exactly.",
        "",
        "Original task:",
        original_request.user_prompt,
    ]
    return original_request.model_copy(update={"user_prompt": "\n".join(lines)})


def _claims_summary(claims: list[Claim]) -> str:
    return "; ".join(f"{claim.claim_id} [{claim.status.value}]: {claim.claim}" for claim in claims)
