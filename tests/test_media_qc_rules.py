"""Phase 32 focused tests: individual QC check functions
(app/media_qc/rules.py) -- pure functions given already-measured inputs,
no subprocess, no filesystem I/O.
"""

from __future__ import annotations

from app.captions.srt import ParsedSrtCue
from app.media_qc.models import QCStatus
from app.media_qc.probes import MediaProbeResult
from app.media_qc.rules import (
    check_audio_clipping,
    check_audio_codec,
    check_audio_silence,
    check_audio_stream_present,
    check_black_frames,
    check_canvas,
    check_duration,
    check_frozen_frames,
    check_fps,
    check_pixel_format,
    check_stream_duration_sanity,
    check_subtitle_cue_count_match,
    check_subtitle_exact_match,
    check_subtitle_text_nonempty,
    check_subtitle_timeline_bounds,
    check_subtitle_timestamps_valid,
    check_video_codec,
    check_video_decodable,
    check_video_file_exists,
    check_video_file_nonzero,
    check_video_stream_present,
)


def _probe(**overrides) -> MediaProbeResult:
    fields = dict(
        duration_ms=6000, width=640, height=360, fps=30.0, video_codec="h264",
        audio_codec="aac", pixel_format="yuv420p", video_stream_count=1, audio_stream_count=1,
    )
    fields.update(overrides)
    return MediaProbeResult(**fields)


def _cue(**overrides) -> ParsedSrtCue:
    fields = dict(index=1, start_ms=0, end_ms=1000, text="hello")
    fields.update(overrides)
    return ParsedSrtCue(**fields)


# ---------------------------------------------------------------------------
# Video: file integrity / decode / streams / codec / pixfmt (item 29)
# ---------------------------------------------------------------------------


def test_healthy_video_checks_all_pass(tmp_path):
    video = tmp_path / "video.mp4"
    video.write_bytes(b"x" * 100)
    assert check_video_file_exists(video).status is QCStatus.PASS
    assert check_video_file_nonzero(video).status is QCStatus.PASS
    assert check_video_decodable(_probe(), None).status is QCStatus.PASS
    probe = _probe()
    assert check_video_stream_present(probe).status is QCStatus.PASS
    assert check_audio_stream_present(probe).status is QCStatus.PASS
    assert check_video_codec(probe, "h264").status is QCStatus.PASS
    assert check_audio_codec(probe, "aac").status is QCStatus.PASS
    assert check_pixel_format(probe, "yuv420p").status is QCStatus.PASS
    assert check_fps(probe, 30.0, 0.01).status is QCStatus.PASS
    assert check_canvas(probe, 640, 360).status is QCStatus.PASS
    assert check_duration(probe, 6000, 100).status is QCStatus.PASS


def test_missing_video_fails_without_exception(tmp_path):
    result = check_video_file_exists(tmp_path / "missing.mp4")
    assert result.status is QCStatus.FAIL


def test_zero_byte_video_fails(tmp_path):
    zero_byte = tmp_path / "zero.mp4"
    zero_byte.write_bytes(b"")
    assert check_video_file_nonzero(zero_byte).status is QCStatus.FAIL


def test_corrupt_mp4_decode_fails():
    result = check_video_decodable(None, "moov atom not found")
    assert result.status is QCStatus.FAIL


def test_missing_video_stream_fails():
    probe = _probe(video_stream_count=0, video_codec=None)
    assert check_video_stream_present(probe).status is QCStatus.FAIL


def test_missing_audio_stream_fails():
    probe = _probe(audio_stream_count=0, audio_codec=None)
    assert check_audio_stream_present(probe).status is QCStatus.FAIL


def test_codec_mismatch_fails():
    probe = _probe(video_codec="vp9")
    assert check_video_codec(probe, "h264").status is QCStatus.FAIL


def test_audio_codec_mismatch_fails():
    probe = _probe(audio_codec="mp3")
    assert check_audio_codec(probe, "aac").status is QCStatus.FAIL


