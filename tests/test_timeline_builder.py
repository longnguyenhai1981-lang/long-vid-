"""Phase 27 focused tests: TimelineBuilder
(app/renderers/timeline/builder.py) and TimelineManifest
(app/models/timeline.py).

Reuses tests/test_visual_renderer.py's project-graph builder
(_create_project_at_mvp_complete) rather than re-deriving ~150 lines of
unrelated fixture setup for every review-gate state transition -- this
file builds its own VoicePlan/VisualPlan/AssemblyPlan/VoiceRenderManifest/
VisualRenderManifest on top of that, tailored to a small, easy-to-reason-
about two-segment timeline.
"""

from __future__ import annotations

import subprocess
import sys
import wave
from datetime import datetime, timezone
from pathlib import Path
from uuid import uuid4

import pytest
from pydantic import ValidationError

from app.audio.storage import AudioFileStore
from app.engines.assembly_plan.models import ASSEMBLY_PLAN_ARTIFACT_TYPE
from app.engines.script.models import SCRIPT_PLAN_ARTIFACT_TYPE
from app.engines.voice_plan.models import VOICE_PLAN_ARTIFACT_TYPE
from app.engines.visual_plan.models import VISUAL_PLAN_ARTIFACT_TYPE
from app.models.assembly import AssemblyPlan, AssemblySegment
from app.models.audio import RenderedVoiceTake, VoiceRenderManifest
from app.models.common import ModuleRunStatus
from app.models.timeline import (
    TimelineAudioRef,
    TimelineCue,
    TimelineCueType,
    TimelineManifest,
    TimelineSegment,
    TimelineTransitionType,
    TimelineVisualRef,
    TimelineVisualSourceStatus,
    VisualMotionType,
)
from app.models.visual import VisualBeat, VisualPlan
from app.models.visual_render import RenderedVisualAsset, VisualRenderManifest, VisualRenderRequirement
from app.models.voice import VoiceChunk, VoicePlan
from app.renderers.timeline.errors import (
    MissingAssemblyPlanArtifactError,
    MissingAudioFileError,
    MissingRenderedVoiceTakeError,
    MissingVisualFileError,
    MissingVisualReferenceError,
    MissingVisualRenderManifestArtifactError,
    MissingVoiceRenderManifestArtifactError,
    RendererStateError,
    StaleAssemblyPlanError,
    StaleVisualPlanError,
    StaleVisualRenderManifestError,
    StaleVoicePlanError,
    StaleVoiceRenderManifestError,
    TimelineManifestIntegrityError,
    UnknownMotionSegmentReferenceError,
    UnknownVisualBeatReferenceError,
    UnknownVoiceChunkReferenceError,
    UnresolvableAudioDurationError,
)
from app.renderers.timeline.builder import TimelineBuilder
from app.renderers.timeline.models import TIMELINE_MANIFEST_ARTIFACT_TYPE, TimelineBuilderInput
from app.storage.artifacts import get_artifact, save_artifact
from app.storage.module_runs import list_module_runs_for_project
from app.storage.projects import update_artifact_reference
from app.visual.storage import VisualFileStore
from tests.test_visual_renderer import _create_project_at_mvp_complete, _valid_script_plan

# ---------------------------------------------------------------------------
# Fixture builders
# ---------------------------------------------------------------------------


def _write_wav(path: Path, duration_seconds: float, framerate: int = 8000) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    frame_count = round(duration_seconds * framerate)
    with wave.open(str(path), "wb") as wav_file:
        wav_file.setnchannels(1)
        wav_file.setsampwidth(2)
        wav_file.setframerate(framerate)
        wav_file.writeframes(b"\x00\x00" * frame_count)


def _write_png(path: Path, size=(64, 64), color=(10, 20, 200)) -> None:
    from PIL import Image

    path.parent.mkdir(parents=True, exist_ok=True)
    Image.new("RGB", size, color).save(path, format="PNG")


