"""CaptionBuilder: turns an already-locked TimelineManifest (plus the
ScriptPlan/VoicePlan it was built from) into a CaptionManifest (Phase
31) -- the caption-timing counterpart to Phase 28's VideoRenderer.
TimelineBuilder describes timing; CaptionBuilder re-expresses that same
timing as caption cues, using the exact authored script text a
TimelineSegment's own narration was rendered from. It never transcribes
audio, never calls an LLM, and never infers or rewrites text.

Freshness chain is deliberately IDENTICAL to app/renderers/video/
renderer.py's own -- MVP_COMPLETE -> load+verify ScriptPlan -> load+
verify VoicePlan -> verify VoicePlan freshness -> load+verify VisualPlan
-> verify VisualPlan freshness -> load+verify AssemblyPlan -> verify
AssemblyPlan freshness -> load+verify TimelineManifest -> load
VoiceRenderManifest/VisualRenderManifest -> verify TimelineManifest is
still fresh against the current plan chain AND against the exact
VoiceRenderManifest/VisualRenderManifest ids it was built from. Caption
text/timing does not itself depend on VisualPlan/VisualRenderManifest at
all, but Phase 31 requirement #9 explicitly prefers this full check over
inventing a lighter, caption-specific freshness exception -- so this
package pays the same verification cost VideoRenderer does, for the same
guarantee.

This module triggers NO rendering of any kind: no VisualRenderer, no
VoiceRenderer, no TimelineBuilder, no provider, no LLM call, no
speech-to-text engine.
"""

from __future__ import annotations

from datetime import datetime, timezone
from uuid import UUID

from sqlalchemy import Engine

from app.captions.builder import build_caption_manifest
from app.models.assembly import AssemblyPlan
from app.models.audio import VoiceRenderManifest
from app.models.caption import CaptionManifest
from app.models.common import ModuleRunStatus, ProjectState
from app.models.module_run import ModuleRun
from app.models.project import Project
from app.models.script import ScriptPlan
from app.models.timeline import TimelineManifest
from app.models.visual import VisualPlan
from app.models.visual_render import VisualRenderManifest
from app.models.voice import VoicePlan
from app.renderers.caption.errors import (
    MissingAssemblyPlanArtifactError,
    MissingScriptPlanArtifactError,
    MissingTimelineManifestArtifactError,
    MissingVisualPlanArtifactError,
    MissingVisualRenderManifestArtifactError,
    MissingVoicePlanArtifactError,
    MissingVoiceRenderManifestArtifactError,
    RendererStateError,
    StaleAssemblyPlanError,
    StaleTimelineManifestError,
    StaleVisualPlanError,
    StaleVoicePlanError,
)
from app.renderers.caption.models import (
    CAPTION_MANIFEST_ARTIFACT_TYPE,
    CaptionBuilderInput,
    CaptionBuilderResult,
)
from app.renderers.timeline.models import TIMELINE_MANIFEST_ARTIFACT_TYPE
from app.renderers.visual.models import VISUAL_RENDER_MANIFEST_ARTIFACT_TYPE
from app.renderers.voice.models import VOICE_RENDER_MANIFEST_ARTIFACT_TYPE
from app.storage import artifacts as artifact_storage
from app.storage import module_runs as module_run_storage
from app.storage import projects as project_storage
from app.storage.errors import ArtifactNotFoundError, ArtifactValidationError

# Duplicated rather than imported -- see app/renderers/video/renderer.py's
# identical precedent: each of these source modules imports app.llm
# transitively, and this package must stay free of app.llm even
# transitively.
_SCRIPT_PLAN_ARTIFACT_TYPE = "script_plan"
_VOICE_PLAN_ARTIFACT_TYPE = "voice_plan"
_VISUAL_PLAN_ARTIFACT_TYPE = "visual_plan"
_ASSEMBLY_PLAN_ARTIFACT_TYPE = "assembly_plan"

MODULE_NAME = "caption_builder"
MODULE_VERSION = "0.1"


