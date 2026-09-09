"""The Feasibility Engine's business prompt. Owned here -- the LLM layer stays generic."""

from __future__ import annotations

import json

from app.config.loader import GlobalConfig
from app.models.idea import IdeaCandidate
from app.models.feasibility import FeasibilityReport
from app.models.research import ResearchR0


def build_system_prompt(global_config: GlobalConfig) -> str:
    schema_json = json.dumps(FeasibilityReport.model_json_schema())
    audience = global_config.audience
    production = global_config.production

    return "\n".join(
        [
            f'You are evaluating production feasibility for "{global_config.brand.name}", '
            "a science explainer YouTube channel.",
            "",
            "YOUR JOB",
            'Answer: "Should Một Tí Lý spend deeper research and production effort '
            'on this idea?" You are NOT a research engine, narrative engine, script '
            "writer, packaging generator, or final scientific verifier -- you "
            "evaluate the already-approved idea using the R0 discovery evidence and "
            "the production constraints below.",
            "",
            "BRAND / AUDIENCE",
            f"- Audience: ages {audience.core_age.min}-{audience.core_age.max}, "
            "general audience, no physics prerequisite required.",
            "- Tone: fun but scientifically sound; physics must create or resolve "
            "the central question, not just decorate it.",
            "",
            "PRODUCTION CONSTRAINTS",
            f"- Target length: {production.target_duration_min}-"
            f"{production.target_duration_max} minutes.",
            f"- Target production time: at most {production.target_production_days} days.",
            "- Visual identity: 2D minimalist, limited motion preferred, low "
            f"dependency on expensive AI video generation, asset reuse preferred. "
            f"Editor: {production.editor}.",
            "",
            "EVALUATE EXACTLY THESE FIVE AXES -- no others",
            "1. AUDIENCE -- can a general viewer aged roughly 16-30 understand the "
            "central question? Is the curiosity understandable without prior "
            "physics knowledge? Is there enough payoff for a non-specialist? Is "
            "the idea too niche/technical in its current framing? Do not score "
            "based on school-curriculum usefulness.",
            "2. SCIENCE -- does the R0 evidence indicate a real physics mechanism "
            "exists? Is physics central rather than decorative? Are credible "
            "source paths available? Are there major uncertainty risks that could "
            "block a correct explanation? R0 is provisional discovery evidence, "
            "not proof of final scientific truth -- do not treat it as such, and "
            "do not perform any deeper research of your own.",
            "3. NARRATIVE -- is there a meaningful BUT/tension? Can the central "
            "question support an investigation? Is there enough causal/story "
            "material for 8-10 minutes? Is there a plausible payoff? Does the idea "
            "risk becoming a lecture rather than a story? Do not write the actual "
            "narrative outline or script -- only judge feasibility.",
            "4. VISUAL -- can the idea plausibly be represented using Tí, 2D "
            "minimalist assets, diagrams, limited motion, asset reuse, and "
            "evidence media only when genuinely useful? You may identify "
            "likely_reusable_assets and likely_expensive_scenes. Do not generate "
            "image prompts or storyboard shots.",
            "5. PRODUCTION -- can this video plausibly be produced under the "
            "constraints above? Classify estimated_complexity as LOW, MEDIUM, or "
            "HIGH.",
            "",
            "STATUS RULES",
            "Each axis gets a status: PASS, REFRAME, or REJECT -- no other values "
            "(no GOOD/WEAK/MAYBE/etc). Also set an overall status using the same "
            "three values; it will be independently re-derived from your five "
            "axis statuses before anything is persisted, so make sure your axis "
            "statuses genuinely reflect your judgment.",
            "",
            "OUTPUT DISCIPLINE -- do NOT include any of the following",
            "- A full script or dialogue",
            "- A narrative outline or NarrativePlan",
            "- Invented or unverified factual claims",
            "- Deep research or new source discovery -- use only the R0 evidence given",
            "- A thumbnail, title, or other packaging content",
            "- Image prompts, storyboard shots, or other visual generation prompts",
            "",
            "OUTPUT FORMAT",
            "Return ONLY a single JSON object matching this schema exactly -- no "
            "prose, no markdown code fences, no explanation:",
            schema_json,
        ]
    )


def build_user_prompt(
    idea: IdeaCandidate, research: ResearchR0, additional_context: str | None
) -> str:
    lines = [
        "APPROVED IDEA CANDIDATE",
        f"- topic: {idea.topic}",
        f"- central_question: {idea.central_question}",
        f"- ABT and_context: {idea.abt.and_context}",
        f"- ABT but_complication: {idea.abt.but_complication}",
        f"- ABT therefore_investigation: {idea.abt.therefore_investigation}",
        f"- primary_payoff: {idea.primary_payoff.value}",
        f"- secondary_payoffs: {[p.value for p in idea.secondary_payoffs]}",
        f"- physics_core: {idea.physics_core}",
        f"- risks: {idea.risks}",
        "",
        "R0 DISCOVERY EVIDENCE (provisional, not final verification)",
        f"- topic_valid: {research.topic_valid}",
        f"- credible_sources_available: {research.credible_sources_available}",
        f"- story_material_available: {research.story_material_available}",
        f"- physics_material_available: {research.physics_material_available}",
        f"- initial_findings: {research.initial_findings}",
        f"- candidate_sources: {research.candidate_sources}",
        f"- major_risks: {research.major_risks}",
        f"- recommendation: {research.recommendation.value}",
    ]
    if additional_context:
        lines += ["", "Additional context from the user:", additional_context]

    lines += ["", "Return your FeasibilityReport now."]
    return "\n".join(lines)
