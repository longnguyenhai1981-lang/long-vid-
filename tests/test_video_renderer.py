"""Phase 28 focused tests: VideoRenderer
(app/renderers/video/renderer.py) -- the artifact-driven, freshness-
checked, ModuleRun-lifecycle layer that resolves a TimelineManifest into
a VideoEncodeRequest and delegates to the real VideoEncoder.

Reuses tests/test_timeline_builder.py's fixture builders (VoicePlan/
VisualPlan/AssemblyPlan/VoiceRenderManifest/VisualRenderManifest) rather
than re-deriving them -- this file only adds what's new for Phase 28:
building a TimelineManifest on top of that fixture, then running
VideoRenderer against it.
"""

from __future__ import annotations

from datetime import datetime, timezone
from uuid import uuid4

import pytest

from app.audio.storage import AudioFileStore
from app.engines.assembly_plan.models import ASSEMBLY_PLAN_ARTIFACT_TYPE
from app.engines.voice_plan.models import VOICE_PLAN_ARTIFACT_TYPE
from app.engines.visual_plan.models import VISUAL_PLAN_ARTIFACT_TYPE
from app.models.assembly import AssemblySegment
from app.models.common import ModuleRunStatus
from app.models.audio import RenderedVoiceTake, VoiceRenderManifest
from app.models.visual_render import RenderedVisualAsset, VisualRenderManifest
from app.renderers.timeline.builder import TimelineBuilder
from app.renderers.timeline.models import TIMELINE_MANIFEST_ARTIFACT_TYPE, TimelineBuilderInput
from app.renderers.video.errors import (
    RendererStateError,
    StaleTimelineManifestError,
    UnrenderedVisualSegmentError,
)
from app.renderers.video.models import ENCODED_VIDEO_ASSET_ARTIFACT_TYPE, VideoRendererInput
from app.renderers.video.renderer import VideoRenderer
from app.storage.artifacts import get_artifact, save_artifact
from app.storage.module_runs import list_module_runs_for_project
from app.video_encoder.errors import InvalidCrossfadeDurationError
from app.video_encoder.storage import VideoFileStore
from app.visual.storage import VisualFileStore
from tests.test_timeline_builder import (
    _assembly_plan,
    _visual_plan,
    _voice_plan,
    _voice_render_manifest,
    _write_png,
    _write_wav,
)
from tests.test_visual_renderer import _create_project_at_mvp_complete

_NO_CROSSFADE_SEGMENTS = [
    AssemblySegment(
        segment_id="S1", script_line_ids=["L001", "L002"], voice_chunk_ids=["C001"],
        visual_beat_id="V1", start_seconds=0.0, end_seconds=4.0,
        music_state="BED", transition_in="NONE", transition_out="CUT",
    ),
    AssemblySegment(
        segment_id="S2", script_line_ids=["L003", "L004"], voice_chunk_ids=["C002"],
        visual_beat_id="V2", start_seconds=4.0, end_seconds=8.0,
        music_state="DUCK", transition_in="CUT", transition_out="NONE",
    ),
]

_CROSSFADE_SEGMENTS = [
    # transition_OUT is the sole field this encoder consults to decide a
    # join (Phase 29) -- S1's own transition_out="DISSOLVE" (-> CROSSFADE)
    # is what actually triggers a real dissolve into S2; S2's
    # transition_in is never consulted.
    AssemblySegment(
        segment_id="S1", script_line_ids=["L001", "L002"], voice_chunk_ids=["C001"],
        visual_beat_id="V1", start_seconds=0.0, end_seconds=4.0,
        music_state="BED", transition_in="NONE", transition_out="DISSOLVE",
    ),
    AssemblySegment(
        segment_id="S2", script_line_ids=["L003", "L004"], voice_chunk_ids=["C002"],
        visual_beat_id="V2", start_seconds=4.0, end_seconds=8.0,
        music_state="DUCK", transition_in="NONE", transition_out="NONE",
    ),
]