class CaptionBuilder:
    def __init__(
        self,
        db_engine: Engine,
        *,
        project_repo=project_storage,
        artifact_repo=artifact_storage,
        module_run_repo=module_run_storage,
    ):
        self._db_engine = db_engine
        self._project_repo = project_repo
        self._artifact_repo = artifact_repo
        self._module_run_repo = module_run_repo

    def run(self, builder_input: CaptionBuilderInput) -> CaptionBuilderResult:
        project = self._project_repo.get_project(self._db_engine, builder_input.project_id)
        if project.state is not ProjectState.MVP_COMPLETE:
            raise RendererStateError(
                f"CaptionBuilder requires project state MVP_COMPLETE, got {project.state.value}"
            )

        script_plan = self._load_and_verify_script_plan(project)
        voice_plan = self._load_and_verify_voice_plan(project)
        self._verify_voice_plan_is_fresh(project, voice_plan)
        visual_plan = self._load_and_verify_visual_plan(project)
        self._verify_visual_plan_is_fresh(project, voice_plan, visual_plan)
        assembly_plan = self._load_and_verify_assembly_plan(project)
        self._verify_assembly_plan_is_fresh(project, voice_plan, visual_plan, assembly_plan)
        timeline_manifest = self._load_and_verify_timeline_manifest(project)
        voice_render_manifest = self._load_and_verify_voice_render_manifest(project)
        visual_render_manifest = self._load_and_verify_visual_render_manifest(project)
        self._verify_timeline_manifest_is_fresh(
            script_plan, voice_plan, visual_plan, assembly_plan,
            timeline_manifest, voice_render_manifest, visual_render_manifest,
        )

        run_record = ModuleRun(
            project_id=builder_input.project_id,
            module=MODULE_NAME,
            module_version=MODULE_VERSION,
            started_at=datetime.now(timezone.utc),
            input_ids=[str(timeline_manifest.id)],
            status=ModuleRunStatus.RUNNING,
        )
        self._module_run_repo.save_module_run(self._db_engine, run_record)

        try:
            manifest = build_caption_manifest(
                builder_input.project_id, timeline_manifest, voice_plan, script_plan
            )
            self._artifact_repo.save_artifact(
                self._db_engine, builder_input.project_id, CAPTION_MANIFEST_ARTIFACT_TYPE, manifest
            )
        except Exception as exc:
            self._module_run_repo.save_module_run(self._db_engine, _with_failure(run_record, exc))
            raise

        self._module_run_repo.save_module_run(self._db_engine, _with_success(run_record, manifest.id))

        return CaptionBuilderResult(manifest=manifest, module_run_id=run_record.run_id)

    # ------------------------------------------------------------------
    # Load + freshness -- identical to app/renderers/video/renderer.py
    # ------------------------------------------------------------------

    def _load_and_verify_script_plan(self, project: Project) -> ScriptPlan:
        if project.script_plan_id is None:
            raise MissingScriptPlanArtifactError(
                f"Project {project.project_id} has no script_plan_id reference"
            )
        try:
            script_plan = self._artifact_repo.get_artifact(
                self._db_engine, project.project_id, _SCRIPT_PLAN_ARTIFACT_TYPE, ScriptPlan
            )
        except (ArtifactNotFoundError, ArtifactValidationError) as exc:
            raise MissingScriptPlanArtifactError(
                f"Project {project.project_id} script_plan_id references a missing or "
                f"invalid ScriptPlan artifact"
            ) from exc
        if script_plan.id != project.script_plan_id:
            raise MissingScriptPlanArtifactError(
                f"Stored ScriptPlan id does not match project.script_plan_id for project "
                f"{project.project_id}"
            )
        return script_plan

    def _load_and_verify_voice_plan(self, project: Project) -> VoicePlan:
        try:
            return self._artifact_repo.get_artifact(
                self._db_engine, project.project_id, _VOICE_PLAN_ARTIFACT_TYPE, VoicePlan
            )
        except (ArtifactNotFoundError, ArtifactValidationError) as exc:
            raise MissingVoicePlanArtifactError(
                f"Project {project.project_id} has no valid VoicePlan artifact"
            ) from exc

    def _verify_voice_plan_is_fresh(self, project: Project, voice_plan: VoicePlan) -> None:
        if voice_plan.script_plan_id != project.script_plan_id:
            raise StaleVoicePlanError(
                f"The current VoicePlan for project {project.project_id} was generated for "
                f"ScriptPlan {voice_plan.script_plan_id}, but the project currently "
                f"references ScriptPlan {project.script_plan_id}"
            )

    def _load_and_verify_visual_plan(self, project: Project) -> VisualPlan:
        try:
            return self._artifact_repo.get_artifact(
                self._db_engine, project.project_id, _VISUAL_PLAN_ARTIFACT_TYPE, VisualPlan
            )
        except (ArtifactNotFoundError, ArtifactValidationError) as exc:
            raise MissingVisualPlanArtifactError(
                f"Project {project.project_id} has no valid VisualPlan artifact"
            ) from exc

    def _verify_visual_plan_is_fresh(
        self, project: Project, voice_plan: VoicePlan, visual_plan: VisualPlan
    ) -> None:
        if visual_plan.script_plan_id != project.script_plan_id:
            raise StaleVisualPlanError(
                f"The current VisualPlan for project {project.project_id} was generated for "
                f"ScriptPlan {visual_plan.script_plan_id}, but the project currently "
                f"references ScriptPlan {project.script_plan_id}"
            )
        if visual_plan.voice_plan_id != voice_plan.id:
            raise StaleVisualPlanError(
                f"The current VisualPlan for project {project.project_id} was generated for "
                f"VoicePlan {visual_plan.voice_plan_id}, but the current VoicePlan is "
                f"{voice_plan.id}"
            )

    def _load_and_verify_assembly_plan(self, project: Project) -> AssemblyPlan:
        try:
            return self._artifact_repo.get_artifact(
                self._db_engine, project.project_id, _ASSEMBLY_PLAN_ARTIFACT_TYPE, AssemblyPlan
            )
        except (ArtifactNotFoundError, ArtifactValidationError) as exc:
            raise MissingAssemblyPlanArtifactError(
                f"Project {project.project_id} has no valid AssemblyPlan artifact"
            ) from exc

    def _verify_assembly_plan_is_fresh(
        self, project: Project, voice_plan: VoicePlan, visual_plan: VisualPlan, assembly_plan: AssemblyPlan
    ) -> None:
        if assembly_plan.script_plan_id != project.script_plan_id:
            raise StaleAssemblyPlanError(
                f"The current AssemblyPlan for project {project.project_id} was generated "
                f"for ScriptPlan {assembly_plan.script_plan_id}, but the project currently "
                f"references ScriptPlan {project.script_plan_id}"
            )
        if assembly_plan.voice_plan_id != voice_plan.id:
            raise StaleAssemblyPlanError(
                f"The current AssemblyPlan for project {project.project_id} was generated "
                f"for VoicePlan {assembly_plan.voice_plan_id}, but the current VoicePlan is "
                f"{voice_plan.id}"
            )
        if assembly_plan.visual_plan_id != visual_plan.id:
            raise StaleAssemblyPlanError(
                f"The current AssemblyPlan for project {project.project_id} was generated "
                f"for VisualPlan {assembly_plan.visual_plan_id}, but the current VisualPlan "
                f"is {visual_plan.id}"
            )

    def _load_and_verify_timeline_manifest(self, project: Project) -> TimelineManifest:
        try:
            return self._artifact_repo.get_artifact(
                self._db_engine, project.project_id, TIMELINE_MANIFEST_ARTIFACT_TYPE, TimelineManifest
            )
        except (ArtifactNotFoundError, ArtifactValidationError) as exc:
            raise MissingTimelineManifestArtifactError(
                f"Project {project.project_id} has no valid TimelineManifest artifact -- "
                f"run TimelineBuilder before caption building"
            ) from exc

    def _load_and_verify_voice_render_manifest(self, project: Project) -> VoiceRenderManifest:
        try:
            return self._artifact_repo.get_artifact(
                self._db_engine, project.project_id, VOICE_RENDER_MANIFEST_ARTIFACT_TYPE, VoiceRenderManifest
            )
        except (ArtifactNotFoundError, ArtifactValidationError) as exc:
            raise MissingVoiceRenderManifestArtifactError(
                f"Project {project.project_id} has no valid VoiceRenderManifest artifact"
            ) from exc

    def _load_and_verify_visual_render_manifest(self, project: Project) -> VisualRenderManifest:
        try:
            return self._artifact_repo.get_artifact(
                self._db_engine, project.project_id, VISUAL_RENDER_MANIFEST_ARTIFACT_TYPE, VisualRenderManifest
            )
        except (ArtifactNotFoundError, ArtifactValidationError) as exc:
            raise MissingVisualRenderManifestArtifactError(
                f"Project {project.project_id} has no valid VisualRenderManifest artifact"
            ) from exc

    @staticmethod
    def _verify_timeline_manifest_is_fresh(
        script_plan: ScriptPlan,
        voice_plan: VoicePlan,
        visual_plan: VisualPlan,
        assembly_plan: AssemblyPlan,
        timeline_manifest: TimelineManifest,
        voice_render_manifest: VoiceRenderManifest,
        visual_render_manifest: VisualRenderManifest,
    ) -> None:
        if timeline_manifest.script_plan_id != script_plan.id:
            raise StaleTimelineManifestError(
                f"The current TimelineManifest was built for ScriptPlan "
                f"{timeline_manifest.script_plan_id}, but the current ScriptPlan is "
                f"{script_plan.id} -- rerun TimelineBuilder first"
            )
        if timeline_manifest.voice_plan_id != voice_plan.id:
            raise StaleTimelineManifestError(
                f"The current TimelineManifest was built for VoicePlan "
                f"{timeline_manifest.voice_plan_id}, but the current VoicePlan is "
                f"{voice_plan.id} -- rerun TimelineBuilder first"
            )
        if timeline_manifest.visual_plan_id != visual_plan.id:
            raise StaleTimelineManifestError(
                f"The current TimelineManifest was built for VisualPlan "
                f"{timeline_manifest.visual_plan_id}, but the current VisualPlan is "
                f"{visual_plan.id} -- rerun TimelineBuilder first"
            )
        if timeline_manifest.assembly_plan_id != assembly_plan.id:
            raise StaleTimelineManifestError(
                f"The current TimelineManifest was built for AssemblyPlan "
                f"{timeline_manifest.assembly_plan_id}, but the current AssemblyPlan is "
                f"{assembly_plan.id} -- rerun TimelineBuilder first"
            )
        if timeline_manifest.voice_render_manifest_id != voice_render_manifest.id:
            raise StaleTimelineManifestError(
                f"The current TimelineManifest was built from VoiceRenderManifest "
                f"{timeline_manifest.voice_render_manifest_id}, but the current "
                f"VoiceRenderManifest is {voice_render_manifest.id} -- voice audio was "
                f"re-rendered since the timeline was built; rerun TimelineBuilder first"
            )
        if timeline_manifest.visual_render_manifest_id != visual_render_manifest.id:
            raise StaleTimelineManifestError(
                f"The current TimelineManifest was built from VisualRenderManifest "
                f"{timeline_manifest.visual_render_manifest_id}, but the current "
                f"VisualRenderManifest is {visual_render_manifest.id} -- visual assets were "
                f"re-rendered since the timeline was built; rerun TimelineBuilder first"
            )


def _with_success(run_record: ModuleRun, manifest_id) -> ModuleRun:
    return ModuleRun(
        run_id=run_record.run_id,
        project_id=run_record.project_id,
        module=run_record.module,
        module_version=run_record.module_version,
        started_at=run_record.started_at,
        completed_at=datetime.now(timezone.utc),
        input_ids=run_record.input_ids,
        output_id=str(manifest_id),
        status=ModuleRunStatus.SUCCESS,
    )


def _with_failure(run_record: ModuleRun, exc: Exception) -> ModuleRun:
    return ModuleRun(
        run_id=run_record.run_id,
        project_id=run_record.project_id,
        module=run_record.module,
        module_version=run_record.module_version,
        started_at=run_record.started_at,
        completed_at=datetime.now(timezone.utc),
        input_ids=run_record.input_ids,
        status=ModuleRunStatus.FAILED,
        error_message=_safe_error_message(exc),
    )


def _safe_error_message(exc: Exception) -> str:
    message = str(exc).strip() or type(exc).__name__
    max_len = 500
    if len(message) > max_len:
        message = message[:max_len] + "... (truncated)"
    return message
