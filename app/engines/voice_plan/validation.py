"""Deterministic Voice Planning business validation.

Runs AFTER generate_structured returns a Pydantic-valid VoicePlan -- Phase
3's structured layer is never touched, and structural bounds already
enforced on VoiceChunk/VoicePlan itself (non-empty chunks, non-empty
line_ids per chunk, take_count in [1, 3]) are Pydantic's job, not this
module's.

What's left to check deterministically is purely referential: does the
VoicePlan's flattened line_id sequence -- across all chunks, in chunk
order -- exactly equal the ScriptPlan's own line_id sequence? A single
list-equality check (once obviously-bad ids are ruled out and reported
separately, for a clearer correction prompt) simultaneously proves full
coverage (no missing line), no extras (no unknown line), no duplicates,
global order preservation, and chunk adjacency/contiguity all at once --
any violation of any of those necessarily breaks the equality.
"""

from __future__ import annotations

from app.models.script import ScriptPlan
from app.models.voice import VoicePlan


def validate_voice_plan(plan: VoicePlan, script_plan: ScriptPlan) -> list[str]:
    """Return human-readable business-rule violations. Empty list means valid."""
    issues: list[str] = []

    script_line_ids = [line.line_id for beat in script_plan.beats for line in beat.lines]
    script_line_id_set = set(script_line_ids)

    chunk_ids = [chunk.chunk_id for chunk in plan.chunks]
    issues.extend(_duplicate_issues("chunk_id", chunk_ids))

    all_line_ids: list[str] = []
    for chunk in plan.chunks:
        all_line_ids.extend(chunk.line_ids)

    unknown = sorted({line_id for line_id in all_line_ids if line_id not in script_line_id_set})
    if unknown:
        issues.append(f"VoicePlan references unknown ScriptLine ids: {unknown}")

    missing = sorted(script_line_id_set - set(all_line_ids))
    if missing:
        issues.append(f"VoicePlan is missing ScriptLine ids: {missing}")

    duplicate_issues = _duplicate_issues("line_id", all_line_ids)
    issues.extend(duplicate_issues)

    # Global order + chunk adjacency, unified: once ids are known-valid,
    # complete, and duplicate-free, exact sequence equality is both
    # necessary and sufficient for "order preserved and chunks contiguous".
    if not unknown and not missing and not duplicate_issues and all_line_ids != script_line_ids:
        issues.append(
            "VoicePlan line order does not exactly preserve ScriptPlan line "
            f"order (and/or chunks are non-contiguous): expected {script_line_ids}, "
            f"got {all_line_ids}"
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