_CROSSFADE_TOO_SHORT_SEGMENTS = [
    # S1 is only 200ms long -- shorter than VideoEncodingSettings' own
    # default crossfade_duration_ms=300, so VideoEncoder must reject this
    # with InvalidCrossfadeDurationError rather than silently shortening
    # the fade to fit.
    AssemblySegment(
        segment_id="S1", script_line_ids=["L001", "L002"], voice_chunk_ids=["C001"],
        visual_beat_id="V1", start_seconds=0.0, end_seconds=0.2,
        music_state="BED", transition_in="NONE", transition_out="DISSOLVE",
    ),
    AssemblySegment(
        segment_id="S2", script_line_ids=["L003", "L004"], voice_chunk_ids=["C002"],
        visual_beat_id="V2", start_seconds=0.2, end_seconds=4.2,
        music_state="DUCK", transition_in="NONE", transition_out="NONE",
    ),
]


def _visual_render_manifest_all_rendered(script_plan_id, voice_plan_id, visual_plan_id, visual_store) -> VisualRenderManifest:
    """Unlike tests/test_timeline_builder.py's own _visual_render_manifest
    (which leaves V2 as an unrendered ASSET_REUSE requirement, exactly
    right for Phase 27's own tests), Phase 28 needs a fully video-
    encodable manifest for its happy-path tests -- both V1 and V2 are
    real rendered PNGs here."""
    _write_png(visual_store.root / "V1_R1.png")
    _write_png(visual_store.root / "V2_R1.png", color=(0, 200, 0))
    assets = [
        RenderedVisualAsset(render_job_id="V1_R1", beat_id="V1", media_type="GENERATED_STILL", file_path="V1_R1.png", width=64, height=64),
        RenderedVisualAsset(render_job_id="V2_R1", beat_id="V2", media_type="GENERATED_STILL", file_path="V2_R1.png", width=64, height=64),
    ]
    return VisualRenderManifest(
        script_plan_id=script_plan_id, voice_plan_id=voice_plan_id, visual_plan_id=visual_plan_id,
        provider="fake-visual", output_format="PNG", assets=assets, requirements=[],
        created_at=datetime.now(timezone.utc),
    )


def _short_first_chunk_voice_render_manifest(script_plan_id, voice_plan_id, audio_store) -> VoiceRenderManifest:
    """C001 (S1's narration) is only 100ms of real audio -- short enough to
    fit inside a 200ms S1 while still being long enough to prove the
    InvalidCrossfadeDurationError below is a crossfade-duration failure,
    not a narration-overflow one."""
    _write_wav(audio_store.root / "C001_T1.wav", duration_seconds=0.1)
    take1 = RenderedVoiceTake(
        render_job_id="C001_T1", chunk_id="C001", take_number=1, line_ids=["L001", "L002"],
        file_path="C001_T1.wav", duration_seconds=0.1, music_state="BED", sfx_opportunity="whoosh",
    )
    _write_wav(audio_store.root / "C002_T1.wav", duration_seconds=2.0)
    take2 = RenderedVoiceTake(
        render_job_id="C002_T1", chunk_id="C002", take_number=1, line_ids=["L003", "L004"],
        file_path="C002_T1.wav", duration_seconds=2.0, music_state="DUCK",
    )
    return VoiceRenderManifest(
        script_plan_id=script_plan_id, voice_plan_id=voice_plan_id, provider="fake-tts",
        voice_id="v1", output_format="WAV", renders=[take1, take2],
        created_at=datetime.now(timezone.utc),
    )


