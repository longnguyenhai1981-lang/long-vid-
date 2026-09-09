"""MediaQCRenderer: loads a project's current final deliverable
(preferring a CaptionedVideoAsset when one exists and is fresh, else the
plain EncodedVideoAsset) plus its optional SubtitleFileAsset/
CaptionManifest, and runs the real MediaQCInspector against them (Phase
32) -- the QC counterpart to Phase 28's VideoRenderer and Phase 31's
CaptionBuilder/SubtitleRenderer.

Freshness chain is deliberately the SAME full script/voice/visual/
assembly/timeline chain every prior renderer in this project already
verifies (Phase 32 requirement #17's own "reuse existing freshness
conventions... upstream timeline freshness chain remains valid"), PLUS a
direct check that the EncodedVideoAsset being inspected was itself
encoded from the CURRENT TimelineManifest. Captions are entirely
OPTIONAL for a given project -- a MISSING CaptionManifest/
SubtitleFileAsset/CaptionedVideoAsset is not an error, but a STALE one
(referencing an id other than the current chain) is rejected explicitly,
never silently inspected as if it were current.

QC never regenerates or fixes anything -- this module only loads
already-produced artifacts, delegates to the standalone
MediaQCInspector, and persists whatever MediaQCReport it returns.
ModuleRun is SUCCESS whenever the inspection itself completed, REGARDLESS
of whether the resulting report says PASS/WARN/FAIL (Phase 32
requirement #25) -- FAILED is reserved for the inspector genuinely being
unable to run at all (an infrastructure error).
"""

from __future__ import annotations

from datetime import datetime, timezone
from uuid import UUID

from sqlalchemy import Engine

from app.media_qc.inspector import MediaQCInspector
from app.media_qc.models import MediaQCReport, MediaQCRequest
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
from app.renderers.media_qc.errors import (
    MissingAssemblyPlanArtifactError,
    MissingEncodedVideoAssetArtifactError,
    MissingScriptPlanArtifactError,
    MissingTimelineManifestArtifactError,
    MissingVisualPlanArtifactError,
    MissingVisualRenderManifestArtifactError,
    MissingVoicePlanArtifactError,
    MissingVoiceRenderManifestArtifactError,
    RendererStateError,
    StaleAssemblyPlanError,
    StaleCaptionedVideoAssetError,
    StaleCaptionManifestError,
    StaleEncodedVideoAssetError,
    StaleSubtitleFileAssetError,
    StaleTimelineManifestError,
    StaleVisualPlanError,
    StaleVoicePlanError,
)
from app.renderers.media_qc.models import MEDIA_QC_REPORT_ARTIFACT_TYPE, MediaQCRendererInput, MediaQCRendererResult
from app.renderers.subtitle.models import CAPTIONED_VIDEO_ASSET_ARTIFACT_TYPE, SUBTITLE_FILE_ASSET_ARTIFACT_TYPE
from app.renderers.timeline.models import TIMELINE_MANIFEST_ARTIFACT_TYPE
from app.renderers.video.models import ENCODED_VIDEO_ASSET_ARTIFACT_TYPE
from app.renderers.visual.models import VISUAL_RENDER_MANIFEST_ARTIFACT_TYPE
from app.renderers.voice.models import VOICE_RENDER_MANIFEST_ARTIFACT_TYPE
from app.storage import artifacts as artifact_storage
from app.storage import module_runs as module_run_storage
from app.storage import projects as project_storage
from app.storage.errors import ArtifactNotFoundError, ArtifactValidationError

# Duplicated rather than imported -- see app/renderers/video/renderer.py's
# identical precedent (app.llm transitive-import avoidance).
_SCRIPT_PLAN_ARTIFACT_TYPE = "script_plan"
_VOICE_PLAN_ARTIFACT_TYPE = "voice_plan"
_VISUAL_PLAN_ARTIFACT_TYPE = "visual_plan"
_ASSEMBLY_PLAN_ARTIFACT_TYPE = "assembly_plan"

MODULE_NAME = "media_qc_renderer"
MODULE_VERSION = "0.1"


