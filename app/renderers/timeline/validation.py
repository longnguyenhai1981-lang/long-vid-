"""Deterministic TimelineManifest integrity validation.

Runs as defense-in-depth AFTER TimelineBuilder has already built every
TimelineSegment/TimelineCue deterministically (see
app/renderers/timeline/builder.py). There is no LLM output here to
validate against a schema -- only the builder's own bookkeeping -- so a
non-empty result indicates an internal construction bug, not a
correctable generation error (see TimelineManifestIntegrityError).

Pure and I/O-free, mirroring app/renderers/visual/validation.py's shape
exactly: file/asset existence is checked once, during resolution, in
builder.py itself -- never re-checked here.
"""

from __future__ import annotations

from app.models.script import ScriptPlan
from app.models.timeline import TimelineManifest
from app.models.visual import VisualPlan
from app.models.voice import VoicePlan


def validate_timeline_manifest(
    manifest: TimelineManifest,
    script_plan: ScriptPlan,
    voice_plan: VoicePlan,
    visual_plan: VisualPlan,
) -> list[str]:
    """Return human-readable integrity violations. Empty list means valid."""
    issues: list[str] = []

    if manifest.script_plan_id != script_plan.id:
        issues.append(
            f"TimelineManifest script_plan_id {manifest.script_plan_id} does not "
            f"match the current ScriptPlan id {script_plan.id}"
        )
    if manifest.voice_plan_id != voice_plan.id:
        issues.append(
            f"TimelineManifest voice_plan_id {manifest.voice_plan_id} does not "
            f"match the current VoicePlan id {voice_plan.id}"
        )
    if manifest.visual_plan_id != visual_plan.id:
        issues.append(
            f"TimelineManifest visual_plan_id {manifest.visual_plan_id} does not "
            f"match the current VisualPlan id {visual_plan.id}"
        )

    segment_ids = [segment.segment_id for segment in manifest.segments]
    issues.extend(_duplicate_issues("segment_id", segment_ids))

    issues.extend(_contiguity_issues(manifest))
    issues.extend(_cue_bounds_issues(manifest))

    return issues


def _contiguity_issues(manifest: TimelineManifest) -> list[str]:
    """MVP policy (Phase 27): narration segments are always contiguous --
    no gap, no overlap. There is no "explicitly allowed gap" mechanism yet
    (item 11's "unless explicitly allowed" clause), so this check is
    unconditional."""
    issues: list[str] = []
    segments = manifest.segments

    first = segments[0]
    if first.start_ms != 0:
        issues.append(
            f"First segment {first.segment_id} must start at 0ms, got {first.start_ms}ms"
        )

    for previous, current in zip(segments, segments[1:]):
        if current.start_ms != previous.end_ms:
            issues.append(
                f"Segment {current.segment_id}.start_ms ({current.start_ms}) does "
                f"not match segment {previous.segment_id}.end_ms ({previous.end_ms}) "
                f"-- the timeline must be contiguous with no gaps or overlaps"
            )

    return issues


def _cue_bounds_issues(manifest: TimelineManifest) -> list[str]:
    issues: list[str] = []
    for cue in manifest.cues:
        if not (0 <= cue.timestamp_ms <= manifest.total_duration_ms):
            issues.append(
                f"Cue {cue.cue_type.value} at {cue.timestamp_ms}ms falls outside "
                f"the timeline's own duration [0, {manifest.total_duration_ms}]ms"
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