def _build_encodable_project(engine, tmp_path):
    """A full MVP_COMPLETE project with every upstream artifact through
    TimelineManifest already saved, fresh, and fully video-encodable (no
    CROSSFADE transitions, every visual actually rendered)."""
    project_id, script_plan = _create_project_at_mvp_complete(engine)

    voice_plan = _voice_plan(script_plan.id)
    save_artifact(engine, project_id, VOICE_PLAN_ARTIFACT_TYPE, voice_plan)

    visual_plan = _visual_plan(script_plan.id, voice_plan.id)
    save_artifact(engine, project_id, VISUAL_PLAN_ARTIFACT_TYPE, visual_plan)

    assembly_plan = _assembly_plan(
        script_plan.id, voice_plan.id, visual_plan.id, segments=_NO_CROSSFADE_SEGMENTS
    )
    save_artifact(engine, project_id, ASSEMBLY_PLAN_ARTIFACT_TYPE, assembly_plan)

    audio_store = AudioFileStore(tmp_path / "audio")
    voice_render_manifest = _voice_render_manifest(script_plan.id, voice_plan.id, audio_store)
    save_artifact(engine, project_id, "voice_render_manifest", voice_render_manifest)

    visual_store = VisualFileStore(tmp_path / "visuals")
    visual_render_manifest = _visual_render_manifest_all_rendered(
        script_plan.id, voice_plan.id, visual_plan.id, visual_store
    )
    save_artifact(engine, project_id, "visual_render_manifest", visual_render_manifest)

    timeline_builder = TimelineBuilder(engine, audio_store, visual_store)
    timeline_result = timeline_builder.run(TimelineBuilderInput(project_id=project_id))

    video_store = VideoFileStore(tmp_path / "video")
    return project_id, timeline_result.manifest, audio_store, visual_store, video_store


# ---------------------------------------------------------------------------
# Happy path
# ---------------------------------------------------------------------------


def test_video_renderer_produces_encoded_video_asset(tmp_path, engine):
    project_id, timeline_manifest, audio_store, visual_store, video_store = _build_encodable_project(
        engine, tmp_path
    )
    renderer = VideoRenderer(engine, audio_store, visual_store, video_store)

    result = renderer.run(VideoRendererInput(project_id=project_id))

    assert result.asset.timeline_manifest_id == timeline_manifest.id
    assert result.asset.duration_ms == timeline_manifest.total_duration_ms
    output_file = video_store.root / result.asset.file_path
    assert output_file.is_file()
    assert output_file.stat().st_size > 0


def test_audio_bindings_none_ignores_manifest_music_cues(tmp_path, engine):
    """Phase 30: _NO_CROSSFADE_SEGMENTS already gives every real
    TimelineManifest a MUSIC_BED_START/MUSIC_DUCK/MUSIC_BED_END cue
    sequence (MusicState has no "none" member) -- proving that omitting
    audio_bindings makes VideoRenderer ignore them entirely, exactly like
    Phase 29, is the real regression guard here."""
    project_id, timeline_manifest, audio_store, visual_store, video_store = _build_encodable_project(
        engine, tmp_path
    )
    renderer = VideoRenderer(engine, audio_store, visual_store, video_store)
    result = renderer.run(VideoRendererInput(project_id=project_id))
    assert result.asset.has_music is False
    assert result.asset.sfx_event_count == 0
    assert result.asset.music_cue_count == 0


def test_audio_bindings_provided_engages_real_music_mixing(tmp_path, engine):
    """_NO_CROSSFADE_SEGMENTS's own C001 voice take also carries
    sfx_opportunity="whoosh" (Phase 27's own fixture), so a real
    TimelineManifest built from it always has one SFX_TRIGGER cue too --
    bound here alongside the music bed so the encode succeeds end to
    end."""
    from app.video_encoder.models import AudioAssetBindings

    project_id, timeline_manifest, audio_store, visual_store, video_store = _build_encodable_project(
        engine, tmp_path
    )
    music_path = tmp_path / "music.wav"
    whoosh_path = tmp_path / "whoosh.wav"
    from tests.test_timeline_builder import _write_wav as _write_wav_music

    _write_wav_music(music_path, duration_seconds=1.0)
    _write_wav_music(whoosh_path, duration_seconds=0.2)

    renderer = VideoRenderer(engine, audio_store, visual_store, video_store)
    result = renderer.run(
        VideoRendererInput(
            project_id=project_id,
            audio_bindings=AudioAssetBindings(
                music_bed_path=music_path, sfx_by_id={"whoosh": whoosh_path}
            ),
        )
    )
    assert result.asset.has_music is True
    assert result.asset.sfx_event_count == 1
    assert result.asset.music_cue_count >= 2  # at least BED_START and BED_END


