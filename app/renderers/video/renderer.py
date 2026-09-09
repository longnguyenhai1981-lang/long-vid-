"""VideoRenderer: turns an already-locked TimelineManifest into a real,
locally playable MP4 file (Phase 28) -- the execution counterpart to
Phase 27's TimelineBuilder. TimelineBuilder describes timing; VideoRenderer
(together with the standalone app/video_encoder/ package it delegates to)
executes it.

MVP_COMPLETE -> load+verify ScriptPlan -> load+verify VoicePlan -> verify
VoicePlan freshness -> load+verify VisualPlan -> verify VisualPlan
freshness -> load+verify AssemblyPlan -> verify AssemblyPlan freshness ->
load+verify TimelineManifest -> load VoiceRenderManifest/
VisualRenderManifest -> verify TimelineManifest is still fresh against
the current plan chain AND against the exact VoiceRenderManifest/
VisualRenderManifest ids it was built from (a re-render of either without
rebuilding the timeline makes it stale, even if the underlying plans
didn't change) -> resolve every TimelineSegment's visual/narration
references into concrete files -> resolve the output canvas from the
first segment's own image dimensions -> delegate to VideoEncoder.encode()
-> persist an EncodedVideoAsset artifact -> persist ModuleRun.

This module triggers NO rendering of any kind: no VisualRenderer, no
VoiceRenderer, no TimelineBuilder, no provider, no LLM call. Every file it
touches must already exist; a segment whose visual was never actually
rendered (a VisualRenderRequirement rather than a RenderedVisualAsset --
e.g. a standalone canonical Tí reference) cannot be encoded and fails
explicitly (UnrenderedVisualSegmentError) rather than being skipped or
rendered on the fly.

Canvas policy (Phase 28 requirement #4): the FIRST TimelineSegment's own
resolved image establishes the output width/height (read once via
Pillow, already a project dependency since Phase 22); every subsequent
segment's image must match exactly, or app/video_encoder/encoder.py's own
VideoDimensionMismatchError fails the whole encode before ffmpeg ever
runs. There is no 1920x1080 hardcoded default -- if the timeline's own
visuals already happen to be 1920x1080, that is what gets used, purely as
a consequence of this policy, never a separate special case.
"""

from __future__ import annotations

from datetime import datetime, timezone
from uuid import UUID

from PIL import Image
from sqlalchemy import Engine

from app.audio.storage import AudioFileStore
from app.models.assembly import AssemblyPlan
from app.models.audio import VoiceRenderManifest
from app.models.common import ModuleRunStatus, ProjectState
from app.models.module_run import ModuleRun
from app.models.project import Project
from app.models.script import ScriptPlan
from app.models.timeline import TimelineManifest, TimelineSegment, TimelineVisualSourceStatus
from app.models.video import EncodedVideoAsset
from app.models.visual import VisualPlan
from app.models.visual_render import VisualRenderManifest
from app.models.voice import VoicePlan
from app.renderers.timeline.models import TIMELINE_MANIFEST_ARTIFACT_TYPE
from app.renderers.video.errors import (
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
    UnrenderedVisualSegmentError,
)
from app.renderers.video.models import ENCODED_VIDEO_ASSET_ARTIFACT_TYPE, VideoRendererInput, VideoRendererResult
from app.renderers.visual.models import VISUAL_RENDER_MANIFEST_ARTIFACT_TYPE
from app.renderers.voice.models import VOICE_RENDER_MANIFEST_ARTIFACT_TYPE
from app.storage import artifacts as artifact_storage
from app.storage import module_runs as module_run_storage
from app.storage import projects as project_storage
from app.storage.errors import ArtifactNotFoundError, ArtifactValidationError
from app.video_encoder.encoder import VideoEncoder
from app.video_encoder.errors import VideoEncoderError
from app.video_encoder.models import (
    AudioAssetBindings,
    VideoEncodeRequest,
    VideoEncodingSettings,
    VideoNarrationClip,
    VideoSegmentInput,
)
from app.video_encoder.storage import VideoFileStore
from app.visual.storage import VisualFileStore

# Duplicated from app.engines.script.models.SCRIPT_PLAN_ARTIFACT_TYPE,
# app.engines.voice_plan.models.VOICE_PLAN_ARTIFACT_TYPE,
# app.engines.visual_plan.models.VISUAL_PLAN_ARTIFACT_TYPE, and
# app.engines.assembly_plan.models.ASSEMBLY_PLAN_ARTIFACT_TYPE rather than
# imported: each of those modules imports app.llm.models, and this
# package must stay free of app.llm even transitively -- see
# app/renderers/timeline/builder.py's identical precedent.
# TIMELINE_MANIFEST_ARTIFACT_TYPE/VOICE_RENDER_MANIFEST_ARTIFACT_TYPE/
# VISUAL_RENDER_MANIFEST_ARTIFACT_TYPE are imported directly instead:
# their source modules are themselves renderer-layer and already free of
# app.llm.
_SCRIPT_PLAN_ARTIFACT_TYPE = "script_plan"
_VOICE_PLAN_ARTIFACT_TYPE = "voice_plan"
_VISUAL_PLAN_ARTIFACT_TYPE = "visual_plan"
_ASSEMBLY_PLAN_ARTIFACT_TYPE = "assembly_plan"

MODULE_NAME = "video_renderer"
MODULE_VERSION = "0.1"


