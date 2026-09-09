"""Deterministic Visual Planning business validation.

Runs AFTER generate_structured returns a Pydantic-valid VisualPlan -- Phase
3's structured layer is never touched, and structural bounds already
enforced on VisualBeat/VisualPlan itself (non-empty beats, non-empty
script_line_ids per beat, non-blank beat_id/narrative_node/concept/
primary_focus) are Pydantic's job, not this module's.

Line coverage/order/adjacency uses the same unified philosophy as Phase
13's VoicePlan validation: the VisualPlan's flattened script_line_ids,
across all beats in beat order, must exactly equal the ScriptPlan's own
line-id sequence. Everything else here is genuinely new to Phase 14:
narrative-node alignment (a beat may not silently span two different
approved narrative nodes), evidence-source integrity (EVIDENCE_MEDIA
beats must cite real ResearchPackage sources; no other beat may cite one
at all), and a deterministic guard against a plan dominated by expensive
C3 visuals.
"""

from __future__ import annotations

from app.models.common import ComplexityClass, VisualMediaType
from app.models.narrative import NarrativePlan
from app.models.research import ResearchPackage
from app.models.script import ScriptPlan
from app.models.visual import VisualPlan

_C3_BUDGET_PERCENT = 20
_C3_BUDGET_MIN_BEATS = 5


def validate_visual_plan(
    plan: VisualPlan,
    script_plan: ScriptPlan,
    narrative_plan: NarrativePlan,
    research_package: ResearchPackage,
) -> list[str]:
    """Return human-readable business-rule violations. Empty list means valid."""
    issues: list[str] = []

    script_line_ids = [line.line_id for beat in script_plan.beats for line in beat.lines]
    line_to_narrative_node = {
        line.line_id: beat.narrative_node for beat in script_plan.beats for line in beat.lines
    }
    valid_narrative_nodes = {node.id for node in narrative_plan.question_ladder}
    valid_source_ids = {source.source_id for source in research_package.sources}

    beat_ids = [beat.beat_id for beat in plan.beats]
    issues.extend(_duplicate_issues("beat_id", beat_ids))

    all_line_ids: list[str] = []
    for beat in plan.beats:
        all_line_ids.extend(beat.script_line_ids)

    # Coverage / order / adjacency, unified exactly like VoicePlan (Phase 13).
    unknown = sorted({line_id for line_id in all_line_ids if line_id not in set(script_line_ids)})
    if unknown:
        issues.append(f"VisualPlan references unknown ScriptLine ids: {unknown}")

    missing = sorted(set(script_line_ids) - set(all_line_ids))
    if missing:
        issues.append(f"VisualPlan is missing ScriptLine ids: {missing}")

    duplicate_line_issues = _duplicate_issues("script_line_id", all_line_ids)
    issues.extend(duplicate_line_issues)

    if not unknown and not missing and not duplicate_line_issues and all_line_ids != script_line_ids:
        issues.append(
            "VisualPlan line order does not exactly preserve ScriptPlan line "
            f"order (and/or beats are non-contiguous): expected {script_line_ids}, "
            f"got {all_line_ids}"
        )

    c3_count = 0
    for beat in plan.beats:
        if beat.narrative_node not in valid_narrative_nodes:
            issues.append(
                f"VisualBeat {beat.beat_id}.narrative_node references unknown "
                f"NarrativePlan question_ladder node id: {beat.narrative_node}"
            )

        underlying_nodes = sorted(
            {
                line_to_narrative_node[line_id]
                for line_id in beat.script_line_ids
                if line_id in line_to_narrative_node
            }
        )
        if len(underlying_nodes) > 1:
            issues.append(
                f"VisualBeat {beat.beat_id} covers ScriptLines from multiple "
                f"narrative nodes: {underlying_nodes}"
            )
        elif len(underlying_nodes) == 1 and beat.narrative_node != underlying_nodes[0]:
            issues.append(
                f"VisualBeat {beat.beat_id} declares narrative_node="
                f"{beat.narrative_node!r} but its covered lines belong to "
                f"narrative_node={underlying_nodes[0]!r}"
            )

        if beat.media_type is VisualMediaType.EVIDENCE_MEDIA:
            if not beat.evidence_source_ids:
                issues.append(
                    f"VisualBeat {beat.beat_id} has media_type=EVIDENCE_MEDIA "
                    f"but no evidence_source_ids"
                )
            unknown_sources = sorted(
                sid for sid in beat.evidence_source_ids if sid not in valid_source_ids
            )
            if unknown_sources:
                issues.append(
                    f"VisualBeat {beat.beat_id} references unknown evidence "
                    f"source_ids: {unknown_sources}"
                )
        elif beat.evidence_source_ids:
            issues.append(
                f"VisualBeat {beat.beat_id} has media_type={beat.media_type.value} "
                f"but declares evidence_source_ids (only EVIDENCE_MEDIA beats "
                f"may): {beat.evidence_source_ids}"
            )

        if beat.complexity is ComplexityClass.C3:
            c3_count += 1

    if (
        len(plan.beats) >= _C3_BUDGET_MIN_BEATS
        and c3_count * 100 > len(plan.beats) * _C3_BUDGET_PERCENT
    ):
        issues.append(
            f"C3 beats ({c3_count}/{len(plan.beats)}) exceed the "
            f"{_C3_BUDGET_PERCENT}% budget for plans with "
            f"{_C3_BUDGET_MIN_BEATS} or more beats"
        )

    return issues


def _duplicate_issues(field_name: str, values: list[str]) -> list[str]:
    seen: set[str] = set()
    duplicates: set[str] = set()
    for value in values:
        if value in seen:
            duplicates.add(value)
        seen.add(value)
    return [f"Duplicate {field_name}: {value}" for value in sorted(duplicates)]
