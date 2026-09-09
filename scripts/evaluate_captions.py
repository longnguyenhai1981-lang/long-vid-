"""Manual integration-evaluation utility for Phase 31: deterministic
subtitle/caption execution.

Builds on scripts/evaluate_audio_mix.py's own project shape exactly
(three real static visual frames, music BED/DUCK/LIFT, a real CROSSFADE,
a SLOW_ZOOM_IN, two SFX_TRIGGER events, three locally-generated sine-tone
WAV fixtures) so this evaluation demonstrates that existing narration/
music/SFX mixing and existing CROSSFADE/motion-lite execution are all
completely unchanged by adding captions on top -- a disposable local
fixture project, deliberately NOT coupled to Phase 30's own disposable
evaluation output path (each phase's evaluation script owns its own
throwaway database and fixtures).

On top of that project, this script additionally:

- runs the real CaptionBuilder (Phase 31) to derive a CaptionManifest
  directly from the same ScriptPlan/VoicePlan/TimelineManifest chain --
  never transcribing audio, never calling an LLM;
- runs the real SubtitleRenderer to export a real UTF-8 SRT file and
  (since local ffmpeg's `subtitles` filter is available in this
  development environment) burn captions into a copy of the encoded
  video via real local ffmpeg.

Produces:

    data/caption_evaluation/motily_phase31_captions.srt   (always)
    data/caption_evaluation/motily_phase31_preview.mp4    (if local
                                                             burn-in is
                                                             available)

The one-project chain this needs to reach MVP_COMPLETE is built directly
against a dedicated, disposable evaluation database
(data/caption_evaluation/eval.db, recreated fresh on every run) -- never
against the shared data/motily.db.

Usage:

    python scripts/evaluate_captions.py
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

from app.captions.models import CaptionRenderSettings
from app.captions.storage import SubtitleFileStore
from app.engines.assembly_plan.models import ASSEMBLY_PLAN_ARTIFACT_TYPE
from app.engines.visual_plan.models import VISUAL_PLAN_ARTIFACT_TYPE
from app.engines.voice_plan.models import VOICE_PLAN_ARTIFACT_TYPE
from app.layer_compositor.models import LayerAnchor, LayerScale
from app.models.assembly import AssemblyPlan, AssemblySegment
from app.models.timeline import VisualMotionType
from app.renderers.caption.builder import CaptionBuilder
from app.renderers.caption.models import CaptionBuilderInput
from app.renderers.subtitle.models import SubtitleRendererInput
from app.renderers.subtitle.renderer import SubtitleRenderer
from app.renderers.timeline.builder import TimelineBuilder
from app.renderers.timeline.models import TimelineBuilderInput
from app.renderers.video.renderer import VideoRenderer
from app.renderers.video.models import VideoRendererInput
from app.renderers.visual.models import BackgroundLayerSource, CompositionSpec, OverlayLayerSource, VisualRendererInput
from app.renderers.visual.renderer import VisualRenderer
from app.audio.storage import AudioFileStore
from app.storage.artifacts import save_artifact
from app.storage.database import init_database
from app.video_encoder.models import AudioAssetBindings
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
from evaluate_audio_mix import _add_second_sfx_opportunity, _write_tone_wav  # noqa: E402

EVAL_DIR = Path("data/caption_evaluation")
EVAL_DB_PATH = EVAL_DIR / "eval.db"
SRT_FILENAME = "motily_phase31_captions.srt"
PREVIEW_FILENAME = "motily_phase31_preview.mp4"


def _build_assembly_plan_music_and_crossfade(script_plan, voice_plan, visual_plan) -> AssemblyPlan:
    """Identical shape to scripts/evaluate_audio_mix.py's own: music
    BED/DUCK/LIFT on S1/S2/S3 plus S2.transition_out=DISSOLVE (a real
    CROSSFADE into S3)."""
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


def _ffmpeg_version() -> str:
    result = subprocess.run(["ffmpeg", "-version"], capture_output=True, text=True, timeout=30)
    return result.stdout.splitlines()[0] if result.returncode == 0 else "(unavailable)"


def _ffmpeg_has_subtitles_filter() -> bool:
    result = subprocess.run(["ffmpeg", "-hide_banner", "-filters"], capture_output=True, text=True, timeout=30)
    return result.returncode == 0 and "subtitles" in result.stdout


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

    # --- Captions: the real CaptionBuilder (Phase 31) -- derived
    # entirely from ScriptPlan/VoicePlan/TimelineManifest, never from
    # audio, never from an LLM.
    caption_result = CaptionBuilder(engine).run(CaptionBuilderInput(project_id=project.project_id))
    caption_manifest = caption_result.manifest

    # --- Phase 30's own local, deterministic, sine-tone WAV fixtures --
    # simple technical test audio, never real/copyrighted music or SFX.
    music_path = _write_tone_wav(EVAL_DIR / "fixtures" / "TEST_TONE_music_bed.wav", 2.5, 196.0)
    whoosh_path = _write_tone_wav(EVAL_DIR / "fixtures" / "TEST_TONE_sfx_whoosh.wav", 0.35, 900.0)
    ding_path = _write_tone_wav(EVAL_DIR / "fixtures" / "TEST_TONE_sfx_ding.wav", 0.25, 1400.0)
    audio_bindings = AudioAssetBindings(
        music_bed_path=music_path, sfx_by_id={"whoosh": whoosh_path, "ding": ding_path}
    )

    # --- Video + audio mix encoding: the real VideoRenderer (Phase 28/
    # 29/30), opted into real music/SFX mix execution.
    video_store = VideoFileStore(EVAL_DIR)
    video_renderer = VideoRenderer(engine, audio_store, visual_store, video_store)
    video_result = video_renderer.run(
        VideoRendererInput(project_id=project.project_id, audio_bindings=audio_bindings)
    )
    encoded_asset = video_result.asset

    # --- Subtitles: the real SubtitleRenderer (Phase 31) -- always
    # exports a real UTF-8 SRT; burns captions into a real copy of the
    # encoded video if this local ffmpeg build supports the `subtitles`
    # filter (checked explicitly, never assumed).
    burn_in_available = _ffmpeg_has_subtitles_filter()
    subtitle_store = SubtitleFileStore(EVAL_DIR)
    subtitle_renderer = SubtitleRenderer(engine, subtitle_store, video_store)
    subtitle_result = subtitle_renderer.run(
        SubtitleRendererInput(
            project_id=project.project_id,
            burn_in_settings=CaptionRenderSettings() if burn_in_available else None,
        )
    )
    subtitle_asset = subtitle_result.subtitle_asset

    # Move (not copy) the exported SRT and (if produced) the captioned
    # video to their exact required evaluation paths --
    # SubtitleRenderer's own deterministic {project_id}/
    # {caption_manifest_id}/{captions.srt,captioned.mp4} convention is
    # used internally, and both files share that same directory, so
    # cleanup of the now-empty intermediate directories only happens
    # once both have been moved out.
    exported_srt_path = subtitle_store.root / subtitle_asset.file_path
    final_srt_path = EVAL_DIR / SRT_FILENAME
    exported_srt_path.replace(final_srt_path)

    final_video_path = None
    probe = {}
    if subtitle_result.captioned_video_asset is not None:
        captioned_asset = subtitle_result.captioned_video_asset
        encoded_path = video_store.root / captioned_asset.file_path
        final_video_path = EVAL_DIR / PREVIEW_FILENAME
        encoded_path.replace(final_video_path)
        probe = _ffprobe_summary(final_video_path)

    intermediate_dir = exported_srt_path.parent
    if intermediate_dir.is_dir() and not any(intermediate_dir.iterdir()):
        intermediate_dir.rmdir()
        if intermediate_dir.parent.is_dir() and not any(intermediate_dir.parent.iterdir()):
            intermediate_dir.parent.rmdir()

    srt_text = final_srt_path.read_text(encoding="utf-8")
    first_timestamp = srt_text.splitlines()[1].split(" --> ")[0]
    last_cue_block = srt_text.strip().split("\n\n")[-1]
    last_timestamp = last_cue_block.splitlines()[1].split(" --> ")[1]

    print(f"Evaluation project: {project.project_id}")
    print()
    print("Caption:")
    print(f"  SRT path:               {final_srt_path.resolve()}")
    print(f"  Cue count:              {len(caption_manifest.cues)}")
    print(f"  Total timeline duration: {timeline_manifest.total_duration_ms / 1000:.3f}s")
    print(f"  Encoding:               {subtitle_asset.encoding}")
    print(f"  First cue start:        {first_timestamp}")
    print(f"  Last cue end:           {last_timestamp}")
    for cue in caption_manifest.cues:
        print(f"    [{cue.start_ms:>5}-{cue.end_ms:>5}ms] {cue.text}")
    print()

    if final_video_path is not None:
        video_stream = next((s for s in probe.get("streams", []) if s["codec_type"] == "video"), {})
        audio_stream = next((s for s in probe.get("streams", []) if s["codec_type"] == "audio"), {})
        probed_duration_seconds = float(probe.get("format", {}).get("duration", 0.0))
        captioned_asset = subtitle_result.captioned_video_asset
        print("Video:")
        print(f"  Output path:            {final_video_path.resolve()}")
        print(f"  File size:              {final_video_path.stat().st_size} bytes")
        print(f"  Video codec:            {video_stream.get('codec_name', captioned_asset.video_codec)}")
        print(f"  Audio codec:            {audio_stream.get('codec_name', captioned_asset.audio_codec)}")
        print(f"  FPS:                    {captioned_asset.fps}")
        print(f"  Canvas:                 {captioned_asset.width}x{captioned_asset.height}")
        print(f"  Duration (ffprobe):     {probed_duration_seconds:.3f}s")
        print(f"  Duration (asset):       {captioned_asset.duration_ms / 1000:.3f}s")
        print(f"  Audio stream copied:    {captioned_asset.audio_stream_copied}")
        print(f"  Source encoded video:   crossfade_count={encoded_asset.crossfade_count}, "
              f"motions={[m.value for m in encoded_asset.motion_profile_used]}, "
              f"has_music={encoded_asset.has_music}, sfx_event_count={encoded_asset.sfx_event_count}")
    else:
        print("Video: burn-in NOT available on this machine (ffmpeg `subtitles` filter "
              "not found) -- SRT generation remains valid regardless; skipped cleanly.")
    print(f"FFmpeg version:           {_ffmpeg_version()}")
    print(f"Subtitle filter capability: {'available' if burn_in_available else 'unavailable'}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
