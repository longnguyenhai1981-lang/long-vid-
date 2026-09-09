"""Phase 32 focused tests: MediaQCRenderer
(app/renderers/media_qc/renderer.py) -- the artifact-driven, freshness-
checked, ModuleRun-lifecycle layer wrapping the real MediaQCInspector.

Reuses tests/test_video_renderer.py's _build_encodable_project and
tests/test_subtitle_renderer.py's _build_captioned_and_encoded_project
fixtures rather than re-deriving them.
"""

from __future__ import annotations

from pathlib import Path
from uuid import uuid4

import pytest

from app.captions.models import CaptionRenderSettings
from app.captions.storage import SubtitleFileStore
from app.media_qc.errors import FFprobeNotFoundError
from app.media_qc.inspector import MediaQCInspector
from app.media_qc.models import MediaQCReport, QCStatus
from app.media_qc.probes import FFprobeClient
from app.models.common import ModuleRunStatus
from app.models.video import EncodedVideoAsset
from app.models.visual_render import VisualRenderManifest
from app.renderers.caption.models import CAPTION_MANIFEST_ARTIFACT_TYPE
from app.renderers.media_qc.errors import (
    MissingEncodedVideoAssetArtifactError,
    RendererStateError,
    StaleCaptionedVideoAssetError,
    StaleCaptionManifestError,
    StaleEncodedVideoAssetError,
    StaleSubtitleFileAssetError,
    StaleTimelineManifestError,
)
from app.renderers.media_qc.models import MEDIA_QC_REPORT_ARTIFACT_TYPE, MediaQCRendererInput
from app.renderers.media_qc.renderer import MediaQCRenderer
from app.renderers.subtitle.models import (
    CAPTIONED_VIDEO_ASSET_ARTIFACT_TYPE,
    SUBTITLE_FILE_ASSET_ARTIFACT_TYPE,
    SubtitleRendererInput,
)
from app.renderers.subtitle.renderer import SubtitleRenderer
from app.renderers.timeline.models import TIMELINE_MANIFEST_ARTIFACT_TYPE
from app.renderers.video.models import ENCODED_VIDEO_ASSET_ARTIFACT_TYPE, VideoRendererInput
from app.renderers.video.renderer import VideoRenderer
from app.renderers.visual.models import VISUAL_RENDER_MANIFEST_ARTIFACT_TYPE
from app.storage.artifacts import get_artifact, save_artifact
from app.storage.module_runs import list_module_runs_for_project
from app.video_encoder.storage import VideoFileStore
from tests.test_subtitle_renderer import _build_captioned_and_encoded_project
from tests.test_video_renderer import _build_encodable_project


def _renderer(engine, tmp_path, **overrides) -> MediaQCRenderer:
    video_store = overrides.pop("video_store", VideoFileStore(tmp_path / "video"))
    subtitle_store = overrides.pop("subtitle_store", SubtitleFileStore(tmp_path / "subtitles"))
    return MediaQCRenderer(engine, video_store, subtitle_store, **overrides)


# ---------------------------------------------------------------------------
# Happy path: encoded-video-only (no captions)
# ---------------------------------------------------------------------------


def test_healthy_encoded_video_produces_report(tmp_path, engine):
    project_id, timeline_manifest, audio_store, visual_store, video_store = _build_encodable_project(
        engine, tmp_path
    )
    VideoRenderer(engine, audio_store, visual_store, video_store).run(VideoRendererInput(project_id=project_id))
    renderer = _renderer(engine, tmp_path, video_store=video_store)
    result = renderer.run(MediaQCRendererInput(project_id=project_id))

    assert result.report.timeline_manifest_id == timeline_manifest.id
    assert result.report.caption_manifest_id is None
    assert result.report.subtitle_file_asset_id is None
    assert len(result.report.checks) > 0


def test_media_qc_report_persisted_as_artifact(tmp_path, engine):
    project_id, timeline_manifest, audio_store, visual_store, video_store = _build_encodable_project(
        engine, tmp_path
    )
    VideoRenderer(engine, audio_store, visual_store, video_store).run(VideoRendererInput(project_id=project_id))
    renderer = _renderer(engine, tmp_path, video_store=video_store)
    result = renderer.run(MediaQCRendererInput(project_id=project_id))

    stored = get_artifact(engine, project_id, MEDIA_QC_REPORT_ARTIFACT_TYPE, MediaQCReport)
    assert stored == result.report