def test_audio_bindings_provided_but_incomplete_fails_explicitly(tmp_path, engine):
    from app.video_encoder.errors import UnknownSFXReferenceError
    from app.video_encoder.models import AudioAssetBindings

    project_id, timeline_manifest, audio_store, visual_store, video_store = _build_encodable_project(
        engine, tmp_path
    )
    renderer = VideoRenderer(engine, audio_store, visual_store, video_store)
    # An entirely empty AudioAssetBindings still fails explicitly -- here
    # via the manifest's own unbound "whoosh" SFX_TRIGGER cue -- never a
    # silent skip just because the caller opted in with nothing bound.
    with pytest.raises(UnknownSFXReferenceError):
        renderer.run(VideoRendererInput(project_id=project_id, audio_bindings=AudioAssetBindings()))


def test_encoded_video_asset_persisted_as_artifact(tmp_path, engine):
    project_id, timeline_manifest, audio_store, visual_store, video_store = _build_encodable_project(
        engine, tmp_path
    )
    renderer = VideoRenderer(engine, audio_store, visual_store, video_store)
    result = renderer.run(VideoRendererInput(project_id=project_id))

    from app.models.video import EncodedVideoAsset

    stored = get_artifact(engine, project_id, ENCODED_VIDEO_ASSET_ARTIFACT_TYPE, EncodedVideoAsset)
    assert stored == result.asset


# ---------------------------------------------------------------------------
# Failure behavior
# ---------------------------------------------------------------------------


def test_unrendered_visual_segment_fails_explicitly(tmp_path, engine):
    """Reusing test_timeline_builder's own _visual_render_manifest (which
    leaves V2 as an unrendered ASSET_REUSE requirement) proves
    VideoRenderer refuses to encode a segment with no real image."""
    from tests.test_timeline_builder import _visual_render_manifest as _partial_visual_render_manifest

    project_id, script_plan = _create_project_at_mvp_complete(engine)
    voice_plan = _voice_plan(script_plan.id)
    save_artifact(engine, project_id, VOICE_PLAN_ARTIFACT_TYPE, voice_plan)
    visual_plan = _visual_plan(script_plan.id, voice_plan.id)
    save_artifact(engine, project_id, VISUAL_PLAN_ARTIFACT_TYPE, visual_plan)
    assembly_plan = _assembly_plan(
        script_plan.id, voice_plan.id, visual_plan.id, segments=_NO_CROSSFADE_SEGMENTS
    )
    save_artifact(engine, project_id, ASSEMBLY_PLAN_ARTIFACT_TYPE, assembly_plan)

    audio_store = AudioFileStore(tmp_path / "audio")
    voice_render_manifest = _voice_render_manifest(script_plan.id, voice_plan.id, audio_store)
    save_artifact(engine, project_id, "voice_render_manifest", voice_render_manifest)

    visual_store = VisualFileStore(tmp_path / "visuals")
    visual_render_manifest = _partial_visual_render_manifest(
        script_plan.id, voice_plan.id, visual_plan.id, visual_store
    )
    save_artifact(engine, project_id, "visual_render_manifest", visual_render_manifest)

    timeline_builder = TimelineBuilder(engine, audio_store, visual_store)
    timeline_builder.run(TimelineBuilderInput(project_id=project_id))

    video_store = VideoFileStore(tmp_path / "video")
    renderer = VideoRenderer(engine, audio_store, visual_store, video_store)
    with pytest.raises(UnrenderedVisualSegmentError):
        renderer.run(VideoRendererInput(project_id=project_id))