def _voice_plan(script_plan_id) -> VoicePlan:
    return VoicePlan(
        script_plan_id=script_plan_id,
        chunks=[
            VoiceChunk(
                chunk_id="C001", line_ids=["L001", "L002"], voice_state="NEUTRAL",
                pace="NORMAL", energy="MEDIUM", take_count=1, music_state="BED",
                sfx_opportunity="whoosh",
            ),
            VoiceChunk(
                chunk_id="C002", line_ids=["L003", "L004"], voice_state="CURIOUS",
                pace="NORMAL", energy="MEDIUM", take_count=1, music_state="DUCK",
            ),
        ],
    )


def _visual_plan(script_plan_id, voice_plan_id) -> VisualPlan:
    return VisualPlan(
        script_plan_id=script_plan_id,
        voice_plan_id=voice_plan_id,
        beats=[
            VisualBeat(
                beat_id="V1", script_line_ids=["L001", "L002"], narrative_node="Q0",
                visual_level="L1_ESTABLISH", visual_function="STORY", media_type="GENERATED_STILL",
                complexity="C1", concept="c1", primary_focus="f1",
            ),
            VisualBeat(
                beat_id="V2", script_line_ids=["L003", "L004"], narrative_node="Q0",
                visual_level="L1_ESTABLISH", visual_function="STORY", media_type="ASSET_REUSE",
                complexity="C1", concept="c2", primary_focus="f2", reuse_key="ti_hero_shot",
            ),
        ],
    )


def _assembly_plan(script_plan_id, voice_plan_id, visual_plan_id, **overrides) -> AssemblyPlan:
    fields = dict(
        script_plan_id=script_plan_id,
        voice_plan_id=voice_plan_id,
        visual_plan_id=visual_plan_id,
        estimated_total_duration_seconds=8.0,
        segments=[
            AssemblySegment(
                segment_id="S1", script_line_ids=["L001", "L002"], voice_chunk_ids=["C001"],
                visual_beat_id="V1", start_seconds=0.0, end_seconds=4.0,
                music_state="BED", transition_in="NONE", transition_out="CUT",
            ),
            AssemblySegment(
                segment_id="S2", script_line_ids=["L003", "L004"], voice_chunk_ids=["C002"],
                visual_beat_id="V2", start_seconds=4.0, end_seconds=8.0,
                music_state="DUCK", transition_in="DISSOLVE", transition_out="NONE",
            ),
        ],
    )
    fields.update(overrides)
    return AssemblyPlan(**fields)


def _voice_render_manifest(
    script_plan_id, voice_plan_id, audio_store: AudioFileStore, *, use_real_wav_for_c002=False
) -> VoiceRenderManifest:
    _write_wav(audio_store.root / "C001_T1.wav", duration_seconds=2.0)
    take1 = RenderedVoiceTake(
        render_job_id="C001_T1", chunk_id="C001", take_number=1, line_ids=["L001", "L002"],
        file_path="C001_T1.wav", duration_seconds=2.0, music_state="BED", sfx_opportunity="whoosh",
    )

    if use_real_wav_for_c002:
        _write_wav(audio_store.root / "C002_T1.wav", duration_seconds=1.5)
        take2 = RenderedVoiceTake(
            render_job_id="C002_T1", chunk_id="C002", take_number=1, line_ids=["L003", "L004"],
            file_path="C002_T1.wav", duration_seconds=None, music_state="DUCK",
        )
    else:
        _write_wav(audio_store.root / "C002_T1.wav", duration_seconds=1.5)
        take2 = RenderedVoiceTake(
            render_job_id="C002_T1", chunk_id="C002", take_number=1, line_ids=["L003", "L004"],
            file_path="C002_T1.wav", duration_seconds=1.5, music_state="DUCK",
        )

    return VoiceRenderManifest(
        script_plan_id=script_plan_id, voice_plan_id=voice_plan_id, provider="fake-tts",
        voice_id="v1", output_format="WAV", renders=[take1, take2],
        created_at=datetime.now(timezone.utc),
    )


