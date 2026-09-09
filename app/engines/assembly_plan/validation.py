"""Deterministic Timing/Assembly Planning normalization and business
validation.

normalize_assembly_plan() runs BEFORE validation, on every generation
attempt (initial and corrected): it overwrites the fields whose truth is
already known from real upstream artifacts (script_plan_id, voice_plan_id,
visual_plan_id, and each segment's voice_chunk_ids) rather than trusting
the LLM to reproduce them -- per the patch's explicit instruction not to
spend a business-correction call on a deterministic relationship the
engine can derive safely. validate_assembly_plan() then only ever sees an
already-normalized plan, so a wrong id or voice_chunk_ids mismatch can
never itself trigger a correction call or exhaustion failure.

Everything else here is genuinely new to Phase 15: for Phase 15, exactly
one AssemblySegment maps to exactly one VisualBeat (the patch's explicit
preference, elevated to a hard rule per its own "STOP and report before
weakening this" instruction), so segment/beat coverage-and-order is one
unified list-equality check against VisualPlan.beats' own order -- the
same philosophy Phase 13/14 used for ScriptLine coverage, applied one
level up. Script-line coverage is still independently re-verified against
ScriptPlan directly. Timeline contiguity and total-duration checks use a
1e-3 second tolerance for floating-point safety; the ScriptPlan-duration
tolerance uses max(30s, 10%) exactly as specified.
"""

from __future__ import annotations

from app.models.assembly import AssemblyPlan
from app.models.script import ScriptPlan
from app.models.visual import VisualPlan
from app.models.voice import VoicePlan

_TIMING_TOLERANCE_SECONDS = 1e-3
_MIN_DURATION_TOLERANCE_SECONDS = 30
_DURATION_TOLERANCE_FRACTION = 0.10


def normalize_assembly_plan(
    plan: AssemblyPlan,
    script_plan: ScriptPlan,
    voice_plan: VoicePlan,
    visual_plan: VisualPlan,
) -> AssemblyPlan:
    """Deterministically overwrite fields whose truth is already known.
    Documented normalized fields: AssemblyPlan.script_plan_id,
    voice_plan_id, visual_plan_id; every AssemblySegment.voice_chunk_ids."""
    normalized_segments = [
        segment.model_copy(
            update={"voice_chunk_ids": _overlapping_voice_chunk_ids(segment, voice_plan)}
        )
        for segment in plan.segments
    ]
    return plan.model_copy(
        update={
            "script_plan_id": script_plan.id,
            "voice_plan_id": voice_plan.id,
            "visual_plan_id": visual_plan.id,
            "segments": normalized_segments,
        }
    )


def _overlapping_voice_chunk_ids(segment, voice_plan: VoicePlan) -> list[str]:
    """Every VoiceChunk whose line_ids intersect the segment's
    script_line_ids, in VoicePlan.chunks' own (stable) order."""
    segment_lines = set(segment.script_line_ids)
    return [
        chunk.chunk_id for chunk in voice_plan.chunks if segment_lines & set(chunk.line_ids)
    ]


def validate_assembly_plan(
    plan: AssemblyPlan,
    script_plan: ScriptPlan,
    voice_plan: VoicePlan,
    visual_plan: VisualPlan,
) -> list[str]:
    """Return human-readable business-rule violations. Empty list means
    valid. Assumes normalize_assembly_plan() has already run -- deterministic
    fields are re-checked here too (so this function is independently
    correct for direct unit testing), but the engine never lets a
    deterministic mismatch reach here uncorrected."""
    issues: list[str] = []

    issues.extend(_duplicate_issues("segment_id", [seg.segment_id for seg in plan.segments]))
    issues.extend(_visual_beat_coverage_issues(plan, visual_plan))
    issues.extend(_segment_line_match_issues(plan, visual_plan))
    issues.extend(_script_line_coverage_issues(plan, script_plan))
    issues.extend(_voice_chunk_overlap_issues(plan, voice_plan))
    issues.extend(_timeline_issues(plan))
    issues.extend(_duration_tolerance_issues(plan, script_plan))

    return issues


def _visual_beat_coverage_issues(plan: AssemblyPlan, visual_plan: VisualPlan) -> list[str]:
    issues: list[str] = []
    expected_order = [beat.beat_id for beat in visual_plan.beats]
    actual_order = [segment.visual_beat_id for segment in plan.segments]
    valid_ids = set(expected_order)

    unknown = sorted({bid for bid in actual_order if bid not in valid_ids})
    if unknown:
        issues.append(f"AssemblyPlan references unknown VisualBeat ids: {unknown}")

    missing = sorted(set(expected_order) - set(actual_order))
    if missing:
        issues.append(f"AssemblyPlan is missing VisualBeat ids: {missing}")

    duplicate_issues = _duplicate_issues("visual_beat_id", actual_order)
    issues.extend(duplicate_issues)

    if not unknown and not missing and not duplicate_issues and actual_order != expected_order:
        issues.append(
            "AssemblyPlan segment order does not exactly match VisualPlan "
            f"beat order: expected {expected_order}, got {actual_order}"
        )

    return issues