def test_crossfade_transition_encodes_successfully(tmp_path, engine):
    """Phase 29: CROSSFADE is executed for real, not rejected -- an
    authored DISSOLVE (-> CROSSFADE) transition_out produces a
    successfully encoded video with a real crossfade join."""
    project_id, script_plan = _create_project_at_mvp_complete(engine)
    voice_plan = _voice_plan(script_plan.id)
    save_artifact(engine, project_id, VOICE_PLAN_ARTIFACT_TYPE, voice_plan)
    visual_plan = _visual_plan(script_plan.id, voice_plan.id)
    save_artifact(engine, project_id, VISUAL_PLAN_ARTIFACT_TYPE, visual_plan)
    assembly_plan = _assembly_plan(
        script_plan.id, voice_plan.id, visual_plan.id, segments=_CROSSFADE_SEGMENTS
    )
    save_artifact(engine, project_id, ASSEMBLY_PLAN_ARTIFACT_TYPE, assembly_plan)

    audio_store = AudioFileStore(tmp_path / "audio")
    voice_render_manifest = _voice_render_manifest(script_plan.id, voice_plan.id, audio_store)
    save_artifact(engine, project_id, "voice_render_manifest", voice_render_manifest)

    visual_store = VisualFileStore(tmp_path / "visuals")
    visual_render_manifest = _visual_render_manifest_all_rendered(
        script_plan.id, voice_plan.id, visual_plan.id, visual_store
    )
    save_artifact(engine, project_id, "visual_render_manifest", visual_render_manifest)

    timeline_builder = TimelineBuilder(engine, audio_store, visual_store)
    timeline_result = timeline_builder.run(TimelineBuilderInput(project_id=project_id))

    video_store = VideoFileStore(tmp_path / "video")
    renderer = VideoRenderer(engine, audio_store, visual_store, video_store)
    result = renderer.run(VideoRendererInput(project_id=project_id))

    assert result.asset.duration_ms == timeline_result.manifest.total_duration_ms
    assert result.asset.crossfade_count == 1
    output_file = video_store.root / result.asset.file_path
    assert output_file.is_file()
    assert output_file.stat().st_size > 0


def test_renderer_state_error_when_not_mvp_complete(tmp_path, engine):
    from app.models.common import ProjectState
    from app.models.project import Project
    from app.storage.projects import create_project

    project = Project(
        title_internal="not ready", created_at=datetime.now(timezone.utc),
        updated_at=datetime.now(timezone.utc), state=ProjectState.NEW_PROJECT,
    )
    create_project(engine, project)

    video_store = VideoFileStore(tmp_path / "video")
    renderer = VideoRenderer(engine, AudioFileStore(tmp_path / "audio"), VisualFileStore(tmp_path / "visuals"), video_store)
    with pytest.raises(RendererStateError):
        renderer.run(VideoRendererInput(project_id=project.project_id))


def test_stale_timeline_manifest_when_visual_rerendered(tmp_path, engine):
    project_id, timeline_manifest, audio_store, visual_store, video_store = _build_encodable_project(
        engine, tmp_path
    )
    # Re-save a "new" VisualRenderManifest (different id) without rebuilding
    # the timeline -- the timeline is now stale relative to it.
    stale_manifest = timeline_manifest  # keep for reference
    from app.storage.artifacts import get_artifact as _get

    current_visual_manifest = _get(engine, project_id, "visual_render_manifest", VisualRenderManifest)
    rerendered = current_visual_manifest.model_copy(update={"id": uuid4()})
    save_artifact(engine, project_id, "visual_render_manifest", rerendered)

    renderer = VideoRenderer(engine, audio_store, visual_store, video_store)
    with pytest.raises(StaleTimelineManifestError):
        renderer.run(VideoRendererInput(project_id=project_id))


def test_missing_timeline_manifest_fails_explicitly(tmp_path, engine):
    from app.renderers.video.errors import MissingTimelineManifestArtifactError

    project_id, script_plan = _create_project_at_mvp_complete(engine)
    voice_plan = _voice_plan(script_plan.id)
    save_artifact(engine, project_id, VOICE_PLAN_ARTIFACT_TYPE, voice_plan)
    visual_plan = _visual_plan(script_plan.id, voice_plan.id)
    save_artifact(engine, project_id, VISUAL_PLAN_ARTIFACT_TYPE, visual_plan)
    assembly_plan = _assembly_plan(script_plan.id, voice_plan.id, visual_plan.id, segments=_NO_CROSSFADE_SEGMENTS)
    save_artifact(engine, project_id, ASSEMBLY_PLAN_ARTIFACT_TYPE, assembly_plan)
    # No TimelineManifest ever built.

    video_store = VideoFileStore(tmp_path / "video")
    renderer = VideoRenderer(engine, AudioFileStore(tmp_path / "audio"), VisualFileStore(tmp_path / "visuals"), video_store)
    with pytest.raises(MissingTimelineManifestArtifactError):
        renderer.run(VideoRendererInput(project_id=project_id))


# ---------------------------------------------------------------------------
# Lifecycle
# ---------------------------------------------------------------------------