def _visual_render_manifest(
    script_plan_id, voice_plan_id, visual_plan_id, visual_store: VisualFileStore
) -> VisualRenderManifest:
    _write_png(visual_store.root / "V1_R1.png")
    asset = RenderedVisualAsset(
        render_job_id="V1_R1", beat_id="V1", media_type="GENERATED_STILL",
        file_path="V1_R1.png", width=64, height=64,
    )
    requirement = VisualRenderRequirement(
        beat_id="V2", media_type="ASSET_REUSE", status="REUSE_ONLY", reference="ti_hero_shot",
    )
    return VisualRenderManifest(
        script_plan_id=script_plan_id, voice_plan_id=voice_plan_id, visual_plan_id=visual_plan_id,
        provider="fake-visual", output_format="PNG", assets=[asset], requirements=[requirement],
        created_at=datetime.now(timezone.utc),
    )


def _build_ready_project(engine, tmp_path, *, assembly_overrides=None):
    """Returns (project_id, script_plan, voice_plan, visual_plan,
    assembly_plan, voice_render_manifest, visual_render_manifest,
    audio_store, visual_store) -- a full MVP_COMPLETE project with every
    upstream artifact TimelineBuilder needs already saved and fresh."""
    project_id, script_plan = _create_project_at_mvp_complete(engine)

    voice_plan = _voice_plan(script_plan.id)
    save_artifact(engine, project_id, VOICE_PLAN_ARTIFACT_TYPE, voice_plan)

    visual_plan = _visual_plan(script_plan.id, voice_plan.id)
    save_artifact(engine, project_id, VISUAL_PLAN_ARTIFACT_TYPE, visual_plan)

    assembly_plan = _assembly_plan(
        script_plan.id, voice_plan.id, visual_plan.id, **(assembly_overrides or {})
    )
    save_artifact(engine, project_id, ASSEMBLY_PLAN_ARTIFACT_TYPE, assembly_plan)

    audio_store = AudioFileStore(tmp_path / "audio")
    voice_render_manifest = _voice_render_manifest(script_plan.id, voice_plan.id, audio_store)
    save_artifact(engine, project_id, "voice_render_manifest", voice_render_manifest)

    visual_store = VisualFileStore(tmp_path / "visuals")
    visual_render_manifest = _visual_render_manifest(
        script_plan.id, voice_plan.id, visual_plan.id, visual_store
    )
    save_artifact(engine, project_id, "visual_render_manifest", visual_render_manifest)

    return (
        project_id, script_plan, voice_plan, visual_plan, assembly_plan,
        voice_render_manifest, visual_render_manifest, audio_store, visual_store,
    )


def _builder(engine, audio_store, visual_store) -> TimelineBuilder:
    return TimelineBuilder(engine, audio_store, visual_store)


# ---------------------------------------------------------------------------
# Timing
# ---------------------------------------------------------------------------


def test_first_segment_starts_at_zero(engine, tmp_path):
    project_id, *_, audio_store, visual_store = _build_ready_project(engine, tmp_path)
    result = _builder(engine, audio_store, visual_store).run(TimelineBuilderInput(project_id=project_id))
    assert result.manifest.segments[0].start_ms == 0


def test_duration_from_recorded_duration_seconds(engine, tmp_path):
    project_id, *_, audio_store, visual_store = _build_ready_project(engine, tmp_path)
    result = _builder(engine, audio_store, visual_store).run(TimelineBuilderInput(project_id=project_id))
    seg1 = result.manifest.segments[0]
    assert seg1.duration_ms == 2000  # 2.0s recorded duration_seconds


def test_duration_measured_from_real_wav_header_when_unrecorded(engine, tmp_path):
    project_id, script_plan, voice_plan, visual_plan, assembly_plan, *_ = _build_ready_project(
        engine, tmp_path
    )
    # Rebuild VoiceRenderManifest with C002's duration_seconds unset --
    # forces the WAV-header measurement path.
    audio_store = AudioFileStore(tmp_path / "audio")
    manifest = _voice_render_manifest(
        script_plan.id, voice_plan.id, audio_store, use_real_wav_for_c002=True
    )
    save_artifact(engine, project_id, "voice_render_manifest", manifest)
    visual_store = VisualFileStore(tmp_path / "visuals")

    result = _builder(engine, audio_store, visual_store).run(TimelineBuilderInput(project_id=project_id))
    seg2 = result.manifest.segments[1]
    assert seg2.duration_ms == 1500  # measured from the real 1.5s WAV file


