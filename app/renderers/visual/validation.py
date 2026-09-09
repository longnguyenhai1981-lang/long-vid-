"""Deterministic Visual Renderer manifest validation.

Runs as defense-in-depth AFTER the renderer has already built every
RenderedVisualAsset/VisualRenderRequirement deterministically (see
app/renderers/visual/renderer.py). There is no LLM output here to validate
against a schema -- only the renderer's own bookkeeping -- so a non-empty
result indicates an internal construction bug, not a correctable
generation error (see VisualRenderManifestIntegrityError).
"""

from __future__ import annotations

from app.models.common import VisualMediaType, VisualRequirementStatus
from app.models.script import ScriptPlan
from app.models.visual import VisualPlan
from app.models.visual_render import VisualRenderManifest
from app.models.voice import VoicePlan

# "Renderable" here means "a real rendered file is a valid manifest.assets
# entry for this media_type" -- not "goes through a VisualProvider". As of
# Phase 25, DIAGRAM produces a real PNG via DiagramRenderer without ever
# calling a provider (see app/renderers/visual/renderer.py), exactly as
# TI_STATE's COMPOSITE mode has produced one via TiCompositor since Phase
# 23 -- both are listed here for that reason, neither is provider-driven.
# Phase 26 adds COMPOSITION for the same reason: VisualLayerCompositor
# produces a real PNG from other beats' already-rendered outputs, never
# from a provider call.
_RENDERABLE_MEDIA_TYPES = {VisualMediaType.GENERATED_STILL, VisualMediaType.DIAGRAM, VisualMediaType.COMPOSITION}
_ASSET_ALLOWED_MEDIA_TYPES = _RENDERABLE_MEDIA_TYPES | {VisualMediaType.TI_STATE}
_REUSE_ONLY_MEDIA_TYPES = {VisualMediaType.ASSET_REUSE}
_CANONICAL_ASSET_READY_MEDIA_TYPES = {VisualMediaType.TI_STATE}
_EXTERNAL_REQUIRED_MEDIA_TYPES = {
    VisualMediaType.LIMITED_MOTION,
    VisualMediaType.EVIDENCE_MEDIA,
    VisualMediaType.AI_HERO_VIDEO,
}


def validate_visual_render_manifest(
    manifest: VisualRenderManifest,
    script_plan: ScriptPlan,
    voice_plan: VoicePlan,
    visual_plan: VisualPlan,
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
    if manifest.visual_plan_id != visual_plan.id:
        issues.append(
            f"Manifest visual_plan_id {manifest.visual_plan_id} does not match "
            f"the current VisualPlan id {visual_plan.id}"
        )

    render_job_ids = [asset.render_job_id for asset in manifest.assets]
    issues.extend(_duplicate_issues("render_job_id", render_job_ids))

    file_paths = [asset.file_path for asset in manifest.assets]
    issues.extend(_duplicate_issues("file_path", file_paths))

    beat_by_id = {beat.beat_id: beat for beat in visual_plan.beats}

    covered_beat_ids = [asset.beat_id for asset in manifest.assets] + [
        req.beat_id for req in manifest.requirements
    ]
    issues.extend(_duplicate_issues("beat_id", covered_beat_ids))

    covered_set = set(covered_beat_ids)
    unknown_beats = sorted(covered_set - set(beat_by_id))
    if unknown_beats:
        issues.append(f"Manifest references unknown VisualPlan beat_ids: {unknown_beats}")

    missing_beats = sorted(set(beat_by_id) - covered_set)
    if missing_beats:
        issues.append(f"Manifest is missing coverage for VisualPlan beat_ids: {missing_beats}")

    for asset in manifest.assets:
        if asset.media_type not in _ASSET_ALLOWED_MEDIA_TYPES:
            issues.append(
                f"Asset {asset.render_job_id!r} has media_type {asset.media_type.value}, "
                f"which is not provider-renderable"
            )
        path_issue = _path_safety_issue(asset.file_path)
        if path_issue:
            issues.append(f"Asset {asset.render_job_id!r}: {path_issue}")

    for requirement in manifest.requirements:
        if requirement.status is VisualRequirementStatus.REUSE_ONLY:
            if requirement.media_type not in _REUSE_ONLY_MEDIA_TYPES:
                issues.append(
                    f"Requirement for beat {requirement.beat_id!r} is REUSE_ONLY but has "
                    f"media_type {requirement.media_type.value}"
                )
        elif requirement.status is VisualRequirementStatus.CANONICAL_ASSET_READY:
            if requirement.media_type not in _CANONICAL_ASSET_READY_MEDIA_TYPES:
                issues.append(
                    f"Requirement for beat {requirement.beat_id!r} is CANONICAL_ASSET_READY but "
                    f"has media_type {requirement.media_type.value}"
                )
        elif requirement.status is VisualRequirementStatus.EXTERNAL_REQUIRED:
            if requirement.media_type not in _EXTERNAL_REQUIRED_MEDIA_TYPES:
                issues.append(
                    f"Requirement for beat {requirement.beat_id!r} is EXTERNAL_REQUIRED but has "
                    f"media_type {requirement.media_type.value}"
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


def _path_safety_issue(file_path: str) -> str | None:
    normalized = file_path.replace("\\", "/")
    if normalized.startswith("/") or (len(file_path) >= 2 and file_path[1] == ":"):
        return f"file_path must not be absolute: {file_path!r}"
    if ".." in normalized.split("/"):
        return f"file_path must not contain '..': {file_path!r}"
    return None
