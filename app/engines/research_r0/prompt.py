"""The R0 Research Engine's business prompt. Owned here -- the LLM layer stays generic."""

from __future__ import annotations

import json

from app.llm.models import LLMRequest
from app.models.idea import IdeaCandidate
from app.models.research import ResearchR0
from app.research.models import RetrievedSource


def build_system_prompt(idea: IdeaCandidate) -> str:
    schema_json = json.dumps(ResearchR0.model_json_schema())

    return "\n".join(
        [
            "You are performing lightweight R0 discovery research for \"Một Tí Lý\", "
            "a science explainer YouTube channel.",
            "",
            "You are NOT establishing final factual truth, and this is NOT a "
            "definitive or exhaustive literature review. R1 research owns deep "
            "factual verification later; R0 is cheap discovery only.",
            "",
            "YOUR JOB",
            "Evaluate whether sufficient credible material appears to exist to "
            "justify deeper (R1) research into this idea. Answer, using only the "
            "idea and the retrieved evidence below:",
            "- Does this topic/story materially exist?",
            "- Are credible sources available?",
            "- Is there enough story material?",
            "- Is there enough physics material?",
            "- Is deeper R1 research justified?",
            "",
            "EVIDENCE RULES",
            "Use ONLY the approved IdeaCandidate below and the retrieved source "
            "evidence you are given. Do NOT invent, guess, or reference any source "
            "URL that was not explicitly supplied to you as retrieved evidence -- "
            "every URL you return in candidate_sources must be copied exactly from "
            "the supplied evidence, character for character.",
            "Do NOT claim to have read full sources -- you were only given titles, "
            "URLs, snippets, source names, and publication dates, never full page "
            "content.",
            "",
            "APPROVED IDEA CANDIDATE",
            json.dumps(idea.model_dump(mode="json")),
            f'Set idea_id to exactly this value: "{idea.id}"',
            "",
            "OUTPUT DISCIPLINE",
            "This is discovery research only. Do not write narrative structure, "
            "script lines, scene lists, or any content beyond the ResearchR0 "
            "fields.",
            "",
            "OUTPUT FORMAT",
            "Return ONLY a single JSON object matching this schema exactly -- no "
            "prose, no markdown code fences, no explanation:",
            schema_json,
        ]
    )


def build_user_prompt(evidence: list[RetrievedSource], additional_context: str | None) -> str:
    lines = ["RETRIEVED SOURCE EVIDENCE (discovery-level only, not verified):"]
    if not evidence:
        lines.append(
            "No sources were found by retrieval. Reflect this honestly -- "
            "credible_sources_available should likely be false, candidate_sources "
            "must be empty, and major_risks should note the absence of evidence."
        )
    else:
        for i, source in enumerate(evidence, start=1):
            lines.append(f"{i}. title: {source.title}")
            lines.append(f"   url: {source.url}")
            if source.snippet:
                lines.append(f"   snippet: {source.snippet}")
            if source.source_name:
                lines.append(f"   source_name: {source.source_name}")
            if source.published_at:
                lines.append(f"   published_at: {source.published_at.isoformat()}")

    if additional_context:
        lines += ["", "Additional context from the user:", additional_context]

    lines += ["", "Return your ResearchR0 discovery assessment now."]
    return "\n".join(lines)


def build_source_correction_request(
    original_request: LLMRequest,
    unknown_urls: list[str],
    evidence: list[RetrievedSource],
) -> LLMRequest:
    """One bounded business-correction request: the previous output cited URL(s)
    absent from the retrieved evidence. This does not touch the LLM layer's own
    JSON/schema correction logic (Phase 3, locked) -- it is R0's own business rule.
    """
    allowed_urls = "\n".join(f"- {source.url}" for source in evidence) or (
        "(no sources were retrieved -- candidate_sources must be an empty list)"
    )
    lines = [
        "Your previous response referenced source URL(s) that were not present in "
        "the retrieved evidence supplied to you:",
        "\n".join(f"- {url}" for url in unknown_urls),
        "",
        "Only URLs supplied to you as retrieved evidence may appear in "
        "candidate_sources. The complete set of URLs you may use is:",
        allowed_urls,
        "",
        "Return a corrected JSON object matching the required schema exactly, "
        "using only URLs from that list.",
        "",
        "Original task:",
        original_request.user_prompt,
    ]
    return original_request.model_copy(update={"user_prompt": "\n".join(lines)})