class MediaQCRenderer:
    def __init__(
        self,
        db_engine: Engine,
        video_store,
        subtitle_store,
        *,
        inspector: MediaQCInspector | None = None,
        project_repo=project_storage,
        artifact_repo=artifact_storage,
        module_run_repo=module_run_storage,
    ):
        self._db_engine = db_engine
        self._video_store = video_store
        self._subtitle_store = subtitle_store
        self._inspector = inspector if inspector is not None else MediaQCInspector()
        self._project_repo = project_repo
        self._artifact_repo = artifact_repo
        self._module_run_repo = module_run_repo

    def run(self, renderer_input: MediaQCRendererInput) -> MediaQCRendererResult:
        project = self._project_repo.get_project(self._db_engine, renderer_input.project_id)
        if project.state is not ProjectState.MVP_COMPLETE:
            raise RendererStateError(
                f"MediaQCRenderer requires project state MVP_COMPLETE, got {project.state.value}"
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

        encoded_asset = self._load_and_verify_encoded_video_asset(project)
        self._verify_encoded_video_asset_is_fresh(encoded_asset, timeline_manifest)

        caption_manifest = self._load_optional_caption_manifest(project)
        if caption_manifest is not None:
            self._verify_caption_manifest_is_fresh(caption_manifest, timeline_manifest)

        subtitle_asset = self._load_optional_subtitle_file_asset(project)
        if subtitle_asset is not None and caption_manifest is not None:
            self._verify_subtitle_file_asset_is_fresh(subtitle_asset, caption_manifest)

        captioned_asset = self._load_optional_captioned_video_asset(project)
        if captioned_asset is not None:
            self._verify_captioned_video_asset_is_fresh(captioned_asset, encoded_asset, caption_manifest, subtitle_asset)

        run_record = ModuleRun(
            project_id=renderer_input.project_id,
            module=MODULE_NAME,
            module_version=MODULE_VERSION,
            started_at=datetime.now(timezone.utc),
            input_ids=[str(encoded_asset.id)],
            status=ModuleRunStatus.RUNNING,
        )
        self._module_run_repo.save_module_run(self._db_engine, run_record)

        try:
            report = self._inspect(
                renderer_input.project_id, timeline_manifest, encoded_asset,
                captioned_asset, caption_manifest, subtitle_asset,
            )
            self._artifact_repo.save_artifact(
                self._db_engine, renderer_input.project_id, MEDIA_QC_REPORT_ARTIFACT_TYPE, report
            )
        except Exception as exc:
            # An exception here means the INSPECTOR ITSELF could not run
            # (infrastructure failure) -- an ordinary media defect is
            # already represented as a FAIL QCCheckResult inside a
            # successfully-returned report, never an exception (Phase 32
            # requirement #23/#25).
            self._module_run_repo.save_module_run(self._db_engine, _with_failure(run_record, exc))
            raise

        self._module_run_repo.save_module_run(self._db_engine, _with_success(run_record, report.id))

        return MediaQCRendererResult(report=report, module_run_id=run_record.run_id)

    # ------------------------------------------------------------------
    # Inspection
    # ------------------------------------------------------------------

    def _inspect(
        self,
        project_id: UUID,
        timeline_manifest: TimelineManifest,
        encoded_asset: EncodedVideoAsset,
        captioned_asset: CaptionedVideoAsset | None,
        caption_manifest: CaptionManifest | None,
        subtitle_asset: SubtitleFileAsset | None,
    ) -> MediaQCReport:
        # Prefer the captioned deliverable when one exists and is fresh
        # -- it is the more final artifact a human would actually review.
        if captioned_asset is not None:
            video_path = self._video_store.root / captioned_asset.file_path
            source_video_asset_id = captioned_asset.id
            expected_video_codec = captioned_asset.video_codec
            expected_audio_codec = captioned_asset.audio_codec
            expected_width, expected_height, expected_fps = (
                captioned_asset.width, captioned_asset.height, captioned_asset.fps,
            )
            expected_duration_ms = captioned_asset.duration_ms
        else:
            video_path = self._video_store.root / encoded_asset.file_path
            source_video_asset_id = encoded_asset.id
            expected_video_codec = encoded_asset.video_codec
            expected_audio_codec = encoded_asset.audio_codec
            expected_width, expected_height, expected_fps = (
                encoded_asset.width, encoded_asset.height, encoded_asset.fps,
            )
            expected_duration_ms = encoded_asset.duration_ms

        subtitle_path = None
        if subtitle_asset is not None and caption_manifest is not None:
            subtitle_path = self._subtitle_store.root / subtitle_asset.file_path

        request = MediaQCRequest(
            project_id=project_id,
            source_video_asset_id=source_video_asset_id,
            timeline_manifest_id=timeline_manifest.id,
            video_path=video_path,
            expected_video_codec=expected_video_codec,
            expected_audio_codec=expected_audio_codec,
            expected_pixel_format="yuv420p",
            expected_fps=float(expected_fps),
            expected_width=expected_width,
            expected_height=expected_height,
            expected_duration_ms=expected_duration_ms,
            subtitle_path=subtitle_path,
            caption_manifest=caption_manifest if subtitle_path is not None else None,
            subtitle_file_asset_id=subtitle_asset.id if subtitle_asset is not None else None,
        )
        return self._inspector.inspect(request)

    # ------------------------------------------------------------------
    # Load + freshness -- full chain identical to every prior renderer
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

    def _load_and_verify_encoded_video_asset(self, project: Project) -> EncodedVideoAsset:
        try:
            return self._artifact_repo.get_artifact(
                self._db_engine, project.project_id, ENCODED_VIDEO_ASSET_ARTIFACT_TYPE, EncodedVideoAsset
            )
        except (ArtifactNotFoundError, ArtifactValidationError) as exc:
            raise MissingEncodedVideoAssetArtifactError(
                f"Project {project.project_id} has no valid EncodedVideoAsset artifact -- "
                f"run VideoRenderer before QC"
            ) from exc

    @staticmethod
    def _verify_encoded_video_asset_is_fresh(
        encoded_asset: EncodedVideoAsset, timeline_manifest: TimelineManifest
    ) -> None:
        if encoded_asset.timeline_manifest_id != timeline_manifest.id:
            raise StaleEncodedVideoAssetError(
                f"The current EncodedVideoAsset was encoded from TimelineManifest "
                f"{encoded_asset.timeline_manifest_id}, but the current TimelineManifest is "
                f"{timeline_manifest.id} -- rerun VideoRenderer first"
            )

    def _load_optional_caption_manifest(self, project: Project) -> CaptionManifest | None:
        try:
            return self._artifact_repo.get_artifact(
                self._db_engine, project.project_id, CAPTION_MANIFEST_ARTIFACT_TYPE, CaptionManifest
            )
        except (ArtifactNotFoundError, ArtifactValidationError):
            return None

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

    def _load_optional_subtitle_file_asset(self, project: Project) -> SubtitleFileAsset | None:
        try:
            return self._artifact_repo.get_artifact(
                self._db_engine, project.project_id, SUBTITLE_FILE_ASSET_ARTIFACT_TYPE, SubtitleFileAsset
            )
        except (ArtifactNotFoundError, ArtifactValidationError):
            return None

    @staticmethod
    def _verify_subtitle_file_asset_is_fresh(
        subtitle_asset: SubtitleFileAsset, caption_manifest: CaptionManifest
    ) -> None:
        if subtitle_asset.caption_manifest_id != caption_manifest.id:
            raise StaleSubtitleFileAssetError(
                f"The current SubtitleFileAsset was exported from CaptionManifest "
                f"{subtitle_asset.caption_manifest_id}, but the current CaptionManifest is "
                f"{caption_manifest.id} -- rerun SubtitleRenderer first"
            )

    def _load_optional_captioned_video_asset(self, project: Project) -> CaptionedVideoAsset | None:
        try:
            return self._artifact_repo.get_artifact(
                self._db_engine, project.project_id, CAPTIONED_VIDEO_ASSET_ARTIFACT_TYPE, CaptionedVideoAsset
            )
        except (ArtifactNotFoundError, ArtifactValidationError):
            return None

    @staticmethod
    def _verify_captioned_video_asset_is_fresh(
        captioned_asset: CaptionedVideoAsset,
        encoded_asset: EncodedVideoAsset,
        caption_manifest: CaptionManifest | None,
        subtitle_asset: SubtitleFileAsset | None,
    ) -> None:
        if captioned_asset.source_encoded_video_asset_id != encoded_asset.id:
            raise StaleCaptionedVideoAssetError(
                f"The current CaptionedVideoAsset was burned from EncodedVideoAsset "
                f"{captioned_asset.source_encoded_video_asset_id}, but the current "
                f"EncodedVideoAsset is {encoded_asset.id} -- rerun VideoRenderer/"
                f"SubtitleRenderer first"
            )
        if caption_manifest is None or captioned_asset.caption_manifest_id != caption_manifest.id:
            raise StaleCaptionedVideoAssetError(
                "The current CaptionedVideoAsset references a CaptionManifest that is "
                "missing or no longer current -- rerun CaptionBuilder/SubtitleRenderer first"
            )
        if subtitle_asset is None or captioned_asset.subtitle_file_asset_id != subtitle_asset.id:
            raise StaleCaptionedVideoAssetError(
                "The current CaptionedVideoAsset references a SubtitleFileAsset that is "
                "missing or no longer current -- rerun SubtitleRenderer first"
            )


def _with_success(run_record: ModuleRun, report_id) -> ModuleRun:
    return ModuleRun(
        run_id=run_record.run_id,
        project_id=run_record.project_id,
        module=run_record.module,
        module_version=run_record.module_version,
        started_at=run_record.started_at,
        completed_at=datetime.now(timezone.utc),
        input_ids=run_record.input_ids,
        output_id=str(report_id),
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