class VideoRenderer:
    def __init__(
        self,
        db_engine: Engine,
        audio_store: AudioFileStore,
        visual_store: VisualFileStore,
        video_store: VideoFileStore,
        *,
        video_encoder: VideoEncoder | None = None,
        project_repo=project_storage,
        artifact_repo=artifact_storage,
        module_run_repo=module_run_storage,
    ):
        self._db_engine = db_engine
        self._audio_store = audio_store
        self._visual_store = visual_store
        self._video_store = video_store
        # Like DiagramRenderer/VisualLayerCompositor, VideoEncoder has no
        # external state and no "unconfigured" failure mode -- a default
        # one is instantiated when not injected.
        self._video_encoder = video_encoder if video_encoder is not None else VideoEncoder()
        self._project_repo = project_repo
        self._artifact_repo = artifact_repo
        self._module_run_repo = module_run_repo

    def run(self, renderer_input: VideoRendererInput) -> VideoRendererResult:
        project = self._project_repo.get_project(self._db_engine, renderer_input.project_id)
        if project.state is not ProjectState.MVP_COMPLETE:
            raise RendererStateError(
                f"VideoRenderer requires project state MVP_COMPLETE, got {project.state.value}"
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
            project_id=renderer_input.project_id,
            module=MODULE_NAME,
            module_version=MODULE_VERSION,
            started_at=datetime.now(timezone.utc),
            input_ids=[str(timeline_manifest.id)],
            status=ModuleRunStatus.RUNNING,
        )
        self._module_run_repo.save_module_run(self._db_engine, run_record)

        try:
            asset = self._encode(
                renderer_input.project_id, timeline_manifest, renderer_input.audio_bindings
            )
            self._artifact_repo.save_artifact(
                self._db_engine, renderer_input.project_id, ENCODED_VIDEO_ASSET_ARTIFACT_TYPE, asset
            )
            # No Project reference update, no state transition -- artifact-
            # driven, not workflow-driven, exactly like every renderer
            # before it.
        except Exception as exc:
            self._module_run_repo.save_module_run(self._db_engine, _with_failure(run_record, exc))
            raise

        self._module_run_repo.save_module_run(self._db_engine, _with_success(run_record, asset.id))

        return VideoRendererResult(asset=asset, module_run_id=run_record.run_id)

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
                f"Project {project.project_id} has no valid TimelineManifest artifact -- "
                f"run TimelineBuilder before video encoding"
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

    # ------------------------------------------------------------------
    # Encode
    # ------------------------------------------------------------------

    def _encode(
        self,
        project_id: UUID,
        timeline_manifest: TimelineManifest,
        audio_bindings: AudioAssetBindings | None,
    ) -> EncodedVideoAsset:
        segment_inputs = [self._resolve_segment_input(segment) for segment in timeline_manifest.segments]
        width, height = self._resolve_canvas_size(segment_inputs[0])

        relative_path = f"{project_id}/{timeline_manifest.id}/video.mp4"
        output_path = self._video_store.root / relative_path

        # Phase 30: audio_bindings is None (the default) unless the caller
        # explicitly opts in -- in that case cues/bindings are simply not
        # forwarded at all, so VideoEncoder's own audio graph is exactly
        # Phase 29's, regardless of what MUSIC_*/SFX_TRIGGER cues the
        # manifest happens to carry (every MusicState has no "none" value,
        # so a real manifest always carries at least one music cue).
        request = VideoEncodeRequest(
            segments=segment_inputs,
            settings=VideoEncodingSettings(width=width, height=height),
            output_path=output_path,
            cues=timeline_manifest.cues if audio_bindings is not None else [],
            audio_bindings=audio_bindings if audio_bindings is not None else AudioAssetBindings(),
        )

        try:
            result = self._video_encoder.encode(request)
        except VideoEncoderError:
            # No provider fallback, no imagery/audio regeneration, no
            # silent timing change -- propagate unchanged so run()
            # records a FAILED ModuleRun.
            raise

        return EncodedVideoAsset(
            project_id=project_id,
            timeline_manifest_id=timeline_manifest.id,
            file_path=relative_path,
            container="mp4",
            video_codec=result.video_codec,
            audio_codec=result.audio_codec,
            width=result.width,
            height=result.height,
            fps=result.fps,
            duration_ms=result.duration_ms,
            file_size_bytes=result.file_size_bytes,
            created_at=datetime.now(timezone.utc),
            crossfade_count=result.crossfade_count,
            motion_profile_used=result.motion_profile_used,
            has_music=result.has_music,
            sfx_event_count=result.sfx_event_count,
            music_cue_count=result.music_cue_count,
        )

    def _resolve_segment_input(self, segment: TimelineSegment) -> VideoSegmentInput:
        if segment.visual.status is TimelineVisualSourceStatus.REQUIREMENT:
            raise UnrenderedVisualSegmentError(
                f"TimelineSegment {segment.segment_id!r}'s visual beat "
                f"{segment.visual.visual_beat_id!r} was never rendered to a file "
                f"(status=REQUIREMENT) -- Phase 28 cannot encode without a real image"
            )

        image_path = self._visual_store.root / segment.visual.file_path
        narration_clips = [
            VideoNarrationClip(file_path=self._audio_store.root / ref.file_path)
            for ref in segment.narration
        ]

        return VideoSegmentInput(
            segment_id=segment.segment_id,
            image_path=image_path,
            duration_ms=segment.duration_ms,
            narration_clips=narration_clips,
            transition_in=segment.transition_in,
            transition_out=segment.transition_out,
            motion=segment.visual_motion,
        )

    @staticmethod
    def _resolve_canvas_size(first_segment_input: VideoSegmentInput) -> tuple[int, int]:
        with Image.open(first_segment_input.image_path) as image:
            return image.size


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
