"""Deterministic, cheap search-query construction. No LLM call rewrites queries here.

R0 must remain cheap and non-recursive: MAX_R0_SEARCH_QUERIES is a fixed,
conservative ceiling, not a target the engine tries to fill.
"""

from __future__ import annotations

from app.models.idea import IdeaCandidate
from app.research.models import ResearchQuery

MAX_R0_SEARCH_QUERIES = 4


def build_search_queries(idea: IdeaCandidate) -> list[ResearchQuery]:
    """At minimum: one query from the topic, one from the central question, and one
    physics-focused query. Remaining budget (up to MAX_R0_SEARCH_QUERIES total) is
    filled from idea.research_questions, in order, then everything is deduplicated
    and capped.
    """
    candidates = [idea.topic, idea.central_question, idea.physics_core]
    candidates.extend(idea.research_questions)

    seen: set[str] = set()
    deduped: list[str] = []
    for text in candidates:
        key = text.strip().lower()
        if key and key not in seen:
            seen.add(key)
            deduped.append(text.strip())

    capped = deduped[:MAX_R0_SEARCH_QUERIES]
    return [ResearchQuery(query=q) for q in capped]
