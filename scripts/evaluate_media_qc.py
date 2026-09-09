"""Manual integration-evaluation utility for Phase 32: automated media
QC / production quality gate.

Reuses scripts/evaluate_captions.py's own project shape exactly (music
BED/DUCK/LIFT, a real CROSSFADE, a SLOW_ZOOM_IN, two SFX_TRIGGER events,
three locally-generated sine-tone WAV fixtures, real captions burned in
via local ffmpeg) -- a disposable local fixture project, deliberately
NOT coupled to Phase 31's own disposable evaluation database or output
path; this script owns its own fresh, disposable
data/media_qc_evaluation/eval.db. On top of that finished production
chain, this script additionally runs the real MediaQCRenderer (Phase 32)
to inspect the resulting CaptionedVideoAsset + SubtitleFileAsset and
produce exactly one MediaQCReport.

Produces:

    data/media_qc_evaluation/motily_phase32_qc_report.json

No new MP4 output path is introduced beyond what the reused production
chain already needs to produce as its own input to QC.

Usage:

    python scripts/evaluate_media_qc.py
"""

from __future__ import annotations

import json
import shutil
import subprocess
import sys
from pathlib import Path

from app.captions.models import CaptionRenderSettings
from app.captions.storage import SubtitleFileStore
from app.engines.assembly_plan.models import ASSEMBLY_PLAN_ARTIFACT_TYPE
from app.engines.visual_plan.models import VISUAL_PLAN_ARTIFACT_TYPE
from app.engines.voice_plan.models import VOICE_PLAN_ARTIFACT_TYPE
from app.layer_compositor.models import LayerAnchor, LayerScale
from app.models.timeline import VisualMotionType
from app.renderers.caption.builder import CaptionBuilder
from app.renderers.caption.models import CaptionBuilderInput
from app.renderers.media_qc.models import MediaQCRendererInput
from app.renderers.media_qc.renderer import MediaQCRenderer
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
from evaluate_captions import _build_assembly_plan_music_and_crossfade  # noqa: E402

EVAL_DIR = Path("data/media_qc_evaluation")
EVAL_DB_PATH = EVAL_DIR / "eval.db"
REPORT_FILENAME = "motily_phase32_qc_report.json"


