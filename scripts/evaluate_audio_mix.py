"""Manual integration-evaluation utility for Phase 30: deterministic
music & SFX mix execution.

Reuses scripts/evaluate_timeline_builder.py's (Phase 27) project-graph
builder and scripts/evaluate_video_encoder.py's (Phase 28) diagram-canvas
fix -- three real static visual frames (GENERATED_STILL via
FakeVisualProvider, a local DIAGRAM overlay, and a COMPOSITION frame
combining both) and three real local WAV narration files, no live AI/TTS
API of any kind. On top of that this script:

- re-saves the VoicePlan so BOTH C001 and C003 carry an sfx_opportunity
  (Phase 27's own fixture only gives C001 one) -- demonstrating at least
  two SFX_TRIGGER cues;
- authors an AssemblyPlan with S1/S2/S3 at music_state BED/DUCK/LIFT
  (TimelineBuilder's own music-state-change cue derivation then produces
  MUSIC_BED_START/MUSIC_DUCK/MUSIC_LIFT automatically, plus an explicit
  MUSIC_BED_END at the timeline's own end) and S2.transition_out=DISSOLVE
  (a real CROSSFADE into S3 -- the sole field this encoder consults for
  join decisions);
- gives S1 a SLOW_ZOOM_IN via TimelineBuilderInput.visual_motions;
- generates three simple sine-tone WAV fixtures locally (a music bed and
  two distinct "SFX" tones) -- deliberately basic technical test audio,
  never real or copyrighted music/SFX, and never downloaded from
  anywhere;
- passes an explicit AudioAssetBindings into VideoRendererInput, opting
  into Phase 30's real music/SFX mix execution.

Produces exactly one real, locally playable MP4:

    data/audio_mix_evaluation/motily_phase30_preview.mp4

The one-project chain this needs to reach MVP_COMPLETE is built directly
against a dedicated, disposable evaluation database
(data/audio_mix_evaluation/eval.db, recreated fresh on every run) --
never against the shared data/motily.db.

This evaluation is primarily a technical mix verification (timing,
looping, gain-state changes, mixing, muxing) -- the sine-tone fixtures
prove correctness, not production-quality sound.

Usage:

    python scripts/evaluate_audio_mix.py
"""

from __future__ import annotations

import json
import math
import shutil
import struct
import subprocess
import sys
import wave
from pathlib import Path

from app.engines.assembly_plan.models import ASSEMBLY_PLAN_ARTIFACT_TYPE
from app.engines.visual_plan.models import VISUAL_PLAN_ARTIFACT_TYPE
from app.engines.voice_plan.models import VOICE_PLAN_ARTIFACT_TYPE
from app.layer_compositor.models import LayerAnchor, LayerScale
from app.models.assembly import AssemblyPlan, AssemblySegment
from app.models.timeline import VisualMotionType
from app.renderers.timeline.builder import TimelineBuilder
from app.renderers.timeline.models import TimelineBuilderInput
from app.renderers.video.renderer import VideoRenderer
from app.renderers.video.models import VideoRendererInput
from app.renderers.visual.models import BackgroundLayerSource, CompositionSpec, OverlayLayerSource, VisualRendererInput
from app.renderers.visual.renderer import VisualRenderer
from app.audio.storage import AudioFileStore
from app.storage.artifacts import save_artifact
from app.storage.database import init_database
from app.video_encoder.models import AudioAssetBindings, VideoEncodingSettings
from app.video_encoder.storage import VideoFileStore
from app.visual.config import VisualSettings
from app.visual.fake import FakeVisualProvider
from app.visual.storage import VisualFileStore

sys.path.insert(0, str(Path(__file__).resolve().parent))
from evaluate_timeline_builder import (  # noqa: E402
    BACKGROUND_SIZE,
    _build_project_at_mvp_complete,
    _build_voice_render_manifest,
    _fake_still_response,
)
from evaluate_video_encoder import _fix_diagram_canvas_to_match_background  # noqa: E402

EVAL_DIR = Path("data/audio_mix_evaluation")
EVAL_DB_PATH = EVAL_DIR / "eval.db"
OUTPUT_FILENAME = "motily_phase30_preview.mp4"


def _build_assembly_plan_music_and_crossfade(script_plan, voice_plan, visual_plan) -> AssemblyPlan:
    """Same timing shape as evaluate_timeline_builder.py's own
    _build_assembly_plan (music_state BED/DUCK/LIFT on S1/S2/S3 already
    matches Phase 30's own demonstration requirements exactly), but
    S2.transition_out=DISSOLVE makes S2->S3 a real CROSSFADE (S1->S2
    stays a plain CUT)."""
    return AssemblyPlan(
        script_plan_id=script_plan.id, voice_plan_id=voice_plan.id, visual_plan_id=visual_plan.id,
        estimated_total_duration_seconds=6.0,
        segments=[
            AssemblySegment(
                segment_id="S1", script_line_ids=["L001"], voice_chunk_ids=["C001"], visual_beat_id="V1",
                start_seconds=0.0, end_seconds=2.0, music_state="BED", transition_in="NONE", transition_out="CUT",
            ),
            AssemblySegment(
                segment_id="S2", script_line_ids=["L002"], voice_chunk_ids=["C002"], visual_beat_id="V2",
                start_seconds=2.0, end_seconds=3.5, music_state="DUCK", transition_in="CUT", transition_out="DISSOLVE",
            ),
            AssemblySegment(
                segment_id="S3", script_line_ids=["L003"], voice_chunk_ids=["C003"], visual_beat_id="V3",
                start_seconds=3.5, end_seconds=6.0, music_state="LIFT", transition_in="NONE", transition_out="NONE",
            ),
        ],
    )


