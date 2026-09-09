"""Deterministic, bounded R1 search-query planning. No LLM call rewrites queries.

Covers the categories from the approved query strategy -- core mechanism and
primary/authoritative sources always fire; dispute, misconception, and
historical/timeline queries fire only when a concrete signal makes them
relevant, so irrelevant categories are never forced. Deduplicated and capped
at MAX_R1_SEARCH_QUERIES regardless of how many candidates are generated --
no autonomous/recursive search loop.
"""

from __future__ import annotations

from app.models.common import PrimaryPayoff
from app.models.feasibility import FeasibilityReport
from app.models.idea import IdeaCandidate
from app.models.research import ResearchR0
from app.research.models import ResearchQuery

MAX_R1_SEARCH_QUERIES = 10


def build_search_queries(
    idea: IdeaCandidate, research_r0: ResearchR0, feasibility: FeasibilityReport
) -> list[ResearchQuery]:
    # Priority order: higher-value categories first, so the lowest-priority
    # filler (historical/timeline) is the first to be dropped by the cap.
    candidates: list[str] = []

    # A. Core mechanism -- always.
    candidates.append(idea.central_question)
    candidates.append(idea.physics_core)

    # B. Primary / authoritative sources -- always.
    candidates.append(f"{idea.topic} original paper official technical report")

    # D. Dispute / alternative explanation -- only when R0 flagged a risk.
    for risk in research_r0.major_risks:
        candidates.append(f"{risk} alternative explanation controversy")

    # E. Misconception -- only when the idea's own payoff is a reversal of a
    # popular belief (a concrete, deterministic signal for relevance).
    if idea.primary_payoff is PrimaryPayoff.REVERSAL:
        candidates.append(f"{idea.topic} common misconception")

    # F. Specific open questions.
    candidates.extend(idea.research_questions)

    # C. Historical / timeline -- lowest priority; included only if budget remains.
    candidates.append(f"{idea.topic} history timeline")

    return _dedupe_and_cap(candidates)


def _dedupe_and_cap(candidates: list[str]) -> list[ResearchQuery]:
    seen: set[str] = set()
    deduped: list[str] = []
    for text in candidates:
        key = text.strip().lower()
        if key and key not in seen:
            seen.add(key)
            deduped.append(text.strip())

    capped = deduped[:MAX_R1_SEARCH_QUERIES]
    return [ResearchQuery(query=q) for q in capped]
