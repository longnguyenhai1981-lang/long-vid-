"""Phase 28 focused tests: video-encoder contract validation
(app/video_encoder/models.py).

Pure pydantic-level tests -- no subprocess, no filesystem I/O.
"""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from app.video_encoder.models import (
    VideoEncodeRequest,
    VideoEncodingSettings,
    VideoNarrationClip,
    VideoSegmentInput,
)


def _settings(**overrides) -> VideoEncodingSettings:
    fields = dict(width=1920, height=1080)
    fields.update(overrides)
    return VideoEncodingSettings(**fields)


def _segment(**overrides) -> VideoSegmentInput:
    fields = dict(
        segment_id="S1", image_path="a.png", duration_ms=1000,
        narration_clips=[VideoNarrationClip(file_path="a.wav")],
        transition_in="HOLD", transition_out="CUT",
    )
    fields.update(overrides)
    return VideoSegmentInput(**fields)


def _request(**overrides) -> VideoEncodeRequest:
    fields = dict(segments=[_segment()], settings=_settings(), output_path="out.mp4")
    fields.update(overrides)
    return VideoEncodeRequest(**fields)


# ---------------------------------------------------------------------------
# VideoEncodingSettings
# ---------------------------------------------------------------------------


def test_default_fps_is_30():
    assert _settings().fps == 30


def test_default_codecs_and_pixel_format():
    settings = _settings()
    assert settings.video_codec == "libx264"
    assert settings.audio_codec == "aac"
    assert settings.pixel_format == "yuv420p"


def test_invalid_fps_rejected():
    with pytest.raises(ValidationError):
        _settings(fps=0)
    with pytest.raises(ValidationError):
        _settings(fps=-5)


def test_invalid_width_height_rejected():
    with pytest.raises(ValidationError):
        VideoEncodingSettings(width=0, height=1080)
    with pytest.raises(ValidationError):
        VideoEncodingSettings(width=1920, height=-1)


def test_blank_codec_rejected():
    with pytest.raises(ValidationError):
        _settings(video_codec="  ")


def test_explicit_ffmpeg_path_accepted():
    settings = _settings(ffmpeg_path="/usr/bin/ffmpeg")
    assert str(settings.ffmpeg_path) == "/usr/bin/ffmpeg" or "ffmpeg" in str(settings.ffmpeg_path)


# ---------------------------------------------------------------------------
# VideoSegmentInput
# ---------------------------------------------------------------------------


def test_segment_zero_duration_rejected():
    with pytest.raises(ValidationError):
        _segment(duration_ms=0)


def test_segment_negative_duration_rejected():
    with pytest.raises(ValidationError):
        _segment(duration_ms=-100)


def test_segment_requires_at_least_one_narration_clip():
    with pytest.raises(ValidationError):
        _segment(narration_clips=[])


def test_segment_blank_id_rejected():
    with pytest.raises(ValidationError):
        _segment(segment_id="   ")


def test_segment_accepts_crossfade_at_model_level():
    """CROSSFADE is a syntactically valid TimelineTransitionType -- rejecting
    it is VideoEncoder's own runtime policy (UnsupportedVideoTransitionError),
    not a structural model invariant, so it must not be rejected here."""
    segment = _segment(transition_in="CROSSFADE")
    assert segment.transition_in.value == "CROSSFADE"


# ---------------------------------------------------------------------------
# VideoEncodeRequest: valid .mp4 output / invalid extension rejection
# ---------------------------------------------------------------------------


def test_valid_mp4_output_accepted():
    request = _request(output_path="out.mp4")
    assert str(request.output_path).endswith(".mp4")


@pytest.mark.parametrize("output_path", ["out.mov", "out.avi", "out.mkv", "out", "out.MP4X"])
def test_invalid_output_extension_rejected(output_path):
    with pytest.raises(ValidationError):
        _request(output_path=output_path)


def test_uppercase_mp4_extension_accepted():
    request = _request(output_path="OUT.MP4")
    assert request.output_path.suffix.lower() == ".mp4"


def test_request_requires_at_least_one_segment():
    with pytest.raises(ValidationError):
        _request(segments=[])


def test_total_duration_ms_sums_segments():
    request = _request(
        segments=[
            _segment(segment_id="S1", duration_ms=1000),
            _segment(segment_id="S2", duration_ms=1500),
        ]
    )
    assert request.total_duration_ms == 2500


# ---------------------------------------------------------------------------
# VideoEncodeResult
# ---------------------------------------------------------------------------


def test_encode_result_model_validation():
    from app.video_encoder.models import VideoEncodeResult

    result = VideoEncodeResult(
        output_path="out.mp4", width=1920, height=1080, fps=30, duration_ms=5000,
        file_size_bytes=12345, video_codec="libx264", audio_codec="aac", ffmpeg_command=["ffmpeg"],
    )
    assert result.duration_ms == 5000


@pytest.mark.parametrize(
    "field,value", [("width", 0), ("height", 0), ("fps", 0), ("duration_ms", 0), ("file_size_bytes", 0)]
)
def test_encode_result_rejects_non_positive_fields(field, value):
    from app.video_encoder.models import VideoEncodeResult

    fields = dict(
        output_path="out.mp4", width=1920, height=1080, fps=30, duration_ms=5000,
        file_size_bytes=12345, video_codec="libx264", audio_codec="aac", ffmpeg_command=["ffmpeg"],
    )
    fields[field] = value
    with pytest.raises(ValidationError):
        VideoEncodeResult(**fields)


# ---------------------------------------------------------------------------
# Phase 29: crossfade_duration_ms / motion
# ---------------------------------------------------------------------------


def test_default_crossfade_duration_is_300ms():
    assert _settings().crossfade_duration_ms == 300


def test_custom_crossfade_duration_accepted():
    assert _settings(crossfade_duration_ms=150).crossfade_duration_ms == 150


def test_non_positive_crossfade_duration_rejected():
    with pytest.raises(ValidationError):
        _settings(crossfade_duration_ms=0)
    with pytest.raises(ValidationError):
        _settings(crossfade_duration_ms=-10)


def test_segment_default_motion_is_static():
    from app.models.timeline import VisualMotionType

    assert _segment().motion is VisualMotionType.STATIC


@pytest.mark.parametrize("motion", ["STATIC", "SLOW_ZOOM_IN", "SLOW_ZOOM_OUT", "PAN_LEFT", "PAN_RIGHT"])
def test_segment_accepts_every_motion_vocabulary_member(motion):
    assert _segment(motion=motion).motion.value == motion


def test_encode_result_default_crossfade_and_motion_are_empty():
    from app.video_encoder.models import VideoEncodeResult

    result = VideoEncodeResult(
        output_path="out.mp4", width=1920, height=1080, fps=30, duration_ms=5000,
        file_size_bytes=12345, video_codec="libx264", audio_codec="aac", ffmpeg_command=["ffmpeg"],
    )
    assert result.crossfade_count == 0
    assert result.motion_profile_used == []


def test_encode_result_negative_crossfade_count_rejected():
    from app.video_encoder.models import VideoEncodeResult

    with pytest.raises(ValidationError):
        VideoEncodeResult(
            output_path="out.mp4", width=1920, height=1080, fps=30, duration_ms=5000,
            file_size_bytes=12345, video_codec="libx264", audio_codec="aac", ffmpeg_command=["ffmpeg"],
            crossfade_count=-1,
        )
