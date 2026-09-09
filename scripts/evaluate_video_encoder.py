"""Manual integration-evaluation utility for Phase 28: deterministic local
video encoding.

Reuses scripts/evaluate_timeline_builder.py's (Phase 27) project-graph
builder -- three real static visual frames (GENERATED_STILL via
FakeVisualProvider, a local DIAGRAM overlay, and a COMPOSITION frame
combining both) and three real local WAV narration files, no live AI/TTS
API of any kind -- but with an all-CUT/HOLD AssemblyPlan (Phase 27's own
evaluation script uses one DISSOLVE transition, which Phase 28 explicitly
does not execute yet; see UnsupportedVideoTransitionError), then drives
the real VideoRenderer.run() path (Phase 28) to produce exactly one real,
locally playable MP4:

    data/video_encoder_evaluation/motily_phase28_preview.mp4

The one-project chain this needs to reach MVP_COMPLETE is built directly
against a dedicated, disposable evaluation database
(data/video_encoder_evaluation/eval.db, recreated fresh on every run) --
never against the shared data/motily.db.

Usage:

    python scripts/evaluate_video_encoder.py
"""

from __future__ import annotations

import json
import shutil
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path

from app.engines.assembly_plan.models import ASSEMBLY_PLAN_ARTIFACT_TYPE
from app.engines.voice_plan.models import VOICE_PLAN_ARTIFACT_TYPE
from app.engines.visual_plan.models import VISUAL_PLAN_ARTIFACT_TYPE
from app.layer_compositor.models import LayerAnchor, LayerScale
from app.models.assembly import AssemblyPlan, AssemblySegment
from app.models.diagram import DiagramCanvas, DiagramEllipse, DiagramPoint, DiagramSpec, DiagramStyle, DiagramTextLabel
from app.renderers.timeline.builder import TimelineBuilder
from app.renderers.timeline.models import TimelineBuilderInput
from app.renderers.video.renderer import VideoRenderer
from app.renderers.video.models import VideoRendererInput
from app.renderers.visual.models import BackgroundLayerSource, CompositionSpec, OverlayLayerSource, VisualRendererInput
from app.renderers.visual.renderer import VisualRenderer
from app.audio.storage import AudioFileStore
from app.storage.artifacts import save_artifact
from app.storage.database import init_database
from app.video_encoder.storage import VideoFileStore
from app.visual.config import VisualSettings
from app.visual.fake import FakeVisualProvider
from app.visual.storage import VisualFileStore

sys.path.insert(0, str(Path(__file__).resolve().parent))
from evaluate_timeline_builder import (  # noqa: E402
    BACKGROUND_SIZE,
    CHUNK_DURATIONS_SECONDS,
    _build_project_at_mvp_complete,
    _build_voice_render_manifest,
    _fake_still_response,
)

EVAL_DIR = Path("data/video_encoder_evaluation")
EVAL_DB_PATH = EVAL_DIR / "eval.db"
OUTPUT_FILENAME = "motily_phase28_preview.mp4"


def _build_assembly_plan_no_crossfade(script_plan, voice_plan, visual_plan) -> AssemblyPlan:
    """Identical timing/segment shape to evaluate_timeline_builder.py's own
    _build_assembly_plan, except every transition is CUT or NONE (HOLD) --
    Phase 28's MVP encoder does not execute CROSSFADE (DISSOLVE) yet."""
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
                start_seconds=2.0, end_seconds=3.5, music_state="DUCK", transition_in="CUT", transition_out="CUT",
            ),
            AssemblySegment(
                segment_id="S3", script_line_ids=["L003"], voice_chunk_ids=["C003"], visual_beat_id="V3",
                start_seconds=3.5, end_seconds=6.0, music_state="LIFT", transition_in="CUT", transition_out="NONE",
            ),
        ],
    )