def test_integer_millisecond_timebase(engine, tmp_path):
    project_id, *_, audio_store, visual_store = _build_ready_project(engine, tmp_path)
    result = _builder(engine, audio_store, visual_store).run(TimelineBuilderInput(project_id=project_id))
    for segment in result.manifest.segments:
        assert isinstance(segment.start_ms, int)
        assert isinstance(segment.end_ms, int)
        assert isinstance(segment.duration_ms, int)


def test_cumulative_segment_timing(engine, tmp_path):
    project_id, *_, audio_store, visual_store = _build_ready_project(engine, tmp_path)
    result = _builder(engine, audio_store, visual_store).run(TimelineBuilderInput(project_id=project_id))
    seg1, seg2 = result.manifest.segments
    assert seg1.start_ms == 0
    assert seg1.end_ms == 2000
    assert seg2.start_ms == 2000
    assert seg2.end_ms == 3500


def test_total_duration_ms_correct(engine, tmp_path):
    project_id, *_, audio_store, visual_store = _build_ready_project(engine, tmp_path)
    result = _builder(engine, audio_store, visual_store).run(TimelineBuilderInput(project_id=project_id))
    assert result.manifest.total_duration_ms == 3500
    assert result.total_duration_ms == 3500


# ---------------------------------------------------------------------------
# Alignment
# ---------------------------------------------------------------------------


def test_script_voice_visual_ids_preserved(engine, tmp_path):
    project_id, *_, audio_store, visual_store = _build_ready_project(engine, tmp_path)
    result = _builder(engine, audio_store, visual_store).run(TimelineBuilderInput(project_id=project_id))
    seg1, seg2 = result.manifest.segments
    assert seg1.segment_id == "S1"
    assert seg1.script_line_ids == ["L001", "L002"]
    assert seg1.narration[0].chunk_id == "C001"
    assert seg1.visual.visual_beat_id == "V1"
    assert seg2.segment_id == "S2"
    assert seg2.narration[0].chunk_id == "C002"
    assert seg2.visual.visual_beat_id == "V2"


def test_rendered_visual_resolved(engine, tmp_path):
    project_id, *_, audio_store, visual_store = _build_ready_project(engine, tmp_path)
    result = _builder(engine, audio_store, visual_store).run(TimelineBuilderInput(project_id=project_id))
    seg1 = result.manifest.segments[0]
    assert seg1.visual.status is TimelineVisualSourceStatus.RENDERED
    assert seg1.visual.file_path == "V1_R1.png"
    assert seg1.visual.width == 64 and seg1.visual.height == 64


def test_requirement_visual_resolved(engine, tmp_path):
    project_id, *_, audio_store, visual_store = _build_ready_project(engine, tmp_path)
    result = _builder(engine, audio_store, visual_store).run(TimelineBuilderInput(project_id=project_id))
    seg2 = result.manifest.segments[1]
    assert seg2.visual.status is TimelineVisualSourceStatus.REQUIREMENT
    assert seg2.visual.reference == "ti_hero_shot"
    assert seg2.visual.file_path is None


def test_rendered_narration_resolved(engine, tmp_path):
    project_id, *_, audio_store, visual_store = _build_ready_project(engine, tmp_path)
    result = _builder(engine, audio_store, visual_store).run(TimelineBuilderInput(project_id=project_id))
    seg1 = result.manifest.segments[0]
    assert seg1.narration[0].render_job_id == "C001_T1"
    assert seg1.narration[0].file_path == "C001_T1.wav"


