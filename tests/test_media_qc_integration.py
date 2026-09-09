"""Phase 32 opt-in integration tests: MediaQCInspector against real local
ffmpeg-produced media.

Skipped cleanly when ffmpeg/ffprobe are not discoverable on this machine
-- Phase 32 must not fail solely because system ffmpeg is absent. When
available (as it is in this development environment), these tests build
real short MP4 fixtures (healthy, silent, all-black, frozen/static,
corrupt) via real ffmpeg and run the real MediaQCInspector (real
FFprobeClient/FrameSampler/AudioAnalyzer, no fakes) against them.
"""

from __future__ import annotations

import shutil
import subprocess
from pathlib import Path
from uuid import uuid4

import pytest

from app.media_qc.inspector import MediaQCInspector
from app.media_qc.models import MediaQCRequest, QCStatus

pytestmark = pytest.mark.skipif(
    shutil.which("ffmpeg") is None or shutil.which("ffprobe") is None,
    reason="ffmpeg/ffprobe not found on PATH -- skipping real media QC integration test",
)

CANVAS = (640, 360)


def _run_ffmpeg(args: list[str]) -> None:
    result = subprocess.run(["ffmpeg", "-y", "-hide_banner", "-loglevel", "error", *args], capture_output=True, text=True)
    assert result.returncode == 0, result.stderr


def _build_changing_video(path: Path, duration_s: int = 6) -> None:
    third = duration_s // 3
    _run_ffmpeg(
        [
            "-f", "lavfi", "-i", f"color=c=red:s={CANVAS[0]}x{CANVAS[1]}:d={third}",
            "-f", "lavfi", "-i", f"color=c=green:s={CANVAS[0]}x{CANVAS[1]}:d={third}",
            "-f", "lavfi", "-i", f"color=c=blue:s={CANVAS[0]}x{CANVAS[1]}:d={third}",
            "-f", "lavfi", "-i", f"sine=frequency=440:duration={duration_s}",
            "-filter_complex", "[0:v][1:v][2:v]concat=n=3:v=1:a=0[v]",
            "-map", "[v]", "-map", "3:a",
            "-c:v", "libx264", "-pix_fmt", "yuv420p", "-r", "30", "-c:a", "aac",
            str(path),
        ]
    )


def _build_silent_video(path: Path, duration_s: int = 6) -> None:
    third = duration_s // 3
    _run_ffmpeg(
        [
            "-f", "lavfi", "-i", f"color=c=red:s={CANVAS[0]}x{CANVAS[1]}:d={third}",
            "-f", "lavfi", "-i", f"color=c=green:s={CANVAS[0]}x{CANVAS[1]}:d={third}",
            "-f", "lavfi", "-i", f"color=c=blue:s={CANVAS[0]}x{CANVAS[1]}:d={third}",
            "-f", "lavfi", "-i", f"anullsrc=r=8000:cl=mono:duration={duration_s}",
            "-filter_complex", "[0:v][1:v][2:v]concat=n=3:v=1:a=0[v]",
            "-map", "[v]", "-map", "3:a",
            "-c:v", "libx264", "-pix_fmt", "yuv420p", "-r", "30", "-c:a", "aac",
            str(path),
        ]
    )


def _build_solid_color_video(path: Path, color: str, duration_s: int = 6) -> None:
    _run_ffmpeg(
        [
            "-f", "lavfi", "-i", f"color=c={color}:s={CANVAS[0]}x{CANVAS[1]}:d={duration_s}",
            "-f", "lavfi", "-i", f"sine=frequency=440:duration={duration_s}",
            "-c:v", "libx264", "-pix_fmt", "yuv420p", "-r", "30", "-c:a", "aac",
            str(path),
        ]
    )


def _request(video_path: Path, **overrides) -> MediaQCRequest:
    fields = dict(
        project_id=uuid4(), source_video_asset_id=uuid4(), timeline_manifest_id=uuid4(),
        video_path=video_path, expected_video_codec="h264", expected_audio_codec="aac",
        expected_pixel_format="yuv420p", expected_fps=30.0, expected_width=CANVAS[0],
        expected_height=CANVAS[1], expected_duration_ms=6000,
    )
    fields.update(overrides)
    return MediaQCRequest(**fields)