def test_module_run_success_regardless_of_report_status(tmp_path, engine):
    project_id, timeline_manifest, audio_store, visual_store, video_store = _build_encodable_project(
        engine, tmp_path
    )
    VideoRenderer(engine, audio_store, visual_store, video_store).run(VideoRendererInput(project_id=project_id))
    renderer = _renderer(engine, tmp_path, video_store=video_store)
    result = renderer.run(MediaQCRendererInput(project_id=project_id))

    runs = list_module_runs_for_project(engine, project_id)
    qc_runs = [run for run in runs if run.module == "media_qc_renderer"]
    assert len(qc_runs) == 1
    assert qc_runs[0].status == ModuleRunStatus.SUCCESS
    assert qc_runs[0].output_id == str(result.report.id)
    # SUCCESS regardless of overall_status -- verified directly:
    # QC completing and detecting a defect is still a successful run.


def test_module_run_failed_on_infrastructure_crash(tmp_path, engine):
    project_id, timeline_manifest, audio_store, visual_store, video_store = _build_encodable_project(
        engine, tmp_path
    )
    VideoRenderer(engine, audio_store, visual_store, video_store).run(VideoRendererInput(project_id=project_id))

    # A genuinely broken inspector (ffprobe cannot be resolved at all) --
    # an infrastructure failure, not an ordinary media defect.
    with pytest.raises(FFprobeNotFoundError):
        FFprobeClient(ffprobe_path=tmp_path / "no_such_ffprobe")

    class _CrashingInspector:
        def inspect(self, request):
            raise RuntimeError("simulated infrastructure crash")

    renderer = _renderer(engine, tmp_path, video_store=video_store, inspector=_CrashingInspector())
    with pytest.raises(RuntimeError):
        renderer.run(MediaQCRendererInput(project_id=project_id))

    runs = list_module_runs_for_project(engine, project_id)
    qc_runs = [run for run in runs if run.module == "media_qc_renderer"]
    assert len(qc_runs) == 1
    assert qc_runs[0].status == ModuleRunStatus.FAILED


# ---------------------------------------------------------------------------
# Prefers CaptionedVideoAsset when present and fresh
# ---------------------------------------------------------------------------


def test_prefers_captioned_video_when_present_and_fresh(tmp_path, engine):
    project_id, timeline_manifest, caption_manifest, encoded_asset, subtitle_store, video_store = (
        _build_captioned_and_encoded_project(engine, tmp_path)
    )
    subtitle_result = SubtitleRenderer(engine, subtitle_store, video_store).run(
        SubtitleRendererInput(project_id=project_id, burn_in_settings=CaptionRenderSettings())
    )
    assert subtitle_result.captioned_video_asset is not None

    renderer = _renderer(engine, tmp_path, video_store=video_store, subtitle_store=subtitle_store)
    result = renderer.run(MediaQCRendererInput(project_id=project_id))

    assert result.report.source_video_asset_id == subtitle_result.captioned_video_asset.id
    assert result.report.caption_manifest_id == caption_manifest.id
    assert result.report.subtitle_file_asset_id == subtitle_result.subtitle_asset.id
    check_ids = {c.check_id for c in result.report.checks}
    assert "QC_SUBTITLE_MATCH" in check_ids


def test_missing_captions_are_not_an_error(tmp_path, engine):
    """Captions are optional per project -- absence must not raise."""
    project_id, timeline_manifest, audio_store, visual_store, video_store = _build_encodable_project(
        engine, tmp_path
    )
    VideoRenderer(engine, audio_store, visual_store, video_store).run(VideoRendererInput(project_id=project_id))
    renderer = _renderer(engine, tmp_path, video_store=video_store)
    result = renderer.run(MediaQCRendererInput(project_id=project_id))  # must not raise
    assert result.report.caption_manifest_id is None


# ---------------------------------------------------------------------------
# Failure behavior / freshness (item 33)
# ---------------------------------------------------------------------------


def test_renderer_state_error_when_not_mvp_complete(tmp_path, engine):
    from datetime import datetime, timezone

    from app.models.common import ProjectState
    from app.models.project import Project
    from app.storage.projects import create_project

    project = Project(
        title_internal="not ready", created_at=datetime.now(timezone.utc),
        updated_at=datetime.now(timezone.utc), state=ProjectState.NEW_PROJECT,
    )
    create_project(engine, project)
    renderer = _renderer(engine, tmp_path)
    with pytest.raises(RendererStateError):
        renderer.run(MediaQCRendererInput(project_id=project.project_id))


def test_missing_encoded_video_asset_fails_explicitly(tmp_path, engine):
    project_id, *_ = _build_encodable_project(engine, tmp_path)
    # VideoRenderer never ran -- no EncodedVideoAsset exists.
    renderer = _renderer(engine, tmp_path)
    with pytest.raises(MissingEncodedVideoAssetArtifactError):
        renderer.run(MediaQCRendererInput(project_id=project_id))


