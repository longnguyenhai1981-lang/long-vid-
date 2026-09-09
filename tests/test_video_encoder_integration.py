"""Phase 28/29/30 opt-in integration tests: real local ffmpeg encodes.

Skipped cleanly when ffmpeg/ffprobe are not discoverable on this machine
-- Phase 28 must not fail solely because system ffmpeg is absent (see
this phase's own requirement #24). When ffmpeg IS available (as it is in
this development environment), these tests build real static PNG frames
and real short WAV narration clips, encode a real MP4 via the real
VideoEncoder (no fake runner), and re-verify the result with a direct
ffprobe call. Phase 29 adds a real CROSSFADE encode and a real
motion-lite (one segment per VisualMotionType member) encode alongside
Phase 28's original CUT-only encode. Phase 30 adds real music/SFX mix
encodes (narration+music, narration+SFX, and a full mix combining music
DUCK/LIFT, SFX, CROSSFADE, and motion-lite) using locally generated
sine-tone WAV fixtures -- never real/copyrighted audio.
"""

from __future__ import annotations

import json
import math
import shutil
import struct
import subprocess
import wave
from pathlib import Path

import pytest

from app.models.timeline import TimelineCue, TimelineCueType
from app.video_encoder.encoder import VideoEncoder
from app.video_encoder.models import (
    AudioAssetBindings,
    VideoEncodeRequest,
    VideoEncodingSettings,
    VideoNarrationClip,
    VideoSegmentInput,
)

