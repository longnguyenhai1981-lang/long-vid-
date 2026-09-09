"""Deterministic Voice Renderer manifest validation.

Runs as defense-in-depth AFTER the renderer has already built every
RenderedVoiceTake deterministically (see app/renderers/voice/renderer.py).
There is no LLM output here to validate against a schema -- only the
renderer's own bookkeeping -- so a non-empty result indicates an internal
construction bug, not a correctable generation error (see
VoiceRenderManifestIntegrityError).
"""

from __future__ import annotations

from app.models.audio import VoiceRenderManifest
from app.models.script import ScriptPlan
from app.models.voice import VoicePlan


def validate_voice_render_manifest(
    manifest: VoiceRenderManifest, script_plan: ScriptPlan, voice_plan: VoicePlan
) -> list[str]:
    """Return human-readable integrity violations. Empty list means valid."""
    issues: list[str] = []

    if manifest.script_plan_id != script_plan.id:
        issues.append(
            f"Manifest script_plan_id {manifest.script_plan_id} does not match "
            f"the current ScriptPlan id {script_plan.id}"
        )
    if manifest.voice_plan_id != voice_plan.id:
        issues.append(
            f"Manifest voice_plan_id {manifest.voice_plan_id} does not match "
            f"the current VoicePlan id {voice_plan.id}"
        )

    job_ids = [render.render_job_id for render in manifest.renders]
    issues.extend(_duplicate_issues("render_job_id", job_ids))

    file_paths = [render.file_path for render in manifest.renders]
    issues.extend(_duplicate_issues("file_path", file_paths))

    chunk_by_id = {chunk.chunk_id: chunk for chunk in voice_plan.chunks}

    renders_by_chunk: dict[str, list] = {}
    for render in manifest.renders:
        renders_by_chunk.setdefault(render.chunk_id, []).append(render)

    unknown_chunks = sorted(set(renders_by_chunk) - set(chunk_by_id))
    if unknown_chunks:
        issues.append(f"Manifest references unknown VoicePlan chunk_ids: {unknown_chunks}")

    missing_chunks = sorted(set(chunk_by_id) - set(renders_by_chunk))
    if missing_chunks:
        issues.append(f"Manifest is missing renders for VoicePlan chunk_ids: {missing_chunks}")

    for chunk_id, renders in sorted(renders_by_chunk.items()):
        if chunk_id not in chunk_by_id:
            continue  # already reported as an unknown chunk above
        chunk = chunk_by_id[chunk_id]

        take_numbers = [render.take_number for render in renders]
        issues.extend(
            _duplicate_issues(
                f"take_number for chunk {chunk_id!r}", [str(n) for n in take_numbers]
            )
        )

        expected_takes = set(range(1, chunk.take_count + 1))
        actual_takes = set(take_numbers)
        if actual_takes != expected_takes:
            issues.append(
                f"Chunk {chunk_id!r} expected take numbers {sorted(expected_takes)}, "
                f"got {sorted(actual_takes)}"
            )

        for render in renders:
            if render.line_ids != chunk.line_ids:
                issues.append(
                    f"Render {render.render_job_id!r} line_ids {render.line_ids} does "
                    f"not match chunk {chunk_id!r} line_ids {chunk.line_ids}"
                )

    for render in manifest.renders:
        path_issue = _path_safety_issue(render.file_path)
        if path_issue:
            issues.append(f"Render {render.render_job_id!r}: {path_issue}")

    return issues


def _duplicate_issues(field_name: str, values: list[str]) -> list[str]:
    seen: set[str] = set()
    duplicates: set[str] = set()
    for value in values:
        if value in seen:
            duplicates.add(value)
        seen.add(value)
    return [f"Duplicate {field_name}: {value}" for value in sorted(duplicates)]


def _path_safety_issue(file_path: str) -> str | None:
    normalized = file_path.replace("\\", "/")
    if normalized.startswith("/") or (len(file_path) >= 2 and file_path[1] == ":"):
        return f"file_path must not be absolute: {file_path!r}"
    if ".." in normalized.split("/"):
        return f"file_path must not contain '..': {file_path!r}"
    return None