def test_fps_mismatch_fails():
    probe = _probe(fps=24.0)
    assert check_fps(probe, 30.0, 0.01).status is QCStatus.FAIL


def test_fps_within_tolerance_passes():
    probe = _probe(fps=29.995)
    assert check_fps(probe, 30.0, 0.01).status is QCStatus.PASS


def test_ntsc_rational_fps_matches_expected():
    probe = _probe(fps=30000 / 1001)
    assert check_fps(probe, 30000 / 1001, 0.01).status is QCStatus.PASS


def test_canvas_mismatch_fails():
    probe = _probe(width=1280, height=720)
    assert check_canvas(probe, 640, 360).status is QCStatus.FAIL


def test_canvas_odd_dimension_fails():
    probe = _probe(width=641, height=360)
    result = check_canvas(probe, 641, 360)
    assert result.status is QCStatus.FAIL
    assert "odd" in result.message.lower()


def test_duration_mismatch_fails():
    probe = _probe(duration_ms=5000)
    assert check_duration(probe, 6000, 100).status is QCStatus.FAIL


def test_duration_within_tolerance_passes():
    probe = _probe(duration_ms=6050)
    assert check_duration(probe, 6000, 100).status is QCStatus.PASS


def test_pixel_format_mismatch_fails():
    probe = _probe(pixel_format="yuv444p")
    assert check_pixel_format(probe, "yuv420p").status is QCStatus.FAIL


def test_stream_duration_sanity_zero_fails():
    probe = _probe(duration_ms=0)
    assert check_stream_duration_sanity(probe, 6000).status is QCStatus.FAIL


def test_stream_duration_sanity_implausibly_large_fails():
    probe = _probe(duration_ms=999_999)
    assert check_stream_duration_sanity(probe, 6000).status is QCStatus.FAIL


def test_stream_duration_sanity_normal_passes():
    probe = _probe(duration_ms=6000)
    assert check_stream_duration_sanity(probe, 6000).status is QCStatus.PASS


# ---------------------------------------------------------------------------
# Visual sampling (item 30) -- no OCR, no CV models
# ---------------------------------------------------------------------------


def test_all_black_samples_fail():
    result = check_black_frames([0.0, 1.0, 2.0, 0.5, 1.5], threshold=16.0, fraction_threshold=1.0)
    assert result.status is QCStatus.FAIL


def test_mixed_valid_samples_pass():
    result = check_black_frames([0.0, 80.0, 120.0, 60.0, 90.0], threshold=16.0, fraction_threshold=1.0)
    assert result.status is QCStatus.PASS


def test_single_dark_frame_does_not_fail_whole_video():
    result = check_black_frames([5.0, 80.0, 120.0, 60.0, 90.0], threshold=16.0, fraction_threshold=1.0)
    assert result.status is QCStatus.PASS


def test_entirely_identical_nonblack_frames_warn():
    result = check_frozen_frames([0.0, 0.0, 0.0, 0.0], threshold=2.0)
    assert result.status is QCStatus.WARN


def test_frozen_never_fails():
    result = check_frozen_frames([0.0, 0.0, 0.0, 0.0], threshold=2.0)
    assert result.status is not QCStatus.FAIL


def test_changed_frames_pass_frozen_check():
    result = check_frozen_frames([0.0, 45.0, 0.0, 60.0], threshold=2.0)
    assert result.status is QCStatus.PASS


def test_static_explainer_with_some_changed_frames_passes():
    """Limited/static explainer content: mostly-static but with at least
    one real transition must not be flagged."""
    result = check_frozen_frames([0.5, 0.3, 90.0, 0.2], threshold=2.0)
    assert result.status is QCStatus.PASS


def test_threshold_boundary_is_deterministic():
    assert check_frozen_frames([2.0, 2.0], threshold=2.0).status is QCStatus.WARN  # <= threshold counts as identical
    assert check_frozen_frames([2.1, 2.1], threshold=2.0).status is QCStatus.PASS