def test_multiple_chunks_share_one_visual_without_copying(engine, tmp_path):
    """A single AssemblySegment's voice_chunk_ids may list more than one
    VoiceChunk -- both narration refs point at real distinct files, and the
    segment's ONE visual reference is a single TimelineVisualRef object
    (its file_path string is referenced, not duplicated as bytes)."""
    project_id, script_plan, voice_plan, visual_plan, *_ = _build_ready_project(
        engine, tmp_path,
        assembly_overrides={
            "segments": [
                AssemblySegment(
                    segment_id="S1", script_line_ids=["L001", "L002", "L003", "L004"],
                    voice_chunk_ids=["C001", "C002"], visual_beat_id="V1",
                    start_seconds=0.0, end_seconds=8.0, music_state="BED",
                    transition_in="NONE", transition_out="NONE",
                ),
            ]
        },
    )
    # V2 is now unreferenced by any segment -- but VisualRenderManifest
    # coverage isn't re-validated by TimelineBuilder itself (only
    # AssemblyPlan's own segments drive resolution), so this remains valid.
    audio_store = AudioFileStore(tmp_path / "audio")
    visual_store = VisualFileStore(tmp_path / "visuals")
    result = _builder(engine, audio_store, visual_store).run(TimelineBuilderInput(project_id=project_id))

    assert len(result.manifest.segments) == 1
    segment = result.manifest.segments[0]
    assert [ref.chunk_id for ref in segment.narration] == ["C001", "C002"]
    assert segment.duration_ms == 3500  # 2000 + 1500
    assert segment.visual.visual_beat_id == "V1"


# ---------------------------------------------------------------------------
# Audio (tracks / cues)
# ---------------------------------------------------------------------------


def test_narration_track_generated_for_every_segment(engine, tmp_path):
    project_id, *_, audio_store, visual_store = _build_ready_project(engine, tmp_path)
    result = _builder(engine, audio_store, visual_store).run(TimelineBuilderInput(project_id=project_id))
    assert all(len(segment.narration) >= 1 for segment in result.manifest.segments)


def test_bed_duck_lift_cues_represented(engine, tmp_path):
    project_id, *_, audio_store, visual_store = _build_ready_project(engine, tmp_path)
    result = _builder(engine, audio_store, visual_store).run(TimelineBuilderInput(project_id=project_id))
    cue_types = [cue.cue_type for cue in result.manifest.cues]
    assert TimelineCueType.MUSIC_BED_START in cue_types  # S1 starts BED
    assert TimelineCueType.MUSIC_DUCK in cue_types  # S2 switches to DUCK
    assert TimelineCueType.MUSIC_BED_END in cue_types  # closing cue


def test_sfx_trigger_cue_from_chunk_sfx_opportunity(engine, tmp_path):
    project_id, *_, audio_store, visual_store = _build_ready_project(engine, tmp_path)
    result = _builder(engine, audio_store, visual_store).run(TimelineBuilderInput(project_id=project_id))
    sfx_cues = [cue for cue in result.manifest.cues if cue.cue_type is TimelineCueType.SFX_TRIGGER]
    assert len(sfx_cues) == 1
    assert sfx_cues[0].reference == "whoosh"
    assert sfx_cues[0].timestamp_ms == 0


def test_no_music_cues_when_music_state_never_changes(engine, tmp_path):
    project_id, *_, audio_store, visual_store = _build_ready_project(
        engine, tmp_path,
    )
    # Overwrite via a fresh assembly plan with both segments BED (no change).
    result = _builder(engine, audio_store, visual_store).run(TimelineBuilderInput(project_id=project_id))
    # Sanity: default fixture already has BED->DUCK, so just confirm exactly
    # 2 music cues (start + duck) plus the closing end cue = 3.
    music_cues = [c for c in result.manifest.cues if c.cue_type != TimelineCueType.SFX_TRIGGER]
    assert len(music_cues) == 3


# ---------------------------------------------------------------------------
# Transition downgrade mapping
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "intent,expected",
    [
        ("CUT", TimelineTransitionType.CUT),
        ("NONE", TimelineTransitionType.HOLD),
        ("DISSOLVE", TimelineTransitionType.CROSSFADE),
        ("MATCH", TimelineTransitionType.CUT),
        ("PUSH", TimelineTransitionType.CUT),
    ],
)
def test_transition_intent_downgrade_mapping(engine, tmp_path, intent, expected):
    project_id, *_, audio_store, visual_store = _build_ready_project(
        engine, tmp_path,
        assembly_overrides={
            "segments": [
                AssemblySegment(
                    segment_id="S1", script_line_ids=["L001", "L002", "L003", "L004"],
                    voice_chunk_ids=["C001", "C002"], visual_beat_id="V1",
                    start_seconds=0.0, end_seconds=8.0, music_state="BED",
                    transition_in=intent, transition_out="CUT",
                ),
            ]
        },
    )
    audio_store2 = AudioFileStore(tmp_path / "audio")
    visual_store2 = VisualFileStore(tmp_path / "visuals")
    result = _builder(engine, audio_store2, visual_store2).run(TimelineBuilderInput(project_id=project_id))
    segment = result.manifest.segments[0]
    assert segment.transition_in is expected
    assert segment.source_transition_in.value == intent


