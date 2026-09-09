"""The R1 Research Engine's business prompt. Owned here -- the LLM layer stays generic.

ResearchPackage.simplification_boundary is a locked, plain `str` field (see
docs/TECHNICAL_SPEC_v0.1.md, Phase 7 -- Spec Deviations). The four
components the approved research process calls for (safe_model,
allowed_simplifications, omitted_complexity, dangerous_oversimplifications)
are therefore required as labeled content WITHIN that one string, not as
separate schema fields.
"""

from __future__ import annotations

import json

from app.config.loader import GlobalConfig
from app.llm.models import LLMRequest
from app.models.feasibility import FeasibilityReport
from app.models.idea import IdeaCandidate
from app.models.research import ResearchPackage, ResearchR0
from app.research.models import RetrievedSource


def build_system_prompt(
    global_config: GlobalConfig,
    idea: IdeaCandidate,
    research_r0: ResearchR0,
    feasibility: FeasibilityReport,
) -> str:
    schema_json = json.dumps(ResearchPackage.model_json_schema())
    science = global_config.science

    return "\n".join(
        [
            f'You are performing R1 deep research for "{global_config.brand.name}".',
            "",
            "Your output becomes the FACTUAL SOURCE OF TRUTH for downstream "
            "narrative and script generation. Nothing written later may "
            "contradict what you establish here.",
            "",
            "You are NOT a script writer, narrative writer, or packaging "
            "generator, and this is NOT an exhaustive academic literature "
            "review. Aim for sufficient research, not exhaustive research: the "
            "central question is answerable, the core mechanism is mapped, "
            "important factual claims have evidence, major disputes are "
            "mapped, and the simplification boundary is clear.",
            "",
            f"Science accuracy priority: {science.accuracy_priority.value}. "
            f"Default depth: {science.default_depth.value}. This is for a "
            "general audience with no physics prerequisite -- fun but "
            "scientifically sound.",
            "",
            "EVIDENCE RULES",
            "Use ONLY the retrieved evidence supplied to you and the prior "
            "validated project artifacts given below. Do NOT invent sources -- "
            "every Source.url you return must be copied exactly from the "
            "supplied evidence. Do NOT claim to have read full documents or "
            "papers -- you were only given title/URL/snippet/source-name/"
            "publication-date metadata, never full page or document content.",
            "",
            "SOURCE QUALITY HIERARCHY (classify quality_tier accordingly)",
            "- TIER 1 (primary/authoritative): original papers, official "
            "agencies, universities, standards bodies, technical reports, "
            "archives.",
            "- TIER 2 (high-quality secondary): reviews, textbook-like "
            "sources, academic encyclopedias, reputable institutional "
            "explainers, reputable science journalism.",
            "- TIER 3 (discovery aid): Wikipedia, blogs, forums, Reddit, "
            "other videos. Tier 3 may help discovery, but important factual "
            "claims should not rely solely on Tier 3 if better evidence is "
            "available to you.",
            "",
            "CLAIM LEDGER -- every claim gets exactly one status",
            "- SAFE: can be stated directly at popular-science level within "
            "the simplification boundary.",
            "- QUALIFIED: can be stated, but requires an explicit condition, "
            "context, or limitation alongside it.",
            "- UNCERTAIN: the evidence available to you is insufficient for a "
            "strong claim.",
            "- DISPUTED: credible sources materially disagree, or "
            "interpretation remains genuinely contested. Do not create false "
            "balance -- a single weak fringe source is not enough to call "
            "something disputed; weigh source quality, independence, and "
            "whether the disagreement is substantive.",
            "- PROHIBITED: downstream content must not state this claim. "
            "This is a factual/safety boundary, not a way to flag something "
            "as merely uninteresting.",
            "SAFE, QUALIFIED, and DISPUTED claims each need at least one "
            "source_id. UNCERTAIN and PROHIBITED claims may have zero. Core "
            "physics claims and decisive historical claims should preferably "
            "cite multiple independent sources where the retrieved evidence "
            "permits it -- but a single genuinely authoritative source is "
            "acceptable when that is all that exists.",
            "",
            "SIMPLIFICATION BOUNDARY",
            "simplification_boundary is a single text field. Write it "
            "covering these four labeled parts explicitly, in order:",
            "1. safe_model: the mental model downstream content is allowed "
            "to use.",
            "2. allowed_simplifications: what may be simplified or omitted "
            "safely.",
            "3. omitted_complexity: what is being left out and why that's "
            "acceptable.",
            "4. dangerous_oversimplifications: what must NOT happen -- e.g. "
            "reversing cause/effect, turning an approximation into an "
            "absolute law, replacing the actual mechanism with a false "
            "analogy, or erasing a condition required for a claim to remain "
            "correct.",
            "",
            "MISCONCEPTIONS",
            "Only note a misconception when the evidence or context actually "
            "supports it. Do not fabricate 'most people believe X' -- if you "
            "cannot support that framing, word it more cautiously, e.g. 'a "
            "common simplified explanation is...'.",
            "",
            "PROHIBITED CLAIMS",
            "prohibited_claims should capture statements downstream modules "
            "must not make -- e.g. an unsupported historical anecdote, an "
            "overstated causal claim, a disproven explanation, or an "
            "unresolved claim presented as settled fact. These are "
            "guardrails; do not omit real ones.",
            "",
            "central_question will be overridden deterministically to match "
            "the approved idea regardless of what you write here -- still "
            "answer the question below as given, do not redirect the "
            "research toward a different question.",
            "",
            "APPROVED IDEA",
            f"- topic: {idea.topic}",
            f"- central_question: {idea.central_question}",
            f"- ABT: {idea.abt.and_context} / {idea.abt.but_complication} / "
            f"{idea.abt.therefore_investigation}",
            f"- physics_core: {idea.physics_core}",
            f"- research_questions: {idea.research_questions}",
            f"- risks: {idea.risks}",
            "",
            "R0 DISCOVERY FINDINGS (provisional, already screened)",
            f"- initial_findings: {research_r0.initial_findings}",
            f"- candidate_sources: {research_r0.candidate_sources}",
            f"- major_risks: {research_r0.major_risks}",
            f"- recommendation: {research_r0.recommendation.value}",
            "",
            "FEASIBILITY NOTES",
            f"- science: {feasibility.science.status.value} -- {feasibility.science.reason}",
            f"- narrative: {feasibility.narrative.status.value} -- {feasibility.narrative.reason}",
            "",
            "OUTPUT DISCIPLINE -- do NOT write any of the following",
            "- A script or dialogue",
            "- A narrative outline or NarrativePlan",
            "- Packaging content (titles, thumbnails)",
            "- A visual plan or image prompts",
            "",
            "OUTPUT FORMAT",
            "Return ONLY a single JSON object matching this schema exactly -- "
            "no prose, no markdown code fences, no explanation:",
            schema_json,
        ]
    )