def _segment_line_match_issues(plan: AssemblyPlan, visual_plan: VisualPlan) -> list[str]:
    issues: list[str] = []
    visual_beats_by_id = {beat.beat_id: beat for beat in visual_plan.beats}
    for segment in plan.segments:
        beat = visual_beats_by_id.get(segment.visual_beat_id)
        if beat is not None and segment.script_line_ids != beat.script_line_ids:
            issues.append(
                f"AssemblySegment {segment.segment_id}.script_line_ids "
                f"{segment.script_line_ids} does not exactly match VisualBeat "
                f"{beat.beat_id}.script_line_ids {beat.script_line_ids}"
            )
    return issues


def _script_line_coverage_issues(plan: AssemblyPlan, script_plan: ScriptPlan) -> list[str]:
    script_line_ids = [line.line_id for beat in script_plan.beats for line in beat.lines]
    all_segment_line_ids = [lid for segment in plan.segments for lid in segment.script_line_ids]
    if all_segment_line_ids == script_line_ids:
        return []
    return [
        "AssemblyPlan's flattened script_line_ids across segments does not "
        f"exactly equal the ScriptPlan's own line order: expected "
        f"{script_line_ids}, got {all_segment_line_ids}"
    ]


def _voice_chunk_overlap_issues(plan: AssemblyPlan, voice_plan: VoicePlan) -> list[str]:
    issues: list[str] = []
    for segment in plan.segments:
        expected = _overlapping_voice_chunk_ids(segment, voice_plan)
        if segment.voice_chunk_ids != expected:
            issues.append(
                f"AssemblySegment {segment.segment_id}.voice_chunk_ids "
                f"{segment.voice_chunk_ids} does not match the VoiceChunks "
                f"that actually overlap its script lines: {expected}"
            )
    return issues


def _timeline_issues(plan: AssemblyPlan) -> list[str]:
    issues: list[str] = []
    segments = plan.segments

    first = segments[0]
    if abs(first.start_seconds - 0.0) > _TIMING_TOLERANCE_SECONDS:
        issues.append(
            f"First segment {first.segment_id} must start at 0 (within "
            f"{_TIMING_TOLERANCE_SECONDS}s tolerance), got {first.start_seconds}"
        )

    for previous, current in zip(segments, segments[1:]):
        if abs(current.start_seconds - previous.end_seconds) > _TIMING_TOLERANCE_SECONDS:
            issues.append(
                f"Segment {current.segment_id}.start_seconds "
                f"({current.start_seconds}) does not match segment "
                f"{previous.segment_id}.end_seconds ({previous.end_seconds}) "
                f"within {_TIMING_TOLERANCE_SECONDS}s tolerance -- the "
                f"timeline must be contiguous with no gaps or overlaps"
            )

    last_end = segments[-1].end_seconds
    if abs(plan.estimated_total_duration_seconds - last_end) > _TIMING_TOLERANCE_SECONDS:
        issues.append(
            "AssemblyPlan.estimated_total_duration_seconds "
            f"({plan.estimated_total_duration_seconds}) does not match the "
            f"final segment's end_seconds ({last_end}) within "
            f"{_TIMING_TOLERANCE_SECONDS}s tolerance"
        )

    return issues


def _duration_tolerance_issues(plan: AssemblyPlan, script_plan: ScriptPlan) -> list[str]:
    tolerance = max(
        _MIN_DURATION_TOLERANCE_SECONDS,
        _DURATION_TOLERANCE_FRACTION * script_plan.estimated_duration_seconds,
    )
    deviation = abs(plan.estimated_total_duration_seconds - script_plan.estimated_duration_seconds)
    if deviation <= tolerance:
        return []
    return [
        "AssemblyPlan.estimated_total_duration_seconds "
        f"({plan.estimated_total_duration_seconds}) deviates from "
        f"ScriptPlan.estimated_duration_seconds "
        f"({script_plan.estimated_duration_seconds}) by {deviation}s, "
        f"exceeding the allowed tolerance of {tolerance}s"
    ]


def _duplicate_issues(field_name: str, values: list[str]) -> list[str]:
    seen: set[str] = set()
    duplicates: set[str] = set()
    for value in values:
        if value in seen:
            duplicates.add(value)
        seen.add(value)
    return [f"Duplicate {field_name}: {value}" for value in sorted(duplicates)]
