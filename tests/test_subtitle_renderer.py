"""Phase 31 focused tests: SubtitleRenderer
(app/renderers/subtitle/renderer.py) -- SRT export (always) and opt-in
local FFmpeg caption burn-in, freshness-checked, ModuleRun-lifecycle.

Reuses tests/test_video_renderer.py's own _build_encodable_project (a
full MVP_COMPLETE project through a real EncodedVideoAsset, via real
ffmpeg) plus tests/test_caption_builder.py's CaptionBuilder to set up a
project with both a CaptionManifest and an EncodedVideoAsset, matching
this renderer's own real-world call order.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from app.captions.models import CaptionRenderSettings
from app.captions.storage import SubtitleFileStore
from app.renderers.caption.builder import CaptionBuilder
from app.renderers.caption.models import CaptionBuilderInput
from app.renderers.subtitle.errors import (
    MissingCaptionManifestArtifactError,
    MissingEncodedVideoAssetArtifactError,
    RendererStateError,
    StaleCaptionManifestError,
    StaleEncodedVideoAssetError,
)
from app.renderers.subtitle.models import (
    CAPTIONED_VIDEO_ASSET_ARTIFACT_TYPE,
    SUBTITLE_FILE_ASSET_ARTIFACT_TYPE,
    SubtitleRendererInput,
)
from app.renderers.subtitle.renderer import SubtitleRenderer
from app.renderers.video.models import ENCODED_VIDEO_ASSET_ARTIFACT_TYPE, VideoRendererInput
from app.renderers.video.renderer import VideoRenderer
from app.storage.artifacts import get_artifact, save_artifact
from app.storage.module_runs import list_module_runs_for_project
from app.video_encoder.storage import VideoFileStore
from tests.test_video_renderer import _build_encodable_project


def _build_captioned_and_encoded_project(engine, tmp_path):
    project_id, timeline_manifest, audio_store, visual_store, video_store = _build_encodable_project(
        engine, tmp_path
    )
    caption_result = CaptionBuilder(engine).run(CaptionBuilderInput(project_id=project_id))
    video_result = VideoRenderer(engine, audio_store, visual_store, video_store).run(
        VideoRendererInput(project_id=project_id)
    )
    subtitle_store = SubtitleFileStore(tmp_path / "subtitles")
    return project_id, timeline_manifest, caption_result.manifest, video_result.asset, subtitle_store, video_store


# ---------------------------------------------------------------------------
# SRT export (always)
# ---------------------------------------------------------------------------


def test_srt_export_produces_subtitle_file_asset(tmp_path, engine):
    project_id, timeline_manifest, caption_manifest, encoded_asset, subtitle_store, video_store = (
        _build_captioned_and_encoded_project(engine, tmp_path)
    )
    renderer = SubtitleRenderer(engine, subtitle_store, video_store)
    result = renderer.run(SubtitleRendererInput(project_id=project_id))

    assert result.subtitle_asset.caption_manifest_id == caption_manifest.id
    assert result.subtitle_asset.cue_count == len(caption_manifest.cues)
    assert result.subtitle_asset.format == "SRT"
    assert result.subtitle_asset.encoding == "utf-8"
    assert result.captioned_video_asset is None  # no burn-in requested

    srt_path = subtitle_store.root / result.subtitle_asset.file_path
    assert srt_path.is_file()
    assert srt_path.stat().st_size == result.subtitle_asset.file_size_bytes
    content = srt_path.read_text(encoding="utf-8")
    assert content.startswith("1\n")
    assert "Đây là câu" in content


def test_srt_export_persisted_as_artifact(tmp_path, engine):
    from app.models.subtitle import SubtitleFileAsset

    project_id, *_rest, subtitle_store, video_store = _build_captioned_and_encoded_project(engine, tmp_path)
    renderer = SubtitleRenderer(engine, subtitle_store, video_store)
    result = renderer.run(SubtitleRendererInput(project_id=project_id))

    stored = get_artifact(engine, project_id, SUBTITLE_FILE_ASSET_ARTIFACT_TYPE, SubtitleFileAsset)
    assert stored == result.subtitle_asset


def test_no_burn_in_when_not_requested_no_ffmpeg_touched(tmp_path, engine, monkeypatch):
    project_id, *_rest, subtitle_store, video_store = _build_captioned_and_encoded_project(engine, tmp_path)

    def _fail_if_called(*args, **kwargs):
        raise AssertionError("ffmpeg should never be invoked when burn_in_settings is None")

    monkeypatch.setattr("subprocess.run", _fail_if_called)
    renderer = SubtitleRenderer(engine, subtitle_store, video_store)
    result = renderer.run(SubtitleRendererInput(project_id=project_id))
    assert result.captioned_video_asset is None


# ---------------------------------------------------------------------------
# Burn-in (opt-in)
# ---------------------------------------------------------------------------


def test_burn_in_when_requested_produces_captioned_video_asset(tmp_path, engine):
    project_id, timeline_manifest, caption_manifest, encoded_asset, subtitle_store, video_store = (
        _build_captioned_and_encoded_project(engine, tmp_path)
    )
    renderer = SubtitleRenderer(engine, subtitle_store, video_store)
    result = renderer.run(
        SubtitleRendererInput(project_id=project_id, burn_in_settings=CaptionRenderSettings())
    )

    assert result.captioned_video_asset is not None
    asset = result.captioned_video_asset
    assert asset.source_encoded_video_asset_id == encoded_asset.id
    assert asset.caption_manifest_id == caption_manifest.id
    assert asset.subtitle_file_asset_id == result.subtitle_asset.id
    assert asset.audio_stream_copied is True
    assert asset.width == encoded_asset.width
    assert asset.height == encoded_asset.height

    output_path = video_store.root / asset.file_path
    assert output_path.is_file()
    assert output_path.stat().st_size == asset.file_size_bytes


def test_burn_in_persisted_as_artifact(tmp_path, engine):
    from app.models.subtitle import CaptionedVideoAsset

    project_id, *_rest, subtitle_store, video_store = _build_captioned_and_encoded_project(engine, tmp_path)
    renderer = SubtitleRenderer(engine, subtitle_store, video_store)
    result = renderer.run(
        SubtitleRendererInput(project_id=project_id, burn_in_settings=CaptionRenderSettings())
    )
    stored = get_artifact(engine, project_id, CAPTIONED_VIDEO_ASSET_ARTIFACT_TYPE, CaptionedVideoAsset)
    assert stored == result.captioned_video_asset


def test_burn_in_requires_encoded_video_asset(tmp_path, engine):
    """Only CaptionBuilder ran (no VideoRenderer yet) -- burn-in must
    fail explicitly rather than silently skip."""
    from tests.test_caption_builder import _build_timeline

    project_id, script_plan, voice_plan, timeline_manifest = _build_timeline(engine, tmp_path)
    CaptionBuilder(engine).run(CaptionBuilderInput(project_id=project_id))

    subtitle_store = SubtitleFileStore(tmp_path / "subtitles")
    video_store = VideoFileStore(tmp_path / "video")
    renderer = SubtitleRenderer(engine, subtitle_store, video_store)
    with pytest.raises(MissingEncodedVideoAssetArtifactError):
        renderer.run(SubtitleRendererInput(project_id=project_id, burn_in_settings=CaptionRenderSettings()))


# ---------------------------------------------------------------------------
# Failure behavior / freshness
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
    subtitle_store = SubtitleFileStore(tmp_path / "subtitles")
    video_store = VideoFileStore(tmp_path / "video")
    renderer = SubtitleRenderer(engine, subtitle_store, video_store)
    with pytest.raises(RendererStateError):
        renderer.run(SubtitleRendererInput(project_id=project.project_id))


def test_missing_caption_manifest_fails_explicitly(tmp_path, engine):
    """VideoRenderer ran, but CaptionBuilder never did."""
    project_id, timeline_manifest, audio_store, visual_store, video_store = _build_encodable_project(
        engine, tmp_path
    )
    VideoRenderer(engine, audio_store, visual_store, video_store).run(
        VideoRendererInput(project_id=project_id)
    )
    subtitle_store = SubtitleFileStore(tmp_path / "subtitles")
    renderer = SubtitleRenderer(engine, subtitle_store, video_store)
    with pytest.raises(MissingCaptionManifestArtifactError):
        renderer.run(SubtitleRendererInput(project_id=project_id))


def test_stale_caption_manifest_fails(tmp_path, engine):
    from uuid import uuid4

    project_id, timeline_manifest, caption_manifest, encoded_asset, subtitle_store, video_store = (
        _build_captioned_and_encoded_project(engine, tmp_path)
    )
    stale_caption_manifest = caption_manifest.model_copy(update={"timeline_manifest_id": uuid4()})
    from app.renderers.caption.models import CAPTION_MANIFEST_ARTIFACT_TYPE

    save_artifact(engine, project_id, CAPTION_MANIFEST_ARTIFACT_TYPE, stale_caption_manifest)

    renderer = SubtitleRenderer(engine, subtitle_store, video_store)
    with pytest.raises(StaleCaptionManifestError):
        renderer.run(SubtitleRendererInput(project_id=project_id))


def test_stale_encoded_video_asset_fails_only_when_burn_in_requested(tmp_path, engine):
    from uuid import uuid4

    project_id, timeline_manifest, caption_manifest, encoded_asset, subtitle_store, video_store = (
        _build_captioned_and_encoded_project(engine, tmp_path)
    )
    stale_encoded_asset = encoded_asset.model_copy(update={"timeline_manifest_id": uuid4()})
    save_artifact(engine, project_id, ENCODED_VIDEO_ASSET_ARTIFACT_TYPE, stale_encoded_asset)

    renderer = SubtitleRenderer(engine, subtitle_store, video_store)
    # SRT-only run never touches EncodedVideoAsset at all -- must succeed.
    result = renderer.run(SubtitleRendererInput(project_id=project_id))
    assert result.captioned_video_asset is None

    # Burn-in requested -- now the staleness must be caught.
    with pytest.raises(StaleEncodedVideoAssetError):
        renderer.run(
            SubtitleRendererInput(project_id=project_id, burn_in_settings=CaptionRenderSettings())
        )


def test_module_run_success(tmp_path, engine):
    from app.models.common import ModuleRunStatus

    project_id, *_rest, subtitle_store, video_store = _build_captioned_and_encoded_project(engine, tmp_path)
    renderer = SubtitleRenderer(engine, subtitle_store, video_store)
    result = renderer.run(SubtitleRendererInput(project_id=project_id))

    runs = list_module_runs_for_project(engine, project_id)
    subtitle_runs = [run for run in runs if run.module == "subtitle_renderer"]
    assert len(subtitle_runs) == 1
    assert subtitle_runs[0].status == ModuleRunStatus.SUCCESS
    assert subtitle_runs[0].output_id == str(result.subtitle_asset.id)


def test_module_run_failed_on_burn_in_error(tmp_path, engine):
    from app.models.common import ModuleRunStatus

    project_id, *_rest, subtitle_store, video_store = _build_captioned_and_encoded_project(engine, tmp_path)
    renderer = SubtitleRenderer(engine, subtitle_store, video_store)

    # Force a burn-in failure by pointing the encoded-video-asset's own
    # file at a nonexistent path -- corrupt the persisted artifact.
    from app.models.video import EncodedVideoAsset

    encoded_asset = get_artifact(engine, project_id, ENCODED_VIDEO_ASSET_ARTIFACT_TYPE, EncodedVideoAsset)
    corrupted = encoded_asset.model_copy(update={"file_path": "does/not/exist.mp4"})
    save_artifact(engine, project_id, ENCODED_VIDEO_ASSET_ARTIFACT_TYPE, corrupted)

    with pytest.raises(Exception):
        renderer.run(SubtitleRendererInput(project_id=project_id, burn_in_settings=CaptionRenderSettings()))

    runs = list_module_runs_for_project(engine, project_id)
    subtitle_runs = [run for run in runs if run.module == "subtitle_renderer"]
    assert len(subtitle_runs) == 1
    assert subtitle_runs[0].status == ModuleRunStatus.FAILED