def _add_second_sfx_opportunity(engine, project_id, voice_plan):
    """Phase 27's own fixture VoicePlan only gives C001 an
    sfx_opportunity ("whoosh") -- Phase 30's evaluation needs at least
    two SFX_TRIGGER cues, so C003 is given a second, distinct one
    ("ding") here and the VoicePlan re-saved, mirroring
    evaluate_video_encoder.py's own diagram-canvas-fix pattern."""
    fixed_chunks = [
        chunk.model_copy(update={"sfx_opportunity": "ding"}) if chunk.chunk_id == "C003" else chunk
        for chunk in voice_plan.chunks
    ]
    fixed_voice_plan = voice_plan.model_copy(update={"chunks": fixed_chunks})
    save_artifact(engine, project_id, VOICE_PLAN_ARTIFACT_TYPE, fixed_voice_plan)
    return fixed_voice_plan


def _write_tone_wav(path: Path, duration_seconds: float, freq_hz: float, framerate: int = 8000) -> Path:
    """A simple sine-tone WAV -- clearly labeled technical test audio,
    never real or copyrighted music/SFX, never downloaded from
    anywhere."""
    path.parent.mkdir(parents=True, exist_ok=True)
    n = int(duration_seconds * framerate)
    with wave.open(str(path), "wb") as wav_file:
        wav_file.setnchannels(1)
        wav_file.setsampwidth(2)
        wav_file.setframerate(framerate)
        frames = bytearray()
        for i in range(n):
            value = int(8000 * math.sin(2 * math.pi * freq_hz * i / framerate))
            frames += struct.pack("<h", value)
        wav_file.writeframes(bytes(frames))
    return path


def _ffmpeg_version() -> str:
    result = subprocess.run(["ffmpeg", "-version"], capture_output=True, text=True, timeout=30)
    return result.stdout.splitlines()[0] if result.returncode == 0 else "(unavailable)"


def _ffprobe_summary(output_path: Path) -> dict:
    result = subprocess.run(
        ["ffprobe", "-v", "error", "-print_format", "json", "-show_format", "-show_streams", str(output_path)],
        capture_output=True, text=True, timeout=60,
    )
    if result.returncode != 0:
        return {}
    return json.loads(result.stdout)