# ---------------------------------------------------------------------------
# Audio (item 31)
# ---------------------------------------------------------------------------


def test_audible_track_passes():
    assert check_audio_silence(-21.0, -60.0).status is QCStatus.PASS


def test_fully_silent_program_fails():
    assert check_audio_silence(-91.0, -60.0).status is QCStatus.FAIL


def test_silence_boundary_deterministic():
    assert check_audio_silence(-60.0, -60.0).status is QCStatus.FAIL  # exactly at threshold -- not > threshold
    assert check_audio_silence(-59.99, -60.0).status is QCStatus.PASS


def test_clipping_detected_at_zero_dbfs():
    assert check_audio_clipping(0.0, 0.0).status is QCStatus.FAIL


def test_clipping_not_detected_below_threshold():
    assert check_audio_clipping(-3.0, 0.0).status is QCStatus.PASS


# ---------------------------------------------------------------------------
# Subtitles (item 32)
# ---------------------------------------------------------------------------


def test_cue_count_match_passes():
    cues = [_cue(index=1), _cue(index=2)]
    assert check_subtitle_cue_count_match(cues, 2).status is QCStatus.PASS


def test_cue_count_mismatch_fails():
    cues = [_cue(index=1)]
    assert check_subtitle_cue_count_match(cues, 2).status is QCStatus.FAIL


def test_timestamps_valid_passes():
    cues = [_cue(index=1, start_ms=0, end_ms=1000), _cue(index=2, start_ms=1000, end_ms=2000)]
    assert check_subtitle_timestamps_valid(cues).status is QCStatus.PASS


def test_timestamp_mismatch_negative_start_fails():
    cues = [_cue(index=1, start_ms=-5, end_ms=1000)]
    assert check_subtitle_timestamps_valid(cues).status is QCStatus.FAIL


def test_timestamp_end_not_after_start_fails():
    cues = [_cue(index=1, start_ms=1000, end_ms=1000)]
    assert check_subtitle_timestamps_valid(cues).status is QCStatus.FAIL


def test_timestamp_overlap_fails():
    cues = [_cue(index=1, start_ms=0, end_ms=2000), _cue(index=2, start_ms=1000, end_ms=3000)]
    assert check_subtitle_timestamps_valid(cues).status is QCStatus.FAIL


def test_out_of_bounds_cue_fails():
    cues = [_cue(index=1, start_ms=0, end_ms=7000)]
    assert check_subtitle_timeline_bounds(cues, 6000).status is QCStatus.FAIL


def test_in_bounds_cue_passes():
    cues = [_cue(index=1, start_ms=0, end_ms=6000)]
    assert check_subtitle_timeline_bounds(cues, 6000).status is QCStatus.PASS


def test_text_mismatch_empty_text_fails():
    cues = [_cue(index=1, text="   ")]
    assert check_subtitle_text_nonempty(cues).status is QCStatus.FAIL


def test_text_nonempty_passes():
    cues = [_cue(index=1, text="Xin chào.")]
    assert check_subtitle_text_nonempty(cues).status is QCStatus.PASS


def test_vietnamese_text_preserved_in_subtitle_check():
    text = "Đây là một câu tiếng Việt đầy đủ dấu: ệ ị ọ ữ ẫ ẳ ố ơ."
    cues = [_cue(index=1, text=text)]
    assert check_subtitle_text_nonempty(cues).status is QCStatus.PASS
    assert cues[0].text == text


def test_exact_srt_match_passes():
    text = "1\n00:00:00,000 --> 00:00:01,000\nXin chào.\n"
    assert check_subtitle_exact_match(text, text).status is QCStatus.PASS


def test_srt_content_mismatch_fails():
    actual = "1\n00:00:00,000 --> 00:00:01,000\nXin chào.\n"
    expected = "1\n00:00:00,000 --> 00:00:01,000\nTạm biệt.\n"
    assert check_subtitle_exact_match(actual, expected).status is QCStatus.FAIL