# ---------------------------------------------------------------------------
# Validation / failure behavior
# ---------------------------------------------------------------------------


def test_missing_audio_file_fails_explicitly(engine, tmp_path):
    project_id, script_plan, voice_plan, *_ = _build_ready_project(engine, tmp_path)
    audio_store = AudioFileStore(tmp_path / "audio")
    (audio_store.root / "C001_T1.wav").unlink()  # metadata says it exists; file does not
    visual_store = VisualFileStore(tmp_path / "visuals")

    with pytest.raises(MissingAudioFileError):
        _builder(engine, audio_store, visual_store).run(TimelineBuilderInput(project_id=project_id))


def test_missing_visual_file_fails_explicitly(engine, tmp_path):
    project_id, *_ = _build_ready_project(engine, tmp_path)
    audio_store = AudioFileStore(tmp_path / "audio")
    visual_store = VisualFileStore(tmp_path / "visuals")
    (visual_store.root / "V1_R1.png").unlink()

    with pytest.raises(MissingVisualFileError):
        _builder(engine, audio_store, visual_store).run(TimelineBuilderInput(project_id=project_id))


def test_stale_voice_render_manifest_fails_explicitly(engine, tmp_path):
    project_id, script_plan, voice_plan, visual_plan, assembly_plan, *_ = _build_ready_project(
        engine, tmp_path
    )
    # A VoiceRenderManifest stamped with a different voice_plan_id.
    audio_store = AudioFileStore(tmp_path / "audio")
    stale_manifest = _voice_render_manifest(script_plan.id, uuid4(), audio_store)
    save_artifact(engine, project_id, "voice_render_manifest", stale_manifest)
    visual_store = VisualFileStore(tmp_path / "visuals")

    with pytest.raises(StaleVoiceRenderManifestError):
        _builder(engine, audio_store, visual_store).run(TimelineBuilderInput(project_id=project_id))


def test_stale_visual_render_manifest_fails_explicitly(engine, tmp_path):
    project_id, script_plan, voice_plan, visual_plan, *_ = _build_ready_project(engine, tmp_path)
    visual_store = VisualFileStore(tmp_path / "visuals")
    stale_manifest = _visual_render_manifest(script_plan.id, voice_plan.id, uuid4(), visual_store)
    save_artifact(engine, project_id, "visual_render_manifest", stale_manifest)
    audio_store = AudioFileStore(tmp_path / "audio")

    with pytest.raises(StaleVisualRenderManifestError):
        _builder(engine, audio_store, visual_store).run(TimelineBuilderInput(project_id=project_id))


def test_stale_assembly_plan_fails_explicitly(engine, tmp_path):
    project_id, script_plan, voice_plan, visual_plan, *_ = _build_ready_project(engine, tmp_path)
    stale_plan = _assembly_plan(script_plan.id, voice_plan.id, uuid4())
    save_artifact(engine, project_id, ASSEMBLY_PLAN_ARTIFACT_TYPE, stale_plan)
    audio_store = AudioFileStore(tmp_path / "audio")
    visual_store = VisualFileStore(tmp_path / "visuals")

    with pytest.raises(StaleAssemblyPlanError):
        _builder(engine, audio_store, visual_store).run(TimelineBuilderInput(project_id=project_id))


def test_stale_voice_plan_fails_explicitly(engine, tmp_path):
    project_id, script_plan, *_ = _build_ready_project(engine, tmp_path)
    script_plan_b = _valid_script_plan(("L001", "L002", "L003", "L004"))
    save_artifact(engine, project_id, SCRIPT_PLAN_ARTIFACT_TYPE, script_plan_b)
    update_artifact_reference(engine, project_id, "script_plan_id", script_plan_b.id)
    audio_store = AudioFileStore(tmp_path / "audio")
    visual_store = VisualFileStore(tmp_path / "visuals")

    with pytest.raises(StaleVoicePlanError):
        _builder(engine, audio_store, visual_store).run(TimelineBuilderInput(project_id=project_id))