def main() -> int:
    for stream in (sys.stdout, sys.stderr):
        if hasattr(stream, "reconfigure"):
            stream.reconfigure(encoding="utf-8")

    if shutil.which("ffmpeg") is None or shutil.which("ffprobe") is None:
        print("ffmpeg/ffprobe not found on PATH -- cannot run this evaluation.", file=sys.stderr)
        return 1

    EVAL_DIR.mkdir(parents=True, exist_ok=True)
    if EVAL_DB_PATH.is_file():
        EVAL_DB_PATH.unlink()  # fresh, disposable DB every run
    engine = init_database(EVAL_DB_PATH)

    project, script_plan, voice_plan, visual_plan = _build_project_at_mvp_complete(engine)
    _fix_diagram_canvas_to_match_background(engine, project.project_id, visual_plan, BACKGROUND_SIZE)
    voice_plan = _add_second_sfx_opportunity(engine, project.project_id, voice_plan)

    # --- Visual rendering: real VisualRenderer, FakeVisualProvider for V1,
    # real local DiagramRenderer for V2, real local VisualLayerCompositor
    # for V3. No live API of any kind.
    provider = FakeVisualProvider([_fake_still_response()])
    visual_store = VisualFileStore(EVAL_DIR / "visuals")
    visual_renderer = VisualRenderer(engine, provider, VisualSettings(provider="none"), visual_store)

    composition_spec = CompositionSpec(
        layers=[
            BackgroundLayerSource(id="background", source_beat_id="V1"),
            OverlayLayerSource(
                id="diagram_overlay", source_beat_id="V2", z_index=1,
                anchor=LayerAnchor.TOP_LEFT, scale=LayerScale(relative_height=0.4), margin=16,
            ),
        ]
    )
    visual_renderer.run(
        VisualRendererInput(project_id=project.project_id, composition_specs={"V3": composition_spec})
    )

    # --- AssemblyPlan (music BED/DUCK/LIFT, one CUT, one real CROSSFADE)
    # + VoiceRenderManifest: hand-authored, deterministic, local WAV
    # fixtures -- no live TTS API.
    assembly_plan = _build_assembly_plan_music_and_crossfade(script_plan, voice_plan, visual_plan)
    save_artifact(engine, project.project_id, ASSEMBLY_PLAN_ARTIFACT_TYPE, assembly_plan)

    audio_store = AudioFileStore(EVAL_DIR / "audio")
    voice_render_manifest = _build_voice_render_manifest(script_plan, voice_plan, audio_store)
    save_artifact(engine, project.project_id, "voice_render_manifest", voice_render_manifest)

    # --- Timeline assembly: the real TimelineBuilder (Phase 27), with
    # Phase 29's explicit motion authoring for S1.
    timeline_builder = TimelineBuilder(engine, audio_store, visual_store)
    timeline_result = timeline_builder.run(
        TimelineBuilderInput(
            project_id=project.project_id, visual_motions={"S1": VisualMotionType.SLOW_ZOOM_IN}
        )
    )
    timeline_manifest = timeline_result.manifest

    # --- Phase 30's own local, deterministic, sine-tone WAV fixtures --
    # simple technical test audio, never real/copyrighted music or SFX.
    music_path = _write_tone_wav(EVAL_DIR / "fixtures" / "TEST_TONE_music_bed.wav", 2.5, 196.0)
    whoosh_path = _write_tone_wav(EVAL_DIR / "fixtures" / "TEST_TONE_sfx_whoosh.wav", 0.35, 900.0)
    ding_path = _write_tone_wav(EVAL_DIR / "fixtures" / "TEST_TONE_sfx_ding.wav", 0.25, 1400.0)
    audio_bindings = AudioAssetBindings(
        music_bed_path=music_path, sfx_by_id={"whoosh": whoosh_path, "ding": ding_path}
    )

    # --- Video + audio mix encoding: the real VideoRenderer (Phase 28/
    # 29/30), opted into real music/SFX mix execution via audio_bindings,
    # writing directly to the final evaluation output path.
    video_store = VideoFileStore(EVAL_DIR)
    video_renderer = VideoRenderer(engine, audio_store, visual_store, video_store)
    video_result = video_renderer.run(
        VideoRendererInput(project_id=project.project_id, audio_bindings=audio_bindings)
    )
    asset = video_result.asset

    # Move (not copy) the encoded file to the exact required evaluation
    # path -- VideoRenderer's own deterministic {project_id}/
    # {timeline_id}/video.mp4 convention is used internally, but this
    # script must produce exactly one MP4, at a fixed, predictable name.
    encoded_path = video_store.root / asset.file_path
    final_path = EVAL_DIR / OUTPUT_FILENAME
    encoded_path.replace(final_path)
    # Clean up the now-empty intermediate directories left behind.
    encoded_path.parent.rmdir()
    encoded_path.parent.parent.rmdir()

    probe = _ffprobe_summary(final_path)
    video_stream = next((s for s in probe.get("streams", []) if s["codec_type"] == "video"), {})
    audio_stream = next((s for s in probe.get("streams", []) if s["codec_type"] == "audio"), {})
    probed_duration_seconds = float(probe.get("format", {}).get("duration", 0.0))

    print("NOTE: music_bed/sfx fixtures are plain locally-generated sine")
    print("tones -- technical test audio only, not production music/SFX.")
    print()
    print(f"Evaluation project: {project.project_id}")
    print(f"Timeline segments: {len(timeline_manifest.segments)}")
    for segment in timeline_manifest.segments:
        print(
            f"  {segment.segment_id}: duration={segment.duration_ms}ms "
            f"transition_out={segment.transition_out.value} motion={segment.visual_motion.value}"
        )
    print()
    print("Cue events:")
    for cue in timeline_manifest.cues:
        ref = f" ({cue.reference})" if cue.reference else ""
        print(f"  {cue.timestamp_ms:>6}ms  {cue.cue_type.value}{ref}")
    print()
    print(f"Path:               {final_path.resolve()}")
    print(f"File size:          {final_path.stat().st_size} bytes")
    print(f"Video codec:        {video_stream.get('codec_name', asset.video_codec)}")
    print(f"Audio codec:        {audio_stream.get('codec_name', asset.audio_codec)}")
    print(f"FPS:                {asset.fps}")
    print(f"Canvas:             {asset.width}x{asset.height}")
    print(f"Duration (ffprobe): {probed_duration_seconds:.3f}s")
    print(f"Duration (asset):   {asset.duration_ms / 1000:.3f}s")
    print(f"Timeline duration:  {timeline_manifest.total_duration_ms / 1000:.3f}s")
    print(f"Crossfade count:    {asset.crossfade_count}")
    print(f"Motions used:       {[m.value for m in asset.motion_profile_used]}")
    print(f"Music cue count:    {asset.music_cue_count}")
    print(f"SFX event count:    {asset.sfx_event_count}")
    print(f"Has music:          {asset.has_music}")
    print(
        "Audio bindings used: music_bed="
        f"{music_path.name}, sfx={{'whoosh': {whoosh_path.name!r}, 'ding': {ding_path.name!r}}}"
    )
    print(f"FFmpeg version:     {_ffmpeg_version()}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
