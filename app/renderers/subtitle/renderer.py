"""SubtitleRenderer: turns an already-built CaptionManifest into a real,
UTF-8 SRT file (always) and, only when explicitly requested, a new
locally-burned-in MP4 (Phase 31) -- the execution counterpart to Phase
31's own CaptionBuilder, mirroring how Phase 28's VideoRenderer sits
downstream of Phase 27's TimelineBuilder.

    CaptionManifest -> render_srt() -> SubtitleFileAsset (always)
                                      -> [opt-in] CaptionBurnInRenderer -> CaptionedVideoAsset

Freshness chain is the SAME full script/voice/visual/assembly/timeline
chain app/renderers/caption/builder.py and app/renderers/video/
renderer.py both already verify (Phase 31 requirement #9's "prefer full
timeline freshness" policy), PLUS a direct check that the current
CaptionManifest was built from the CURRENT TimelineManifest (not a
stale/superseded one). Burn-in additionally verifies the current
EncodedVideoAsset was itself encoded from the same current
TimelineManifest.

Burn-in is opt-in (SubtitleRendererInput.burn_in_settings, default
None): a normal SRT-only run never loads an EncodedVideoAsset and never
invokes ffmpeg at all.
"""

from __future__ import annotations

from datetime import datetime, timezone
from uuid import UUID

from sqlalchemy import Engine

from app.captions.burn_in import CaptionBurnInRenderer
from app.captions.errors import CaptionError
from app.captions.models import CaptionBurnInRequest
from app.captions.srt import render_srt
from app.captions.storage import SubtitleFileStore
from app.models.assembly import AssemblyPlan
from app.models.audio import VoiceRenderManifest
from app.models.caption import CaptionManifest
from app.models.common import ModuleRunStatus, ProjectState
from app.models.module_run import ModuleRun
from app.models.project import Project
from app.models.script import ScriptPlan
from app.models.subtitle import CaptionedVideoAsset, SubtitleFileAsset
from app.models.timeline import TimelineManifest
from app.models.video import EncodedVideoAsset
from app.models.visual import VisualPlan
from app.models.visual_render import VisualRenderManifest
from app.models.voice import VoicePlan
from app.renderers.caption.models import CAPTION_MANIFEST_ARTIFACT_TYPE
from app.renderers.subtitle.errors import (
    MissingAssemblyPlanArtifactError,
    MissingCaptionManifestArtifactError,
    MissingEncodedVideoAssetArtifactError,
    MissingScriptPlanArtifactError,
    MissingTimelineManifestArtifactError,
    MissingVisualPlanArtifactError,
    MissingVisualRenderManifestArtifactError,
    MissingVoicePlanArtifactError,
    MissingVoiceRenderManifestArtifactError,
    RendererStateError,
    StaleAssemblyPlanError,
    StaleCaptionManifestError,
    StaleEncodedVideoAssetError,
    StaleTimelineManifestError,
    StaleVisualPlanError,
    StaleVoicePlanError,
)
from app.renderers.subtitle.models import (
    CAPTIONED_VIDEO_ASSET_ARTIFACT_TYPE,
    SUBTITLE_FILE_ASSET_ARTIFACT_TYPE,
    SubtitleRendererInput,
    SubtitleRendererResult,
)
from app.renderers.timeline.models import TIMELINE_MANIFEST_ARTIFACT_TYPE
from app.renderers.video.models import ENCODED_VIDEO_ASSET_ARTIFACT_TYPE
from app.renderers.visual.models import VISUAL_RENDER_MANIFEST_ARTIFACT_TYPE
from app.renderers.voice.models import VOICE_RENDER_MANIFEST_ARTIFACT_TYPE
from app.storage import artifacts as artifact_storage
from app.storage import module_runs as module_run_storage
from app.storage import projects as project_storage
from app.storage.errors import ArtifactNotFoundError, ArtifactValidationError
from app.video_encoder.storage import VideoFileStore

# Duplicated rather than imported -- see app/renderers/video/renderer.py's
# identical precedent (app.llm transitive-import avoidance).
_SCRIPT_PLAN_ARTIFACT_TYPE = "script_plan"
_VOICE_PLAN_ARTIFACT_TYPE = "voice_plan"
_VISUAL_PLAN_ARTIFACT_TYPE = "visual_plan"
_ASSEMBLY_PLAN_ARTIFACT_TYPE = "assembly_plan"

