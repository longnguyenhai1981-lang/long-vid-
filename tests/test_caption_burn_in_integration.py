"""Phase 31 opt-in integration test: a real local ffmpeg caption burn-in.

Skipped cleanly when ffmpeg/ffprobe are not discoverable on this machine,
OR when the resolved ffmpeg build does not report the `subtitles` filter
-- Phase 31 must not fail solely because the local burn-in environment is
unavailable (SRT generation remains valid either way; see this phase's
own requirement #15/#26). When both ARE available (as they are in this
development environment -- ffmpeg 9.0-full_build with libass), this test
builds a real short video, a real CaptionManifest with >=3 Vietnamese
cues, a real UTF-8 SRT, and a real burned-caption MP4, then re-verifies
the result with a direct ffprobe call and extracts one frame during an
active caption for manual/visual inspection only (never OCR).
"""

from __future__ import annotations

import json
import shutil
import subprocess
import wave
from datetime import datetime, timezone
from pathlib import Path
from uuid import uuid4

import pytest

from app.captions.burn_in import CaptionBurnInRenderer
from app.captions.models import CaptionBurnInRequest, CaptionRenderSettings
from app.captions.srt import render_srt
from app.models.caption import CaptionCue, CaptionManifest


def _ffmpeg_filters_output() -> str:
    result = subprocess.run(["ffmpeg", "-hide_banner", "-filters"], capture_output=True, text=True, timeout=30)
    return result.stdout if result.returncode == 0 else ""


pytestmark = pytest.mark.skipif(
    shutil.which("ffmpeg") is None
    or shutil.which("ffprobe") is None
    or "subtitles" not in _ffmpeg_filters_output(),
    reason="ffmpeg/ffprobe/subtitles filter not available -- skipping real caption burn-in test",
)


def _write_png(path: Path, size, color) -> Path:
    from PIL import Image

    path.parent.mkdir(parents=True, exist_ok=True)
    Image.new("RGB", size, color).save(path)
    return path


def _write_wav(path: Path, duration_seconds: float, framerate: int = 8000) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    frame_count = round(duration_seconds * framerate)
    with wave.open(str(path), "wb") as wav_file:
        wav_file.setnchannels(1)
        wav_file.setsampwidth(2)
        wav_file.setframerate(framerate)
        wav_file.writeframes(b"\x00\x00" * frame_count)
    return path


def _ffprobe_json(output_path: Path) -> dict:
    result = subprocess.run(
        ["ffprobe", "-v", "error", "-print_format", "json", "-show_format", "-show_streams", str(output_path)],
        capture_output=True, text=True, timeout=60,
    )
    assert result.returncode == 0, result.stderr
    return json.loads(result.stdout)


def _cue(**overrides) -> CaptionCue:
    fields = dict(
        script_line_ids=["L001"], voice_chunk_id="C001", render_job_id="C001_T1",
    )
    fields.update(overrides)
    return CaptionCue(**fields)


def test_real_caption_burn_in_end_to_end(tmp_path):
    canvas_size = (640, 360)
    total_duration_ms = 6000

    # --- Build a real 6-second base video via real ffmpeg (3 segments,
    # no narration content needed for this test's own scope -- it only
    # exercises caption burn-in, not the full video pipeline).
    image_path = _write_png(tmp_path / "frame.png", canvas_size, (30, 90, 150))
    silent_audio = tmp_path / "silence.wav"
    _write_wav(silent_audio, total_duration_ms / 1000)
    base_video = tmp_path / "base.mp4"
    command = [
        "ffmpeg", "-y", "-hide_banner", "-loglevel", "error",
        "-loop", "1", "-t", str(total_duration_ms / 1000), "-i", str(image_path),
        "-i", str(silent_audio),
        "-c:v", "libx264", "-pix_fmt", "yuv420p", "-r", "30", "-c:a", "aac", "-shortest",
        str(base_video),
    ]
    result = subprocess.run(command, capture_output=True, text=True, timeout=60)
    assert result.returncode == 0, result.stderr

    # --- A real CaptionManifest with >= 3 Vietnamese cues.
    cues = [
        _cue(start_ms=0, end_ms=2000, duration_ms=2000, text="Xin chào, đây là phụ đề tiếng Việt."),
        _cue(start_ms=2000, end_ms=4000, duration_ms=2000, text="Kiểm tra dấu: ệ ị ọ ữ ẫ ẳ ố ơ."),
        _cue(start_ms=4000, end_ms=6000, duration_ms=2000, text="Câu cuối cùng, cảm ơn đã theo dõi."),
    ]
    manifest = CaptionManifest(
        project_id=uuid4(), timeline_manifest_id=uuid4(), script_plan_id=uuid4(),
        voice_plan_id=uuid4(), voice_render_manifest_id=uuid4(),
        total_duration_ms=total_duration_ms, cues=cues, created_at=datetime.now(timezone.utc),
    )

    # --- A real UTF-8 SRT file.
    srt_path = tmp_path / "captions.srt"
    srt_path.write_text(render_srt(manifest), encoding="utf-8", newline="")
    assert srt_path.read_text(encoding="utf-8") == render_srt(manifest)

    # --- A real burned-caption MP4.
    output_path = tmp_path / "burned.mp4"
    request = CaptionBurnInRequest(
        input_video_path=base_video, subtitle_path=srt_path, output_path=output_path,
        settings=CaptionRenderSettings(font_size=24),
    )
    burn_result = CaptionBurnInRenderer().render(request)

    assert output_path.is_file()
    assert burn_result.width == canvas_size[0]
    assert burn_result.height == canvas_size[1]
    assert burn_result.fps == 30
    assert burn_result.video_codec == "h264"
    assert burn_result.audio_codec == "aac"
    assert burn_result.audio_stream_copied is True

    probe = _ffprobe_json(output_path)
    video_streams = [s for s in probe["streams"] if s["codec_type"] == "video"]
    audio_streams = [s for s in probe["streams"] if s["codec_type"] == "audio"]
    assert len(video_streams) == 1
    assert video_streams[0]["codec_name"] == "h264"
    assert video_streams[0]["width"] == canvas_size[0]
    assert video_streams[0]["height"] == canvas_size[1]
    assert len(audio_streams) == 1
    assert audio_streams[0]["codec_name"] == "aac"

    actual_duration_seconds = float(probe["format"]["duration"])
    assert actual_duration_seconds == pytest.approx(total_duration_ms / 1000, abs=0.2)

    # --- Extract one frame during an active caption (t=1.0s, inside the
    # first cue) for manual/visual inspection only -- never OCR.
    frame_path = tmp_path / "frame_with_caption.png"
    extract_command = [
        "ffmpeg", "-y", "-hide_banner", "-loglevel", "error",
        "-ss", "1.0", "-i", str(output_path), "-frames:v", "1", str(frame_path),
    ]
    extract_result = subprocess.run(extract_command, capture_output=True, text=True, timeout=30)
    assert extract_result.returncode == 0, extract_result.stderr
    assert frame_path.is_file()
    assert frame_path.stat().st_size > 0