def _fix_diagram_canvas_to_match_background(engine, project_id, visual_plan, background_size) -> None:
    """evaluate_timeline_builder.py's own DIAGRAM beat (V2) uses a
    300x200 canvas, independent of its GENERATED_STILL background's own
    640x360 size -- fine for Phase 27's timeline-only evaluation, but
    Phase 28's strict "every visual must match the output canvas exactly"
    policy (VideoDimensionMismatchError) means V2 must be re-authored at
    the SAME canvas size before this script's VisualRenderer pass. This
    re-saves VisualPlan with a corrected V2.diagram_spec rather than
    duplicating evaluate_timeline_builder.py's entire ~150-line MVP_COMPLETE
    scaffold just to change one canvas size."""
    fixed_diagram_spec = DiagramSpec(
        canvas=DiagramCanvas(width=background_size[0], height=background_size[1], background_color=None),
        elements=[
            DiagramEllipse(
                center=DiagramPoint(x=0.5, y=0.5), radius_x=0.25, radius_y=0.3,
                style=DiagramStyle(stroke_color="#FFFFFF", stroke_width=4, fill_color="#CC000080"),
            ),
            DiagramTextLabel(
                position=DiagramPoint(x=0.5, y=0.5), text="V2 DIAGRAM", align="CENTER",
                style=DiagramStyle(text_color="#FFFFFF", font_size=18),
            ),
        ],
    )
    fixed_beats = [
        beat.model_copy(update={"diagram_spec": fixed_diagram_spec}) if beat.beat_id == "V2" else beat
        for beat in visual_plan.beats
    ]
    fixed_visual_plan = visual_plan.model_copy(update={"beats": fixed_beats})
    save_artifact(engine, project_id, VISUAL_PLAN_ARTIFACT_TYPE, fixed_visual_plan)


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
    visual_result = visual_renderer.run(
        VisualRendererInput(project_id=project.project_id, composition_specs={"V3": composition_spec})
    )

    # --- AssemblyPlan (CUT/HOLD only) + VoiceRenderManifest: hand-
    # authored, deterministic, local WAV fixtures -- no live TTS API.
    assembly_plan = _build_assembly_plan_no_crossfade(script_plan, voice_plan, visual_plan)
    save_artifact(engine, project.project_id, ASSEMBLY_PLAN_ARTIFACT_TYPE, assembly_plan)

    audio_store = AudioFileStore(EVAL_DIR / "audio")
    voice_render_manifest = _build_voice_render_manifest(script_plan, voice_plan, audio_store)
    save_artifact(engine, project.project_id, "voice_render_manifest", voice_render_manifest)

    # --- Timeline assembly: the real TimelineBuilder (Phase 27).
    timeline_builder = TimelineBuilder(engine, audio_store, visual_store)
    timeline_result = timeline_builder.run(TimelineBuilderInput(project_id=project.project_id))
    timeline_manifest = timeline_result.manifest

    # --- Video encoding: the real VideoRenderer (Phase 28), writing
    # directly to the final evaluation output path.
    video_store = VideoFileStore(EVAL_DIR)
    video_renderer = VideoRenderer(engine, audio_store, visual_store, video_store)
    video_result = video_renderer.run(VideoRendererInput(project_id=project.project_id))
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

    print(f"Evaluation project: {project.project_id}")
    print(f"Visual provider calls: {visual_result.provider_call_count} (expected 1, only for V1)")
    print(f"Timeline segments: {len(timeline_manifest.segments)}")
    print()
    print(f"Path:            {final_path.resolve()}")
    print(f"File size:       {final_path.stat().st_size} bytes")
    print(f"Video codec:     {video_stream.get('codec_name', asset.video_codec)}")
    print(f"Audio codec:     {audio_stream.get('codec_name', asset.audio_codec)}")
    print(f"Width x Height:  {asset.width}x{asset.height}")
    print(f"FPS:             {asset.fps}")
    print(f"Duration (ffprobe): {probed_duration_seconds:.3f}s")
    print(f"Duration (asset):   {asset.duration_ms / 1000:.3f}s")
    print(f"Timeline duration:  {timeline_manifest.total_duration_ms / 1000:.3f}s")
    print(f"FFmpeg version:  {_ffmpeg_version()}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
