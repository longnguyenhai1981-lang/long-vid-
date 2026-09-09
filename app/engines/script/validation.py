"""Deterministic Script business validation.

Runs AFTER generate_structured returns a Pydantic-valid ScriptPlan -- Phase
3's structured layer is never touched. Checks only what deterministic code
can actually verify: structural integrity (beat/line non-emptiness, id
uniqueness), narrative-node integrity (every beat maps to a real
QuestionLadderNode.id, in non-regressing approved order), claim referential
integrity (every referenced claim exists and is never PROHIBITED), and
duration sanity. Semantic quality -- whether the script actually delivers on
the packaging promise, whether qualification wording is adequate, whether a
micro-hook genuinely emerges from story logic -- is a prompting concern this
layer does not and cannot check; that responsibility stays with the prompt
(and, for claim verification specifically, a future Phase 11).
"""

from __future__ import annotations

from app.models.common import ClaimStatus
from app.models.narrative import NarrativePlan
from app.models.research import ResearchPackage
from app.models.script import ScriptBeat, ScriptPlan

_DURATION_MIN_SECONDS = 420
_DURATION_MAX_SECONDS = 660


def validate_script_plan(
    plan: ScriptPlan, research_package: ResearchPackage, narrative_plan: NarrativePlan
) -> list[str]:
    """Return human-readable business-rule violations. Empty list means valid."""
    issues: list[str] = []
    issues.extend(_validate_non_empty(plan))

    beat_ids = [beat.beat_id for beat in plan.beats]
    issues.extend(_duplicate_issues("beat_id", beat_ids))

    line_ids = [line.line_id for beat in plan.beats for line in beat.lines]
    issues.extend(_duplicate_issues("line_id", line_ids))

    valid_node_ids = {node.id for node in narrative_plan.question_ladder}
    issues.extend(_validate_narrative_nodes(plan.beats, valid_node_ids))

    ladder_order = _compute_ladder_order(narrative_plan)
    issues.extend(_check_narrative_node_order(plan.beats, ladder_order))

    issues.extend(_validate_claim_references(plan.beats, research_package))
    issues.extend(_validate_duration(plan))

    return issues


def _validate_non_empty(plan: ScriptPlan) -> list[str]:
    issues: list[str] = []
    if not plan.beats:
        issues.append("ScriptPlan.beats must not be empty")
    for beat in plan.beats:
        if not beat.lines:
            issues.append(f"ScriptBeat {beat.beat_id} has no lines")
    return issues


def _validate_narrative_nodes(beats: list[ScriptBeat], valid_node_ids: set[str]) -> list[str]:
    issues: list[str] = []
    for beat in beats:
        if beat.narrative_node not in valid_node_ids:
            issues.append(
                f"ScriptBeat {beat.beat_id} references unknown narrative_node: "
                f"{beat.narrative_node!r}"
            )
    return issues


def _compute_ladder_order(narrative_plan: NarrativePlan) -> list[str]:
    """Walk the Question Ladder chain (via creates_next_question) from its start
    node to its end node, returning node ids in approved narrative order."""
    nodes = narrative_plan.question_ladder
    if not nodes:
        return []
    nodes_by_id = {node.id: node for node in nodes}
    referenced_as_next = {
        node.creates_next_question for node in nodes if node.creates_next_question is not None
    }
    start_candidates = [node.id for node in nodes if node.id not in referenced_as_next]
    if len(start_candidates) != 1:
        # NarrativePlan is already business-validated (Phase 8 guarantees a
        # single linear chain) by the time Script Engine loads it -- this is
        # a defensive fallback, not an expected path.
        return [node.id for node in nodes]

    order: list[str] = []
    seen: set[str] = set()
    current: str | None = start_candidates[0]
    while current is not None and current not in seen:
        seen.add(current)
        order.append(current)
        current = nodes_by_id[current].creates_next_question
    return order


def _check_narrative_node_order(beats: list[ScriptBeat], ladder_order: list[str]) -> list[str]:
    issues: list[str] = []
    order_index = {node_id: i for i, node_id in enumerate(ladder_order)}
    seen_first_appearance: set[str] = set()
    last_index = -1
    for beat in beats:
        node_id = beat.narrative_node
        if node_id not in order_index or node_id in seen_first_appearance:
            # Unknown nodes are reported by _validate_narrative_nodes; repeats
            # of an already-seen node are explicitly allowed.
            continue
        seen_first_appearance.add(node_id)
        idx = order_index[node_id]
        if idx < last_index:
            issues.append(
                f"ScriptBeat {beat.beat_id} narrative_node {node_id} appears out of "
                f"approved narrative order (regressed after an earlier node)"
            )
        last_index = idx
    return issues


def _validate_claim_references(beats: list[ScriptBeat], research_package: ResearchPackage) -> list[str]:
    issues: list[str] = []
    valid_claim_ids = {claim.claim_id for claim in research_package.claims}
    prohibited_claim_ids = {
        claim.claim_id for claim in research_package.claims if claim.status is ClaimStatus.PROHIBITED
    }
    for beat in beats:
        for line in beat.lines:
            for claim_id in line.claim_ids:
                if claim_id not in valid_claim_ids:
                    issues.append(
                        f"ScriptLine {line.line_id} references unknown claim_id: {claim_id}"
                    )
                elif claim_id in prohibited_claim_ids:
                    issues.append(
                        f"ScriptLine {line.line_id} references PROHIBITED claim_id: "
                        f"{claim_id}, which must not be used as factual support"
                    )
    return issues


def _validate_duration(plan: ScriptPlan) -> list[str]:
    if plan.estimated_duration_seconds < _DURATION_MIN_SECONDS or (
        plan.estimated_duration_seconds > _DURATION_MAX_SECONDS
    ):
        return [
            f"estimated_duration_seconds {plan.estimated_duration_seconds} is outside "
            f"the acceptable band [{_DURATION_MIN_SECONDS}, {_DURATION_MAX_SECONDS}]"
        ]
    return []


def _duplicate_issues(field_name: str, values: list[str]) -> list[str]:
    seen: set[str] = set()
    duplicates: set[str] = set()
    for value in values:
        if value in seen:
            duplicates.add(value)
        seen.add(value)
    return [f"Duplicate {field_name}: {value}" for value in sorted(duplicates)]