def test_healthy_video_passes_every_check(tmp_path):
    video = tmp_path / "healthy.mp4"
    _build_changing_video(video)
    report = MediaQCInspector().inspect(_request(video))
    assert report.overall_status is QCStatus.PASS
    assert report.ready_for_human_review is True
    for check in report.checks:
        assert check.status is QCStatus.PASS, f"{check.check_id}: {check.message}"


def test_missing_video_fails_report_not_exception(tmp_path):
    report = MediaQCInspector().inspect(_request(tmp_path / "does_not_exist.mp4"))
    assert report.overall_status is QCStatus.FAIL
    assert report.checks[0].check_id == "QC_VIDEO_EXISTS"


def test_corrupt_file_named_mp4_fails_decode(tmp_path):
    corrupt = tmp_path / "corrupt.mp4"
    corrupt.write_bytes(b"this is not a real video container")
    report = MediaQCInspector().inspect(_request(corrupt))
    assert report.overall_status is QCStatus.FAIL
    decode_check = next(c for c in report.checks if c.check_id == "QC_VIDEO_DECODE")
    assert decode_check.status is QCStatus.FAIL


def test_silent_audio_fails_qc_audio_silence(tmp_path):
    video = tmp_path / "silent.mp4"
    _build_silent_video(video)
    report = MediaQCInspector().inspect(_request(video))
    silence_check = next(c for c in report.checks if c.check_id == "QC_AUDIO_SILENCE")
    assert silence_check.status is QCStatus.FAIL
    assert report.overall_status is QCStatus.FAIL


def test_wrong_duration_expectation_fails(tmp_path):
    video = tmp_path / "healthy.mp4"
    _build_changing_video(video)
    # Deliberately claim the timeline expected 10 seconds, not 6.
    report = MediaQCInspector().inspect(_request(video, expected_duration_ms=10_000))
    duration_check = next(c for c in report.checks if c.check_id == "QC_VIDEO_DURATION")
    assert duration_check.status is QCStatus.FAIL
    assert report.overall_status is QCStatus.FAIL


def test_all_black_video_fails_qc_video_black(tmp_path):
    video = tmp_path / "black.mp4"
    _build_solid_color_video(video, "black")
    report = MediaQCInspector().inspect(_request(video))
    black_check = next(c for c in report.checks if c.check_id == "QC_VIDEO_BLACK")
    assert black_check.status is QCStatus.FAIL
    assert report.overall_status is QCStatus.FAIL


def test_static_single_color_video_warns_frozen_not_fail(tmp_path):
    """A legitimate static/limited-motion presentation (this channel's
    own style) must WARN, never FAIL, on whole-program sameness."""
    video = tmp_path / "frozen.mp4"
    _build_solid_color_video(video, "blue")
    report = MediaQCInspector().inspect(_request(video))
    frozen_check = next(c for c in report.checks if c.check_id == "QC_VIDEO_FROZEN")
    assert frozen_check.status is QCStatus.WARN
    black_check = next(c for c in report.checks if c.check_id == "QC_VIDEO_BLACK")
    assert black_check.status is QCStatus.PASS  # blue is not black
    assert report.overall_status is QCStatus.WARN
    assert report.ready_for_human_review is True


def test_codec_mismatch_expectation_fails(tmp_path):
    video = tmp_path / "healthy.mp4"
    _build_changing_video(video)
    report = MediaQCInspector().inspect(_request(video, expected_video_codec="vp9"))
    codec_check = next(c for c in report.checks if c.check_id == "QC_VIDEO_CODEC")
    assert codec_check.status is QCStatus.FAIL


def test_canvas_mismatch_expectation_fails(tmp_path):
    video = tmp_path / "healthy.mp4"
    _build_changing_video(video)
    report = MediaQCInspector().inspect(_request(video, expected_width=1280, expected_height=720))
    canvas_check = next(c for c in report.checks if c.check_id == "QC_VIDEO_CANVAS")
    assert canvas_check.status is QCStatus.FAIL
