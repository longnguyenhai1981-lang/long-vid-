"""Deterministic Narrative business validation.

Runs AFTER generate_structured returns a Pydantic-valid NarrativePlan --
Phase 3's structured layer is never touched. Checks the two things a
deterministic validator can actually verify: question-ladder structural
integrity (ids, references, a single linear chain) and claim referential
integrity (every claim_id used must exist and must not be PROHIBITED).
Causal quality of the ladder (does Qn's answer genuinely motivate Qn+1) is
a prompting concern, not something structural code can check.
"""

from __future__ import annotations

from app.models.common import ClaimStatus
from app.models.narrative import NarrativePlan, QuestionLadderNode
from app.models.research import ResearchPackage


def validate_narrative_plan(plan: NarrativePlan, research_package: ResearchPackage) -> list[str]:
    """Return human-readable business-rule violations. Empty list means valid."""
    issues: list[str] = []
    issues.extend(_validate_ladder_structure(plan.question_ladder))
    issues.extend(_validate_claim_references(plan, research_package))
    return issues


def _validate_ladder_structure(nodes: list[QuestionLadderNode]) -> list[str]:
    issues: list[str] = []
    if not nodes:
        return issues

    ids = [node.id for node in nodes]
    issues.extend(_duplicate_issues("QuestionLadderNode.id", ids))
    id_set = set(ids)

    if len(id_set) != len(ids):
        # Structural checks below assume unique ids; let the correction
        # attempt fix duplicates first rather than reporting confusing
        # secondary symptoms of the same root cause.
        return issues

    for node in nodes:
        if node.creates_next_question is None:
            continue
        if node.creates_next_question == node.id:
            issues.append(
                f"QuestionLadderNode {node.id} references itself as creates_next_question"
            )
        elif node.creates_next_question not in id_set:
            issues.append(
                f"QuestionLadderNode {node.id}.creates_next_question references "
                f"unknown node id: {node.creates_next_question}"
            )

    if issues:
        return issues

    final_nodes = [node.id for node in nodes if node.creates_next_question is None]
    if len(final_nodes) == 0:
        issues.append(
            "Question ladder has no final node (every node has a non-null creates_next_question)"
        )
    elif len(final_nodes) > 1:
        issues.append(
            f"Question ladder has multiple final nodes (creates_next_question=null): "
            f"{final_nodes}; exactly one is required"
        )
    if issues:
        return issues

    referenced_as_next = {
        node.creates_next_question for node in nodes if node.creates_next_question is not None
    }
    start_candidates = [node.id for node in nodes if node.id not in referenced_as_next]
    if len(start_candidates) != 1:
        issues.append(
            "Question ladder does not form a single linear chain: expected exactly "
            f"one starting node with no incoming reference, found {start_candidates}"
        )
        return issues

    by_id = {node.id: node for node in nodes}
    visited: list[str] = []
    current: str | None = start_candidates[0]
    while current is not None:
        if current in visited:
            issues.append(f"Question ladder contains a cycle involving node {current}")
            return issues
        visited.append(current)
        current = by_id[current].creates_next_question

    if len(visited) != len(nodes):
        missing = sorted(id_set - set(visited))
        issues.append(
            f"Question ladder is not a single connected chain -- unreachable nodes: {missing}"
        )

    return issues


def _validate_claim_references(plan: NarrativePlan, research_package: ResearchPackage) -> list[str]:
    issues: list[str] = []
    valid_claim_ids = {claim.claim_id for claim in research_package.claims}
    prohibited_claim_ids = {
        claim.claim_id for claim in research_package.claims if claim.status is ClaimStatus.PROHIBITED
    }

    def check(claim_id: str, location: str) -> None:
        if claim_id not in valid_claim_ids:
            issues.append(f"{location} references unknown claim_id: {claim_id}")
        elif claim_id in prohibited_claim_ids:
            issues.append(
                f"{location} references PROHIBITED claim_id: {claim_id}, which must "
                f"not be used as factual support"
            )

    for node in plan.question_ladder:
        for claim_id in node.claim_ids:
            check(claim_id, f"QuestionLadderNode {node.id}")

    for claim_id in plan.claim_ids_used:
        check(claim_id, "NarrativePlan.claim_ids_used")

    return issues


def _duplicate_issues(field_name: str, values: list[str]) -> list[str]:
    seen: set[str] = set()
    duplicates: set[str] = set()
    for value in values:
        if value in seen:
            duplicates.add(value)
        seen.add(value)
    return [f"Duplicate {field_name}: {value}" for value in sorted(duplicates)]