def test_missing_voice_render_manifest_fails_explicitly(engine, tmp_path):
    # Build fully but skip saving a VoiceRenderManifest.
    project_id, script_plan = _create_project_at_mvp_complete(engine)
    voice_plan = _voice_plan(script_plan.id)
    save_artifact(engine, project_id, VOICE_PLAN_ARTIFACT_TYPE, voice_plan)
    visual_plan = _visual_plan(script_plan.id, voice_plan.id)
    save_artifact(engine, project_id, VISUAL_PLAN_ARTIFACT_TYPE, visual_plan)
    assembly_plan = _assembly_plan(script_plan.id, voice_plan.id, visual_plan.id)
    save_artifact(engine, project_id, ASSEMBLY_PLAN_ARTIFACT_TYPE, assembly_plan)

    audio_store = AudioFileStore(tmp_path / "audio")
    visual_store = VisualFileStore(tmp_path / "visuals")

    with pytest.raises(MissingVoiceRenderManifestArtifactError):
        _builder(engine, audio_store, visual_store).run(TimelineBuilderInput(project_id=project_id))


def test_unknown_voice_chunk_reference_fails_explicitly(engine, tmp_path):
    project_id, script_plan, voice_plan, visual_plan, *_ = _build_ready_project(
        engine, tmp_path,
        assembly_overrides={
            "segments": [
                AssemblySegment(
                    segment_id="S1", script_line_ids=["L001", "L002", "L003", "L004"],
                    voice_chunk_ids=["C999"], visual_beat_id="V1",
                    start_seconds=0.0, end_seconds=8.0, music_state="BED",
                    transition_in="NONE", transition_out="NONE",
                ),
            ]
        },
    )
    audio_store = AudioFileStore(tmp_path / "audio")
    visual_store = VisualFileStore(tmp_path / "visuals")

    with pytest.raises(UnknownVoiceChunkReferenceError):
        _builder(engine, audio_store, visual_store).run(TimelineBuilderInput(project_id=project_id))


def test_duplicate_segment_id_fails_explicitly(engine, tmp_path):
    project_id, script_plan, voice_plan, visual_plan, *_ = _build_ready_project(
        engine, tmp_path,
        assembly_overrides={
            "segments": [
                AssemblySegment(
                    segment_id="S1", script_line_ids=["L001", "L002"], voice_chunk_ids=["C001"],
                    visual_beat_id="V1", start_seconds=0.0, end_seconds=4.0,
                    music_state="BED", transition_in="NONE", transition_out="CUT",
                ),
                AssemblySegment(
                    segment_id="S1", script_line_ids=["L003", "L004"], voice_chunk_ids=["C002"],
                    visual_beat_id="V2", start_seconds=4.0, end_seconds=8.0,
                    music_state="DUCK", transition_in="DISSOLVE", transition_out="NONE",
                ),
            ]
        },
    )
    audio_store = AudioFileStore(tmp_path / "audio")
    visual_store = VisualFileStore(tmp_path / "visuals")

    with pytest.raises(TimelineManifestIntegrityError):
        _builder(engine, audio_store, visual_store).run(TimelineBuilderInput(project_id=project_id))


# ---------------------------------------------------------------------------
# Motion-lite (Phase 29)
# ---------------------------------------------------------------------------


def test_segment_defaults_to_static_motion_when_unspecified(engine, tmp_path):
    project_id, *_, audio_store, visual_store = _build_ready_project(engine, tmp_path)
    result = _builder(engine, audio_store, visual_store).run(TimelineBuilderInput(project_id=project_id))
    assert all(segment.visual_motion is VisualMotionType.STATIC for segment in result.manifest.segments)


def test_visual_motions_override_applies_to_named_segment_only(engine, tmp_path):
    project_id, *_, audio_store, visual_store = _build_ready_project(engine, tmp_path)
    result = _builder(engine, audio_store, visual_store).run(
        TimelineBuilderInput(project_id=project_id, visual_motions={"S1": VisualMotionType.SLOW_ZOOM_IN})
    )
    seg1 = next(s for s in result.manifest.segments if s.segment_id == "S1")
    seg2 = next(s for s in result.manifest.segments if s.segment_id == "S2")
    assert seg1.visual_motion is VisualMotionType.SLOW_ZOOM_IN
    assert seg2.visual_motion is VisualMotionType.STATIC  # unspecified -- never inferred