def test_module_run_success(tmp_path, engine):
    project_id, timeline_manifest, audio_store, visual_store, video_store = _build_encodable_project(
        engine, tmp_path
    )
    renderer = VideoRenderer(engine, audio_store, visual_store, video_store)
    result = renderer.run(VideoRendererInput(project_id=project_id))

    runs = list_module_runs_for_project(engine, project_id)
    video_runs = [run for run in runs if run.module == "video_renderer"]
    assert len(video_runs) == 1
    assert video_runs[0].status == ModuleRunStatus.SUCCESS
    assert video_runs[0].output_id == str(result.asset.id)


def test_module_run_failed_on_encoder_error(tmp_path, engine):
    """S1 is only 200ms -- shorter than the encoder's default
    crossfade_duration_ms=300 -- so a DISSOLVE (-> CROSSFADE)
    transition_out on it must fail with InvalidCrossfadeDurationError
    (Phase 29), recorded as a FAILED ModuleRun exactly like any other
    encoder-level rejection."""
    project_id, script_plan = _create_project_at_mvp_complete(engine)
    voice_plan = _voice_plan(script_plan.id)
    save_artifact(engine, project_id, VOICE_PLAN_ARTIFACT_TYPE, voice_plan)
    visual_plan = _visual_plan(script_plan.id, voice_plan.id)
    save_artifact(engine, project_id, VISUAL_PLAN_ARTIFACT_TYPE, visual_plan)
    assembly_plan = _assembly_plan(
        script_plan.id, voice_plan.id, visual_plan.id, segments=_CROSSFADE_TOO_SHORT_SEGMENTS
    )
    save_artifact(engine, project_id, ASSEMBLY_PLAN_ARTIFACT_TYPE, assembly_plan)

    audio_store = AudioFileStore(tmp_path / "audio")
    voice_render_manifest = _short_first_chunk_voice_render_manifest(script_plan.id, voice_plan.id, audio_store)
    save_artifact(engine, project_id, "voice_render_manifest", voice_render_manifest)
    visual_store = VisualFileStore(tmp_path / "visuals")
    visual_render_manifest = _visual_render_manifest_all_rendered(
        script_plan.id, voice_plan.id, visual_plan.id, visual_store
    )
    save_artifact(engine, project_id, "visual_render_manifest", visual_render_manifest)

    timeline_builder = TimelineBuilder(engine, audio_store, visual_store)
    timeline_builder.run(TimelineBuilderInput(project_id=project_id))

    video_store = VideoFileStore(tmp_path / "video")
    renderer = VideoRenderer(engine, audio_store, visual_store, video_store)
    with pytest.raises(InvalidCrossfadeDurationError):
        renderer.run(VideoRendererInput(project_id=project_id))

    runs = list_module_runs_for_project(engine, project_id)
    video_runs = [run for run in runs if run.module == "video_renderer"]
    assert len(video_runs) == 1
    assert video_runs[0].status == ModuleRunStatus.FAILED


def test_no_project_state_transition(tmp_path, engine):
    from app.storage.projects import get_project

    project_id, timeline_manifest, audio_store, visual_store, video_store = _build_encodable_project(
        engine, tmp_path
    )
    renderer = VideoRenderer(engine, audio_store, visual_store, video_store)
    renderer.run(VideoRendererInput(project_id=project_id))
    assert get_project(engine, project_id).state.value == "MVP_COMPLETE"


# ---------------------------------------------------------------------------
# Determinism
# ---------------------------------------------------------------------------


def test_deterministic_rerun_business_timing(tmp_path, engine):
    project_id, timeline_manifest, audio_store, visual_store, video_store = _build_encodable_project(
        engine, tmp_path
    )
    renderer = VideoRenderer(engine, audio_store, visual_store, video_store)

    first = renderer.run(VideoRendererInput(project_id=project_id))
    second = renderer.run(VideoRendererInput(project_id=project_id))

    assert first.asset.duration_ms == second.asset.duration_ms
    assert first.asset.width == second.asset.width
    assert first.asset.height == second.asset.height
    assert first.asset.video_codec == second.asset.video_codec
    assert first.asset.audio_codec == second.asset.audio_codec