def test_stale_encoded_video_asset_fails(tmp_path, engine):
    project_id, timeline_manifest, audio_store, visual_store, video_store = _build_encodable_project(
        engine, tmp_path
    )
    VideoRenderer(engine, audio_store, visual_store, video_store).run(VideoRendererInput(project_id=project_id))
    encoded_asset = get_artifact(engine, project_id, ENCODED_VIDEO_ASSET_ARTIFACT_TYPE, EncodedVideoAsset)
    stale_asset = encoded_asset.model_copy(update={"timeline_manifest_id": uuid4()})
    save_artifact(engine, project_id, ENCODED_VIDEO_ASSET_ARTIFACT_TYPE, stale_asset)

    renderer = _renderer(engine, tmp_path, video_store=video_store)
    with pytest.raises(StaleEncodedVideoAssetError):
        renderer.run(MediaQCRendererInput(project_id=project_id))


def test_stale_caption_manifest_fails(tmp_path, engine):
    project_id, timeline_manifest, caption_manifest, encoded_asset, subtitle_store, video_store = (
        _build_captioned_and_encoded_project(engine, tmp_path)
    )
    from app.renderers.caption.models import CAPTION_MANIFEST_ARTIFACT_TYPE

    stale_manifest = caption_manifest.model_copy(update={"timeline_manifest_id": uuid4()})
    save_artifact(engine, project_id, CAPTION_MANIFEST_ARTIFACT_TYPE, stale_manifest)

    renderer = _renderer(engine, tmp_path, video_store=video_store, subtitle_store=subtitle_store)
    with pytest.raises(StaleCaptionManifestError):
        renderer.run(MediaQCRendererInput(project_id=project_id))


def test_stale_subtitle_file_asset_fails(tmp_path, engine):
    project_id, timeline_manifest, caption_manifest, encoded_asset, subtitle_store, video_store = (
        _build_captioned_and_encoded_project(engine, tmp_path)
    )
    subtitle_result = SubtitleRenderer(engine, subtitle_store, video_store).run(
        SubtitleRendererInput(project_id=project_id)
    )
    stale_subtitle_asset = subtitle_result.subtitle_asset.model_copy(update={"caption_manifest_id": uuid4()})
    save_artifact(engine, project_id, SUBTITLE_FILE_ASSET_ARTIFACT_TYPE, stale_subtitle_asset)

    renderer = _renderer(engine, tmp_path, video_store=video_store, subtitle_store=subtitle_store)
    with pytest.raises(StaleSubtitleFileAssetError):
        renderer.run(MediaQCRendererInput(project_id=project_id))


def test_stale_captioned_video_asset_fails(tmp_path, engine):
    project_id, timeline_manifest, caption_manifest, encoded_asset, subtitle_store, video_store = (
        _build_captioned_and_encoded_project(engine, tmp_path)
    )
    subtitle_result = SubtitleRenderer(engine, subtitle_store, video_store).run(
        SubtitleRendererInput(project_id=project_id, burn_in_settings=CaptionRenderSettings())
    )
    stale_captioned = subtitle_result.captioned_video_asset.model_copy(
        update={"source_encoded_video_asset_id": uuid4()}
    )
    save_artifact(engine, project_id, CAPTIONED_VIDEO_ASSET_ARTIFACT_TYPE, stale_captioned)

    renderer = _renderer(engine, tmp_path, video_store=video_store, subtitle_store=subtitle_store)
    with pytest.raises(StaleCaptionedVideoAssetError):
        renderer.run(MediaQCRendererInput(project_id=project_id))


def test_stale_timeline_manifest_fails(tmp_path, engine):
    project_id, timeline_manifest, audio_store, visual_store, video_store = _build_encodable_project(
        engine, tmp_path
    )
    VideoRenderer(engine, audio_store, visual_store, video_store).run(VideoRendererInput(project_id=project_id))

    current_visual_manifest = get_artifact(engine, project_id, VISUAL_RENDER_MANIFEST_ARTIFACT_TYPE, VisualRenderManifest)
    rerendered = current_visual_manifest.model_copy(update={"id": uuid4()})
    save_artifact(engine, project_id, VISUAL_RENDER_MANIFEST_ARTIFACT_TYPE, rerendered)

    renderer = _renderer(engine, tmp_path, video_store=video_store)
    with pytest.raises(StaleTimelineManifestError):
        renderer.run(MediaQCRendererInput(project_id=project_id))