def build_user_prompt(evidence: list[RetrievedSource], additional_context: str | None) -> str:
    lines = ["RETRIEVED SOURCE EVIDENCE (metadata/snippets only, not full documents):"]
    if not evidence:
        lines.append(
            "No sources were retrieved. Reflect this honestly: claims should "
            "mostly be UNCERTAIN, sources should be empty, and this "
            "limitation should be visible in the executive_summary."
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

    lines += ["", "Return your ResearchPackage now."]
    return "\n".join(lines)


def build_correction_request(
    original_request: LLMRequest,
    issues: list[str],
    evidence: list[RetrievedSource],
) -> LLMRequest:
    """One bounded business-correction request addressing every violation together
    (URL provenance, referential integrity, duplicate ids, evidence-by-status).
    Does not touch the LLM layer's own JSON/schema correction logic (Phase 3,
    locked) -- this is R1's own business rule, exactly like Phase 5's R0 guard.
    """
    allowed_urls = "\n".join(f"- {source.url}" for source in evidence) or (
        "(no sources were retrieved -- sources must be an empty list)"
    )
    lines = [
        "Your previous response failed business validation. Problems found:",
        "\n".join(f"- {issue}" for issue in issues),
        "",
        "Fix ALL of the above together in your corrected response:",
        "- Every Source.url must be copied exactly from this allowed list:",
        allowed_urls,
        "- Every Claim.source_ids entry must reference a source_id that "
        "exists among your own returned sources.",
        "- Every Source.supports_claims entry must reference a claim_id "
        "that exists among your own returned claims.",
        "- claim_id values must be unique; source_id values must be unique.",
        "- SAFE, QUALIFIED, and DISPUTED claims must each have at least one "
        "source_id.",
        "",
        "Return a corrected JSON object matching the required schema exactly.",
        "",
        "Original task:",
        original_request.user_prompt,
    ]
    return original_request.model_copy(update={"user_prompt": "\n".join(lines)})