def _ffmpeg_version() -> str:
    result = subprocess.run(["ffmpeg", "-version"], capture_output=True, text=True, timeout=30)
    return result.stdout.splitlines()[0] if result.returncode == 0 else "(unavailable)"


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

    assembly_plan = _build_assembly_plan_music_and_crossfade(script_plan, voice_plan, visual_plan)
    save_artifact(engine, project.project_id, ASSEMBLY_PLAN_ARTIFACT_TYPE, assembly_plan)

    audio_store = AudioFileStore(EVAL_DIR / "audio")
    voice_render_manifest = _build_voice_render_manifest(script_plan, voice_plan, audio_store)
    save_artifact(engine, project.project_id, "voice_render_manifest", voice_render_manifest)

    timeline_builder = TimelineBuilder(engine, audio_store, visual_store)
    timeline_result = timeline_builder.run(
        TimelineBuilderInput(
            project_id=project.project_id, visual_motions={"S1": VisualMotionType.SLOW_ZOOM_IN}
        )
    )
    timeline_manifest = timeline_result.manifest

    caption_result = CaptionBuilder(engine).run(CaptionBuilderInput(project_id=project.project_id))
    caption_manifest = caption_result.manifest

    music_path = _write_tone_wav(EVAL_DIR / "fixtures" / "TEST_TONE_music_bed.wav", 2.5, 196.0)
    whoosh_path = _write_tone_wav(EVAL_DIR / "fixtures" / "TEST_TONE_sfx_whoosh.wav", 0.35, 900.0)
    ding_path = _write_tone_wav(EVAL_DIR / "fixtures" / "TEST_TONE_sfx_ding.wav", 0.25, 1400.0)
    audio_bindings = AudioAssetBindings(
        music_bed_path=music_path, sfx_by_id={"whoosh": whoosh_path, "ding": ding_path}
    )

    video_store = VideoFileStore(EVAL_DIR)
    video_renderer = VideoRenderer(engine, audio_store, visual_store, video_store)
    video_result = video_renderer.run(
        VideoRendererInput(project_id=project.project_id, audio_bindings=audio_bindings)
    )
    encoded_asset = video_result.asset

    subtitle_store = SubtitleFileStore(EVAL_DIR)
    subtitle_renderer = SubtitleRenderer(engine, subtitle_store, video_store)
    subtitle_result = subtitle_renderer.run(
        SubtitleRendererInput(project_id=project.project_id, burn_in_settings=CaptionRenderSettings())
    )
    subtitle_asset = subtitle_result.subtitle_asset
    captioned_asset = subtitle_result.captioned_video_asset

    # --- Phase 32: the real MediaQCRenderer, inspecting the finished
    # captioned deliverable + its subtitle sidecar.
    qc_renderer = MediaQCRenderer(engine, video_store, subtitle_store)
    qc_result = qc_renderer.run(MediaQCRendererInput(project_id=project.project_id))
    report = qc_result.report

    video_path = video_store.root / (captioned_asset.file_path if captioned_asset else encoded_asset.file_path)

    report_dict = {
        "id": str(report.id),
        "project_id": str(report.project_id),
        "source_video_asset_id": str(report.source_video_asset_id),
        "timeline_manifest_id": str(report.timeline_manifest_id),
        "caption_manifest_id": str(report.caption_manifest_id) if report.caption_manifest_id else None,
        "subtitle_file_asset_id": str(report.subtitle_file_asset_id) if report.subtitle_file_asset_id else None,
        "overall_status": report.overall_status.value,
        "ready_for_human_review": report.ready_for_human_review,
        "created_at": report.created_at.isoformat(),
        "checks": [
            {
                "check_id": check.check_id,
                "status": check.status.value,
                "message": check.message,
                "measured_value": check.measured_value,
                "expected_value": check.expected_value,
                "details": check.details,
            }
            for check in report.checks
        ],
        "summary": {
            "video_path": str(video_path.resolve()),
            "video_duration_ms": encoded_asset.duration_ms,
            "timeline_duration_ms": timeline_manifest.total_duration_ms,
            "subtitle_cue_count": len(caption_manifest.cues),
            "warning_count": sum(1 for c in report.checks if c.status.value == "WARN"),
            "failure_count": sum(1 for c in report.checks if c.status.value == "FAIL"),
        },
    }

    report_path = EVAL_DIR / REPORT_FILENAME
    with open(report_path, "w", encoding="utf-8") as f:
        json.dump(report_dict, f, indent=2, ensure_ascii=False)
        f.write("\n")

    print(f"Evaluation project: {project.project_id}")
    print()
    print(f"{'check_id':<32} {'status':<6} {'measured':<40} {'expected'}")
    for check in report.checks:
        print(f"{check.check_id:<32} {check.status.value:<6} {str(check.measured_value):<40} {check.expected_value}")
    print()
    print(f"overall_status:          {report.overall_status.value}")
    print(f"ready_for_human_review:  {report.ready_for_human_review}")
    print(f"video path:              {video_path.resolve()}")
    print(f"video duration:          {encoded_asset.duration_ms}ms")
    print(f"timeline duration:       {timeline_manifest.total_duration_ms}ms")
    print(f"subtitle cue count:      {len(caption_manifest.cues)}")
    print(f"warning count:           {report_dict['summary']['warning_count']}")
    print(f"failure count:           {report_dict['summary']['failure_count']}")
    print()
    print(f"MediaQCReport JSON:      {report_path.resolve()}")
    print(f"FFmpeg version:          {_ffmpeg_version()}")

    if report.overall_status.value == "WARN":
        warn_checks = [c for c in report.checks if c.status.value == "WARN"]
        print()
        print("WARN explanation (conservative rule, not a defect):")
        for check in warn_checks:
            print(f"  {check.check_id}: {check.message}")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