MODULE_NAME = "subtitle_renderer"
MODULE_VERSION = "0.1"


class SubtitleRenderer:
    def __init__(
        self,
        db_engine: Engine,
        subtitle_store: SubtitleFileStore,
        video_store: VideoFileStore,
        *,
        burn_in_renderer: CaptionBurnInRenderer | None = None,
        project_repo=project_storage,
        artifact_repo=artifact_storage,
        module_run_repo=module_run_storage,
    ):
        self._db_engine = db_engine
        self._subtitle_store = subtitle_store
        self._video_store = video_store
        self._burn_in_renderer = burn_in_renderer if burn_in_renderer is not None else CaptionBurnInRenderer()
        self._project_repo = project_repo
        self._artifact_repo = artifact_repo
        self._module_run_repo = module_run_repo

    def run(self, renderer_input: SubtitleRendererInput) -> SubtitleRendererResult:
        project = self._project_repo.get_project(self._db_engine, renderer_input.project_id)
        if project.state is not ProjectState.MVP_COMPLETE:
            raise RendererStateError(
                f"SubtitleRenderer requires project state MVP_COMPLETE, got {project.state.value}"
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
        caption_manifest = self._load_and_verify_caption_manifest(project)
        self._verify_caption_manifest_is_fresh(caption_manifest, timeline_manifest)

        run_record = ModuleRun(
            project_id=renderer_input.project_id,
            module=MODULE_NAME,
            module_version=MODULE_VERSION,
            started_at=datetime.now(timezone.utc),
            input_ids=[str(caption_manifest.id)],
            status=ModuleRunStatus.RUNNING,
        )
        self._module_run_repo.save_module_run(self._db_engine, run_record)

        try:
            subtitle_asset = self._export_srt(renderer_input.project_id, caption_manifest)
            self._artifact_repo.save_artifact(
                self._db_engine, renderer_input.project_id, SUBTITLE_FILE_ASSET_ARTIFACT_TYPE, subtitle_asset
            )

            captioned_video_asset = None
            if renderer_input.burn_in_settings is not None:
                encoded_video_asset = self._load_and_verify_encoded_video_asset(project)
                self._verify_encoded_video_asset_is_fresh(encoded_video_asset, timeline_manifest)
                captioned_video_asset = self._burn_in(
                    renderer_input.project_id, encoded_video_asset, subtitle_asset,
                    caption_manifest, renderer_input.burn_in_settings,
                )
                self._artifact_repo.save_artifact(
                    self._db_engine, renderer_input.project_id,
                    CAPTIONED_VIDEO_ASSET_ARTIFACT_TYPE, captioned_video_asset,
                )
        except Exception as exc:
            self._module_run_repo.save_module_run(self._db_engine, _with_failure(run_record, exc))
            raise

        self._module_run_repo.save_module_run(
            self._db_engine, _with_success(run_record, subtitle_asset.id)
        )

        return SubtitleRendererResult(
            subtitle_asset=subtitle_asset,
            captioned_video_asset=captioned_video_asset,
            module_run_id=run_record.run_id,
        )

    # ------------------------------------------------------------------
    # SRT export
    # ------------------------------------------------------------------

    def _export_srt(self, project_id: UUID, caption_manifest: CaptionManifest) -> SubtitleFileAsset:
        srt_text = render_srt(caption_manifest)
        relative_path = f"{project_id}/{caption_manifest.id}/captions.srt"
        absolute_path = self._subtitle_store.write(relative_path, srt_text)
        file_size_bytes = absolute_path.stat().st_size

        return SubtitleFileAsset(
            project_id=project_id,
            caption_manifest_id=caption_manifest.id,
            format="SRT",
            file_path=relative_path,
            cue_count=len(caption_manifest.cues),
            total_duration_ms=caption_manifest.total_duration_ms,
            file_size_bytes=file_size_bytes,
            encoding="utf-8",
            created_at=datetime.now(timezone.utc),
        )

    # ------------------------------------------------------------------
    # Burn-in
    # ------------------------------------------------------------------

    def _burn_in(
        self,
        project_id: UUID,
        encoded_video_asset: EncodedVideoAsset,
        subtitle_asset: SubtitleFileAsset,
        caption_manifest: CaptionManifest,
        settings,
    ) -> CaptionedVideoAsset:
        input_video_path = self._video_store.root / encoded_video_asset.file_path
        subtitle_path = self._subtitle_store.root / subtitle_asset.file_path
        relative_output_path = f"{project_id}/{caption_manifest.id}/captioned.mp4"
        output_path = self._video_store.root / relative_output_path

        request = CaptionBurnInRequest(
            input_video_path=input_video_path,
            subtitle_path=subtitle_path,
            output_path=output_path,
            settings=settings,
        )
        try:
            result = self._burn_in_renderer.render(request)
        except CaptionError:
            # No fallback to an un-captioned output, no silent skip --
            # propagate unchanged so run() records a FAILED ModuleRun.
            raise

        return CaptionedVideoAsset(
            project_id=project_id,
            source_encoded_video_asset_id=encoded_video_asset.id,
            caption_manifest_id=caption_manifest.id,
            subtitle_file_asset_id=subtitle_asset.id,
            file_path=relative_output_path,
            container="mp4",
            video_codec=result.video_codec,
            audio_codec=result.audio_codec,
            audio_stream_copied=result.audio_stream_copied,
            width=result.width,
            height=result.height,
            fps=result.fps,
            duration_ms=result.duration_ms,
            file_size_bytes=result.file_size_bytes,
            created_at=datetime.now(timezone.utc),
        )

    # ------------------------------------------------------------------
    # Load + freshness
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
                f"Project {project.project_id} has no valid TimelineManifest artifact"
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

    def _load_and_verify_caption_manifest(self, project: Project) -> CaptionManifest:
        try:
            return self._artifact_repo.get_artifact(
                self._db_engine, project.project_id, CAPTION_MANIFEST_ARTIFACT_TYPE, CaptionManifest
            )
        except (ArtifactNotFoundError, ArtifactValidationError) as exc:
            raise MissingCaptionManifestArtifactError(
                f"Project {project.project_id} has no valid CaptionManifest artifact -- "
                f"run CaptionBuilder before subtitle export"
            ) from exc

    @staticmethod
    def _verify_caption_manifest_is_fresh(
        caption_manifest: CaptionManifest, timeline_manifest: TimelineManifest
    ) -> None:
        if caption_manifest.timeline_manifest_id != timeline_manifest.id:
            raise StaleCaptionManifestError(
                f"The current CaptionManifest was built from TimelineManifest "
                f"{caption_manifest.timeline_manifest_id}, but the current TimelineManifest "
                f"is {timeline_manifest.id} -- rerun CaptionBuilder first"
            )

    def _load_and_verify_encoded_video_asset(self, project: Project) -> EncodedVideoAsset:
        try:
            return self._artifact_repo.get_artifact(
                self._db_engine, project.project_id, ENCODED_VIDEO_ASSET_ARTIFACT_TYPE, EncodedVideoAsset
            )
        except (ArtifactNotFoundError, ArtifactValidationError) as exc:
            raise MissingEncodedVideoAssetArtifactError(
                f"Project {project.project_id} has no valid EncodedVideoAsset artifact -- "
                f"run VideoRenderer before caption burn-in"
            ) from exc

    @staticmethod
    def _verify_encoded_video_asset_is_fresh(
        encoded_video_asset: EncodedVideoAsset, timeline_manifest: TimelineManifest
    ) -> None:
        if encoded_video_asset.timeline_manifest_id != timeline_manifest.id:
            raise StaleEncodedVideoAssetError(
                f"The current EncodedVideoAsset was encoded from TimelineManifest "
                f"{encoded_video_asset.timeline_manifest_id}, but the current "
                f"TimelineManifest is {timeline_manifest.id} -- rerun VideoRenderer first"
            )


def _with_success(run_record: ModuleRun, asset_id) -> ModuleRun:
    return ModuleRun(
        run_id=run_record.run_id,
        project_id=run_record.project_id,
        module=run_record.module,
        module_version=run_record.module_version,
        started_at=run_record.started_at,
        completed_at=datetime.now(timezone.utc),
        input_ids=run_record.input_ids,
        output_id=str(asset_id),
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