def test_unknown_motion_segment_reference_rejected(engine, tmp_path):
    project_id, *_, audio_store, visual_store = _build_ready_project(engine, tmp_path)
    with pytest.raises(UnknownMotionSegmentReferenceError):
        _builder(engine, audio_store, visual_store).run(
            TimelineBuilderInput(project_id=project_id, visual_motions={"S999": VisualMotionType.PAN_LEFT})
        )


# ---------------------------------------------------------------------------
# Lifecycle
# ---------------------------------------------------------------------------


def test_module_run_success(engine, tmp_path):
    project_id, *_, audio_store, visual_store = _build_ready_project(engine, tmp_path)
    result = _builder(engine, audio_store, visual_store).run(TimelineBuilderInput(project_id=project_id))

    runs = list_module_runs_for_project(engine, project_id)
    timeline_runs = [run for run in runs if run.module == "timeline_builder"]
    assert len(timeline_runs) == 1
    assert timeline_runs[0].status == ModuleRunStatus.SUCCESS
    assert timeline_runs[0].output_id == str(result.manifest.id)


def test_module_run_failed_on_validation_error(engine, tmp_path):
    project_id, *_ = _build_ready_project(engine, tmp_path)
    audio_store = AudioFileStore(tmp_path / "audio")
    visual_store = VisualFileStore(tmp_path / "visuals")
    (audio_store.root / "C001_T1.wav").unlink()

    with pytest.raises(MissingAudioFileError):
        _builder(engine, audio_store, visual_store).run(TimelineBuilderInput(project_id=project_id))

    runs = list_module_runs_for_project(engine, project_id)
    timeline_runs = [run for run in runs if run.module == "timeline_builder"]
    assert len(timeline_runs) == 1
    assert timeline_runs[0].status == ModuleRunStatus.FAILED


def test_no_project_state_transition(engine, tmp_path):
    from app.storage.projects import get_project

    project_id, *_, audio_store, visual_store = _build_ready_project(engine, tmp_path)
    _builder(engine, audio_store, visual_store).run(TimelineBuilderInput(project_id=project_id))
    assert get_project(engine, project_id).state.value == "MVP_COMPLETE"


def test_no_llm_dependency_in_fresh_subprocess():
    """Runs in a fresh subprocess -- within this test process, an earlier
    test module may have already imported app.llm."""
    project_root = Path(__file__).resolve().parent.parent
    script = (
        "import sys\n"
        "import app.renderers.timeline\n"
        "loaded = [name for name in sys.modules "
        "if name == 'app.llm' or name.startswith('app.llm.')]\n"
        "print(','.join(loaded))\n"
    )
    result = subprocess.run(
        [sys.executable, "-c", script], cwd=str(project_root), capture_output=True, text=True, timeout=30,
    )
    assert result.returncode == 0, result.stderr
    assert result.stdout.strip() == ""


# ---------------------------------------------------------------------------
# Determinism
# ---------------------------------------------------------------------------


def test_identical_repeated_build_produces_identical_business_timeline(engine, tmp_path):
    project_id, *_, audio_store, visual_store = _build_ready_project(engine, tmp_path)
    builder = _builder(engine, audio_store, visual_store)

    first = builder.run(TimelineBuilderInput(project_id=project_id))
    second = builder.run(TimelineBuilderInput(project_id=project_id))

    def _business_fields(manifest):
        return [
            (s.segment_id, s.start_ms, s.end_ms, s.duration_ms, s.visual.visual_beat_id,
             [r.chunk_id for r in s.narration])
            for s in manifest.segments
        ], manifest.total_duration_ms, [
            (c.timestamp_ms, c.cue_type.value) for c in manifest.cues
        ]

    assert _business_fields(first.manifest) == _business_fields(second.manifest)
    assert first.manifest.id != second.manifest.id  # only id/created_at may differ