pytestmark = pytest.mark.skipif(
    shutil.which("ffmpeg") is None or shutil.which("ffprobe") is None,
    reason="ffmpeg/ffprobe not found on PATH -- skipping real-encode integration test",
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


def _write_tone_wav(path: Path, duration_seconds: float, freq_hz: float, framerate: int = 8000) -> Path:
    """A simple sine-tone WAV -- technical test audio only, never real or
    copyrighted music/SFX."""
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


def _ffprobe_json(output_path: Path) -> dict:
    result = subprocess.run(
        [
            "ffprobe", "-v", "error", "-print_format", "json",
            "-show_format", "-show_streams", str(output_path),
        ],
        capture_output=True, text=True, timeout=60,
    )
    assert result.returncode == 0, result.stderr
    return json.loads(result.stdout)


def test_real_three_segment_encode_produces_playable_mp4(tmp_path):
    canvas_size = (320, 240)
    durations_seconds = [1.0, 1.5, 0.8]
    colors = [(200, 0, 0), (0, 200, 0), (0, 0, 200)]

    segments = []
    for i, (duration_seconds, color) in enumerate(zip(durations_seconds, colors)):
        image_path = _write_png(tmp_path / f"frame{i}.png", canvas_size, color)
        audio_path = _write_wav(tmp_path / f"narration{i}.wav", duration_seconds)
        segments.append(
            VideoSegmentInput(
                segment_id=f"S{i}", image_path=image_path, duration_ms=round(duration_seconds * 1000),
                narration_clips=[VideoNarrationClip(file_path=audio_path)],
                transition_in="CUT", transition_out="CUT",
            )
        )

    output_path = tmp_path / "integration.mp4"
    request = VideoEncodeRequest(
        segments=segments,
        settings=VideoEncodingSettings(width=canvas_size[0], height=canvas_size[1], fps=30),
        output_path=output_path,
    )

    result = VideoEncoder().encode(request)

    assert output_path.is_file()
    assert result.file_size_bytes == output_path.stat().st_size
    assert result.file_size_bytes > 0

    probe = _ffprobe_json(output_path)
    video_streams = [s for s in probe["streams"] if s["codec_type"] == "video"]
    audio_streams = [s for s in probe["streams"] if s["codec_type"] == "audio"]

    assert len(video_streams) == 1
    assert video_streams[0]["codec_name"] == "h264"
    assert video_streams[0]["width"] == canvas_size[0]
    assert video_streams[0]["height"] == canvas_size[1]
    assert video_streams[0]["r_frame_rate"] == "30/1"
    assert video_streams[0]["pix_fmt"] == "yuv420p"

    assert len(audio_streams) == 1
    assert audio_streams[0]["codec_name"] == "aac"

    expected_duration_seconds = sum(durations_seconds)
    actual_duration_seconds = float(probe["format"]["duration"])
    assert actual_duration_seconds == pytest.approx(expected_duration_seconds, abs=0.15)

    expected_total_ms = sum(s.duration_ms for s in segments)
    assert result.duration_ms == expected_total_ms


def test_real_crossfade_encode_preserves_total_duration(tmp_path):
    """Phase 29: a real CROSSFADE join, verified end to end with real
    ffmpeg -- the combined output duration must still equal the naive sum
    of both segments' own authored durations (never shortened by the
    dissolve's own overlap)."""
    canvas_size = (320, 240)
    durations_seconds = [2.0, 2.0, 2.0]
    colors = [(200, 0, 0), (0, 200, 0), (0, 0, 200)]

    segments = []
    for i, (duration_seconds, color) in enumerate(zip(durations_seconds, colors)):
        image_path = _write_png(tmp_path / f"frame{i}.png", canvas_size, color)
        audio_path = _write_wav(tmp_path / f"narration{i}.wav", duration_seconds)
        segments.append(
            VideoSegmentInput(
                segment_id=f"S{i}", image_path=image_path, duration_ms=round(duration_seconds * 1000),
                narration_clips=[VideoNarrationClip(file_path=audio_path)],
                transition_in="CUT",
                transition_out="CROSSFADE" if i == 0 else "CUT",
            )
        )

    output_path = tmp_path / "crossfade_integration.mp4"
    request = VideoEncodeRequest(
        segments=segments,
        settings=VideoEncodingSettings(width=canvas_size[0], height=canvas_size[1], fps=30, crossfade_duration_ms=300),
        output_path=output_path,
    )

    result = VideoEncoder().encode(request)

    assert result.crossfade_count == 1
    assert output_path.is_file()

    probe = _ffprobe_json(output_path)
    video_streams = [s for s in probe["streams"] if s["codec_type"] == "video"]
    assert video_streams[0]["width"] == canvas_size[0]
    assert video_streams[0]["height"] == canvas_size[1]

    expected_total_ms = sum(s.duration_ms for s in segments)
    assert result.duration_ms == expected_total_ms
    actual_duration_seconds = float(probe["format"]["duration"])
    assert actual_duration_seconds == pytest.approx(expected_total_ms / 1000, abs=0.15)


def test_real_mixed_cut_then_crossfade_with_zoom_motion(tmp_path):
    """Regression for a real timebase mismatch found via manual evaluation:
    zoompan (used for SLOW_ZOOM_IN/OUT) carries a different internal
    timebase than a plain scale/crop chain, and a CUT's `concat` node
    silently tolerates that mismatch while a later `xfade` node does not
    -- so a chain shaped [zoom]--CUT-->[static]--CROSSFADE-->[pan] (concat
    feeding into xfade, with zoompan upstream of the concat) is the
    narrowest real repro. See encoder.py's settb=AVTB comment for the
    fix."""
    canvas_size = (320, 240)
    segments = [
        VideoSegmentInput(
            segment_id="S1", image_path=_write_png(tmp_path / "frame0.png", canvas_size, (200, 0, 0)),
            duration_ms=2000,
            narration_clips=[VideoNarrationClip(file_path=_write_wav(tmp_path / "n0.wav", 2.0))],
            transition_in="CUT", transition_out="CUT", motion="SLOW_ZOOM_IN",
        ),
        VideoSegmentInput(
            segment_id="S2", image_path=_write_png(tmp_path / "frame1.png", canvas_size, (0, 200, 0)),
            duration_ms=1500,
            narration_clips=[VideoNarrationClip(file_path=_write_wav(tmp_path / "n1.wav", 1.5))],
            transition_in="CUT", transition_out="CROSSFADE",
        ),
        VideoSegmentInput(
            segment_id="S3", image_path=_write_png(tmp_path / "frame2.png", canvas_size, (0, 0, 200)),
            duration_ms=1500,
            narration_clips=[VideoNarrationClip(file_path=_write_wav(tmp_path / "n2.wav", 1.5))],
            transition_in="CUT", transition_out="CUT", motion="PAN_RIGHT",
        ),
    ]
    output_path = tmp_path / "mixed_integration.mp4"
    request = VideoEncodeRequest(
        segments=segments,
        settings=VideoEncodingSettings(width=canvas_size[0], height=canvas_size[1], fps=30, crossfade_duration_ms=300),
        output_path=output_path,
    )

    result = VideoEncoder().encode(request)

    assert result.crossfade_count == 1
    assert sorted(m.value for m in result.motion_profile_used) == ["PAN_RIGHT", "SLOW_ZOOM_IN"]
    probe = _ffprobe_json(output_path)
    video_streams = [s for s in probe["streams"] if s["codec_type"] == "video"]
    assert video_streams[0]["width"] == canvas_size[0]
    assert video_streams[0]["height"] == canvas_size[1]
    expected_total_ms = sum(s.duration_ms for s in segments)
    assert result.duration_ms == expected_total_ms


def test_real_motion_lite_encode_preserves_canvas_and_duration(tmp_path):
    """Phase 29: one segment per motion-lite vocabulary member, verified
    end to end with real ffmpeg -- canvas dimensions and total duration
    must be exactly preserved regardless of which motion is applied."""
    canvas_size = (320, 240)
    motions = ["STATIC", "SLOW_ZOOM_IN", "SLOW_ZOOM_OUT", "PAN_LEFT", "PAN_RIGHT"]
    colors = [(200, 0, 0), (0, 200, 0), (0, 0, 200), (200, 200, 0), (200, 0, 200)]

    segments = []
    for i, (motion, color) in enumerate(zip(motions, colors)):
        image_path = _write_png(tmp_path / f"frame{i}.png", canvas_size, color)
        audio_path = _write_wav(tmp_path / f"narration{i}.wav", 1.0)
        segments.append(
            VideoSegmentInput(
                segment_id=f"S{i}", image_path=image_path, duration_ms=1000,
                narration_clips=[VideoNarrationClip(file_path=audio_path)],
                transition_in="CUT", transition_out="CUT", motion=motion,
            )
        )

    output_path = tmp_path / "motion_integration.mp4"
    request = VideoEncodeRequest(
        segments=segments,
        settings=VideoEncodingSettings(width=canvas_size[0], height=canvas_size[1], fps=30),
        output_path=output_path,
    )

    result = VideoEncoder().encode(request)

    assert sorted(m.value for m in result.motion_profile_used) == sorted(
        ["SLOW_ZOOM_IN", "SLOW_ZOOM_OUT", "PAN_LEFT", "PAN_RIGHT"]
    )
    assert output_path.is_file()

    probe = _ffprobe_json(output_path)
    video_streams = [s for s in probe["streams"] if s["codec_type"] == "video"]
    assert video_streams[0]["width"] == canvas_size[0]
    assert video_streams[0]["height"] == canvas_size[1]

    expected_total_ms = sum(s.duration_ms for s in segments)
    assert result.duration_ms == expected_total_ms
    actual_duration_seconds = float(probe["format"]["duration"])
    assert actual_duration_seconds == pytest.approx(expected_total_ms / 1000, abs=0.15)


def test_real_narration_plus_music_encode(tmp_path):
    """Phase 30 (A): a short WAV narration and a short looping music bed,
    real encode, ffprobe confirms AAC and the timeline's own duration."""
    canvas_size = (320, 240)
    image_path = _write_png(tmp_path / "frame.png", canvas_size, (100, 100, 200))
    narration_path = _write_wav(tmp_path / "narration.wav", 2.0)
    music_path = _write_tone_wav(tmp_path / "music.wav", 0.7, 220)  # shorter than the bed -- must loop

    segment = VideoSegmentInput(
        segment_id="S1", image_path=image_path, duration_ms=2000,
        narration_clips=[VideoNarrationClip(file_path=narration_path)],
        transition_in="CUT", transition_out="CUT",
    )
    cues = [TimelineCue(timestamp_ms=0, cue_type=TimelineCueType.MUSIC_BED_START)]
    bindings = AudioAssetBindings(music_bed_path=music_path)

    output_path = tmp_path / "music_integration.mp4"
    request = VideoEncodeRequest(
        segments=[segment],
        settings=VideoEncodingSettings(width=canvas_size[0], height=canvas_size[1], fps=30),
        output_path=output_path, cues=cues, audio_bindings=bindings,
    )

    result = VideoEncoder().encode(request)
    assert result.has_music
    assert output_path.is_file()

    probe = _ffprobe_json(output_path)
    audio_streams = [s for s in probe["streams"] if s["codec_type"] == "audio"]
    assert audio_streams[0]["codec_name"] == "aac"
    assert result.duration_ms == 2000
    actual_duration_seconds = float(probe["format"]["duration"])
    assert actual_duration_seconds == pytest.approx(2.0, abs=0.15)


def test_real_narration_plus_sfx_encode(tmp_path):
    """Phase 30 (B): at least two triggered SFX, real encode succeeds."""
    canvas_size = (320, 240)
    image_path = _write_png(tmp_path / "frame.png", canvas_size, (10, 200, 10))
    narration_path = _write_wav(tmp_path / "narration.wav", 3.0)
    whoosh_path = _write_tone_wav(tmp_path / "whoosh.wav", 0.3, 900)
    ding_path = _write_tone_wav(tmp_path / "ding.wav", 0.2, 1400)

    segment = VideoSegmentInput(
        segment_id="S1", image_path=image_path, duration_ms=3000,
        narration_clips=[VideoNarrationClip(file_path=narration_path)],
        transition_in="CUT", transition_out="CUT",
    )
    cues = [
        TimelineCue(timestamp_ms=500, cue_type=TimelineCueType.SFX_TRIGGER, reference="whoosh"),
        TimelineCue(timestamp_ms=2000, cue_type=TimelineCueType.SFX_TRIGGER, reference="ding"),
    ]
    bindings = AudioAssetBindings(sfx_by_id={"whoosh": whoosh_path, "ding": ding_path})

    output_path = tmp_path / "sfx_integration.mp4"
    request = VideoEncodeRequest(
        segments=[segment],
        settings=VideoEncodingSettings(width=canvas_size[0], height=canvas_size[1], fps=30),
        output_path=output_path, cues=cues, audio_bindings=bindings,
    )

    result = VideoEncoder().encode(request)
    assert result.sfx_event_count == 2
    assert output_path.is_file()
    probe = _ffprobe_json(output_path)
    assert any(s["codec_type"] == "audio" for s in probe["streams"])
    assert result.duration_ms == 3000


def test_real_full_mix_encode(tmp_path):
    """Phase 30 (C): narration + music with DUCK/LIFT + SFX + CROSSFADE +
    one motion-lite segment, real encode succeeds with the correct final
    duration."""
    canvas_size = (320, 240)
    durations_ms = [2000, 2000, 2000]
    colors = [(200, 0, 0), (0, 200, 0), (0, 0, 200)]

    segments = []
    for i, (duration_ms, color) in enumerate(zip(durations_ms, colors)):
        image_path = _write_png(tmp_path / f"frame{i}.png", canvas_size, color)
        narration_path = _write_wav(tmp_path / f"narration{i}.wav", duration_ms / 1000)
        segments.append(
            VideoSegmentInput(
                segment_id=f"S{i}", image_path=image_path, duration_ms=duration_ms,
                narration_clips=[VideoNarrationClip(file_path=narration_path)],
                transition_in="CUT",
                transition_out="CROSSFADE" if i == 0 else "CUT",
                motion="SLOW_ZOOM_IN" if i == 2 else "STATIC",
            )
        )
    total_ms = sum(durations_ms)

    music_path = _write_tone_wav(tmp_path / "music.wav", 1.5, 180)
    sfx_path = _write_tone_wav(tmp_path / "sfx.wav", 0.3, 950)

    cues = [
        TimelineCue(timestamp_ms=0, cue_type=TimelineCueType.MUSIC_BED_START),
        TimelineCue(timestamp_ms=2000, cue_type=TimelineCueType.MUSIC_DUCK),
        TimelineCue(timestamp_ms=4000, cue_type=TimelineCueType.MUSIC_LIFT),
        TimelineCue(timestamp_ms=total_ms, cue_type=TimelineCueType.MUSIC_BED_END),
        TimelineCue(timestamp_ms=1000, cue_type=TimelineCueType.SFX_TRIGGER, reference="whoosh"),
        TimelineCue(timestamp_ms=5500, cue_type=TimelineCueType.SFX_TRIGGER, reference="whoosh"),
    ]
    bindings = AudioAssetBindings(music_bed_path=music_path, sfx_by_id={"whoosh": sfx_path})

    output_path = tmp_path / "full_mix_integration.mp4"
    request = VideoEncodeRequest(
        segments=segments,
        settings=VideoEncodingSettings(width=canvas_size[0], height=canvas_size[1], fps=30, crossfade_duration_ms=300),
        output_path=output_path, cues=cues, audio_bindings=bindings,
    )

    result = VideoEncoder().encode(request)

    assert result.crossfade_count == 1
    assert result.has_music
    assert result.sfx_event_count == 2
    assert output_path.is_file()

    probe = _ffprobe_json(output_path)
    video_streams = [s for s in probe["streams"] if s["codec_type"] == "video"]
    assert video_streams[0]["width"] == canvas_size[0]
    assert video_streams[0]["height"] == canvas_size[1]
    assert result.duration_ms == total_ms
    actual_duration_seconds = float(probe["format"]["duration"])
    assert actual_duration_seconds == pytest.approx(total_ms / 1000, abs=0.15)
