"""Phase 28 focused tests: VideoEncoder validation and process behavior
(app/video_encoder/encoder.py).

Uses a fake/injected subprocess runner throughout -- real ffmpeg is never
invoked in this file (see tests/test_video_encoder_integration.py for the
opt-in real-ffmpeg integration test).
"""

from __future__ import annotations

import wave
from pathlib import Path
from types import SimpleNamespace

import pytest

from app.models.timeline import TimelineCue, TimelineCueType
from app.video_encoder.encoder import VideoEncoder
from app.video_encoder.errors import (
    AudioAssetUnreadableError,
    FFmpegFilterUnavailableError,
    FFmpegNotFoundError,
    FFprobeNotFoundError,
    InvalidCrossfadeDurationError,
    InvalidMusicCueSequenceError,
    MissingMusicAssetError,
    MissingNarrationFileError,
    MissingSFXAssetError,
    MissingVisualFileError,
    MotionSourceTooSmallError,
    NarrationDurationOverflowError,
    UnknownSFXReferenceError,
    UnsupportedAudioAssetFormatError,
    UnsupportedNarrationFormatError,
    UnsupportedVisualFormatError,
    VideoDimensionMismatchError,
    VideoEncodingProcessError,
    VideoOutputCorruptError,
    VideoOutputNotProducedError,
)
from app.video_encoder.models import (
    AudioAssetBindings,
    VideoEncodeRequest,
    VideoEncodingSettings,
    VideoNarrationClip,
    VideoSegmentInput,
)


def _write_png(path: Path, size=(64, 48), color=(200, 0, 0)) -> Path:
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


def _settings(**overrides) -> VideoEncodingSettings:
    fields = dict(width=64, height=48, fps=30)
    fields.update(overrides)
    return VideoEncodingSettings(**fields)


def _segment(image_path, narration_path, **overrides) -> VideoSegmentInput:
    fields = dict(
        segment_id="S1", image_path=image_path, duration_ms=1000,
        narration_clips=[VideoNarrationClip(file_path=narration_path)],
        transition_in="HOLD", transition_out="CUT",
    )
    fields.update(overrides)
    return VideoSegmentInput(**fields)


class _FakeProcessResult:
    def __init__(self, returncode=0, stdout="", stderr=""):
        self.returncode = returncode
        self.stdout = stdout
        self.stderr = stderr


class _FakeRunner:
    """Returns a fixed sequence of results, one per call, in order --
    mirrors FakeVisualProvider/FakeTTSProvider's shape."""

    def __init__(self, results):
        self._results = list(results)
        self.calls: list[list[str]] = []

    def __call__(self, command: list[str]):
        self.calls.append(command)
        return self._results[len(self.calls) - 1]


def _valid_request(tmp_path, output_path=None) -> VideoEncodeRequest:
    image = _write_png(tmp_path / "a.png")
    audio = _write_wav(tmp_path / "a.wav", 1.0)
    return VideoEncodeRequest(
        segments=[_segment(image, audio, duration_ms=1000)],
        settings=_settings(),
        output_path=output_path or (tmp_path / "out.mp4"),
    )


# ---------------------------------------------------------------------------
# Validation: missing/unsupported visual
# ---------------------------------------------------------------------------


def test_missing_visual_file_rejected(tmp_path):
    audio = _write_wav(tmp_path / "a.wav", 1.0)
    request = VideoEncodeRequest(
        segments=[_segment(tmp_path / "missing.png", audio)],
        settings=_settings(), output_path=tmp_path / "out.mp4",
    )
    with pytest.raises(MissingVisualFileError):
        VideoEncoder(runner=_FakeRunner([])).encode(request)


def test_unsupported_visual_format_rejected(tmp_path):
    bogus = tmp_path / "a.gif"
    bogus.write_bytes(b"GIF89a")
    audio = _write_wav(tmp_path / "a.wav", 1.0)
    request = VideoEncodeRequest(
        segments=[_segment(bogus, audio)], settings=_settings(), output_path=tmp_path / "out.mp4",
    )
    with pytest.raises(UnsupportedVisualFormatError):
        VideoEncoder(runner=_FakeRunner([])).encode(request)


def test_corrupt_visual_file_rejected(tmp_path):
    corrupt = tmp_path / "a.png"
    corrupt.write_bytes(b"not a real png")
    audio = _write_wav(tmp_path / "a.wav", 1.0)
    request = VideoEncodeRequest(
        segments=[_segment(corrupt, audio)], settings=_settings(), output_path=tmp_path / "out.mp4",
    )
    with pytest.raises(UnsupportedVisualFormatError):
        VideoEncoder(runner=_FakeRunner([])).encode(request)


def test_dimension_mismatch_rejected(tmp_path):
    image = _write_png(tmp_path / "a.png", size=(100, 100))
    audio = _write_wav(tmp_path / "a.wav", 1.0)
    request = VideoEncodeRequest(
        segments=[_segment(image, audio)], settings=_settings(width=64, height=48),
        output_path=tmp_path / "out.mp4",
    )
    with pytest.raises(VideoDimensionMismatchError):
        VideoEncoder(runner=_FakeRunner([])).encode(request)


# ---------------------------------------------------------------------------
# Validation: missing/unsupported narration, overflow
# ---------------------------------------------------------------------------


def test_missing_narration_file_rejected(tmp_path):
    image = _write_png(tmp_path / "a.png")
    request = VideoEncodeRequest(
        segments=[_segment(image, tmp_path / "missing.wav")], settings=_settings(),
        output_path=tmp_path / "out.mp4",
    )
    with pytest.raises(MissingNarrationFileError):
        VideoEncoder(runner=_FakeRunner([])).encode(request)


def test_unsupported_narration_format_rejected(tmp_path):
    image = _write_png(tmp_path / "a.png")
    bogus_audio = tmp_path / "a.mp3"
    bogus_audio.write_bytes(b"ID3")
    request = VideoEncodeRequest(
        segments=[_segment(image, bogus_audio)], settings=_settings(), output_path=tmp_path / "out.mp4",
    )
    with pytest.raises(UnsupportedNarrationFormatError):
        VideoEncoder(runner=_FakeRunner([])).encode(request)


def test_corrupt_narration_file_rejected(tmp_path):
    image = _write_png(tmp_path / "a.png")
    corrupt_audio = tmp_path / "a.wav"
    corrupt_audio.write_bytes(b"not a real wav")
    request = VideoEncodeRequest(
        segments=[_segment(image, corrupt_audio)], settings=_settings(), output_path=tmp_path / "out.mp4",
    )
    with pytest.raises(UnsupportedNarrationFormatError):
        VideoEncoder(runner=_FakeRunner([])).encode(request)


def test_narration_duration_overflow_rejected(tmp_path):
    image = _write_png(tmp_path / "a.png")
    audio = _write_wav(tmp_path / "a.wav", 2.0)  # 2000ms real audio
    request = VideoEncodeRequest(
        segments=[_segment(image, audio, duration_ms=1000)],  # only 1000ms authored
        settings=_settings(), output_path=tmp_path / "out.mp4",
    )
    with pytest.raises(NarrationDurationOverflowError):
        VideoEncoder(runner=_FakeRunner([])).encode(request)


def test_narration_shorter_than_duration_is_allowed(tmp_path):
    image = _write_png(tmp_path / "a.png")
    audio = _write_wav(tmp_path / "a.wav", 0.5)  # shorter than segment duration
    request = VideoEncodeRequest(
        segments=[_segment(image, audio, duration_ms=1000)],
        settings=_settings(), output_path=tmp_path / "out.mp4",
    )
    fake_output = tmp_path / "out.mp4"
    fake_output.write_bytes(b"fake mp4 bytes")
    runner = _FakeRunner([_FakeProcessResult(returncode=0), _FakeProcessResult(returncode=0, stdout="video\naudio\n")])
    result = VideoEncoder(runner=runner).encode(request)
    assert result.duration_ms == 1000


# ---------------------------------------------------------------------------
# Validation: CROSSFADE (Phase 29)
# ---------------------------------------------------------------------------


def test_crossfade_transition_in_on_first_segment_is_ignored(tmp_path):
    """transition_in is never consulted for join decisions -- a lone
    segment's transition_in=CROSSFADE has no adjacent partner to blend
    with and must not raise anything."""
    image = _write_png(tmp_path / "a.png")
    audio = _write_wav(tmp_path / "a.wav", 1.0)
    request = VideoEncodeRequest(
        segments=[_segment(image, audio, transition_in="CROSSFADE")],
        settings=_settings(), output_path=tmp_path / "out.mp4",
    )
    (tmp_path / "out.mp4").write_bytes(b"fake")
    runner = _FakeRunner([_FakeProcessResult(returncode=0), _FakeProcessResult(returncode=0, stdout="video\naudio\n")])
    VideoEncoder(runner=runner).encode(request)  # must not raise


def test_crossfade_transition_out_on_last_segment_is_ignored(tmp_path):
    """The LAST segment's own transition_out has no next segment to join
    to -- CROSSFADE there must not raise anything either."""
    image = _write_png(tmp_path / "a.png")
    audio = _write_wav(tmp_path / "a.wav", 1.0)
    request = VideoEncodeRequest(
        segments=[_segment(image, audio, transition_out="CROSSFADE")],
        settings=_settings(), output_path=tmp_path / "out.mp4",
    )
    (tmp_path / "out.mp4").write_bytes(b"fake")
    runner = _FakeRunner([_FakeProcessResult(returncode=0), _FakeProcessResult(returncode=0, stdout="video\naudio\n")])
    VideoEncoder(runner=runner).encode(request)  # must not raise


def test_crossfade_duration_too_long_for_earlier_segment_rejected(tmp_path):
    image = _write_png(tmp_path / "a.png")
    audio = _write_wav(tmp_path / "a.wav", 0.1)
    segments = [
        _segment(image, audio, segment_id="S1", duration_ms=200, transition_out="CROSSFADE"),
        _segment(image, audio, segment_id="S2", duration_ms=1000, transition_in="CUT", transition_out="CUT"),
    ]
    request = VideoEncodeRequest(segments=segments, settings=_settings(), output_path=tmp_path / "out.mp4")
    with pytest.raises(InvalidCrossfadeDurationError):
        VideoEncoder(runner=_FakeRunner([])).encode(request)


def test_crossfade_duration_too_long_for_later_segment_rejected(tmp_path):
    image = _write_png(tmp_path / "a.png")
    audio = _write_wav(tmp_path / "a.wav", 0.1)
    segments = [
        _segment(image, audio, segment_id="S1", duration_ms=1000, transition_out="CROSSFADE"),
        _segment(image, audio, segment_id="S2", duration_ms=200, transition_in="CUT", transition_out="CUT"),
    ]
    request = VideoEncodeRequest(segments=segments, settings=_settings(), output_path=tmp_path / "out.mp4")
    with pytest.raises(InvalidCrossfadeDurationError):
        VideoEncoder(runner=_FakeRunner([])).encode(request)


def test_crossfade_duration_equal_to_segment_duration_rejected(tmp_path):
    """Must be STRICTLY shorter -- exactly equal is also rejected."""
    image = _write_png(tmp_path / "a.png")
    audio = _write_wav(tmp_path / "a.wav", 0.1)
    segments = [
        _segment(image, audio, segment_id="S1", duration_ms=300, transition_out="CROSSFADE"),
        _segment(image, audio, segment_id="S2", duration_ms=1000, transition_in="CUT", transition_out="CUT"),
    ]
    request = VideoEncodeRequest(
        segments=segments, settings=_settings(crossfade_duration_ms=300), output_path=tmp_path / "out.mp4"
    )
    with pytest.raises(InvalidCrossfadeDurationError):
        VideoEncoder(runner=_FakeRunner([])).encode(request)


def test_valid_crossfade_with_filter_available_succeeds(tmp_path):
    """xfade IS reported by the fake ffmpeg -filters output, so this must
    succeed, with the filter-availability check as its own runner call
    before the real encode."""
    image = _write_png(tmp_path / "a.png")
    audio = _write_wav(tmp_path / "a.wav", 0.1)
    segments = [
        _segment(image, audio, segment_id="S1", duration_ms=1000, transition_out="CROSSFADE"),
        _segment(image, audio, segment_id="S2", duration_ms=1000, transition_in="CUT", transition_out="CUT"),
    ]
    request = VideoEncodeRequest(segments=segments, settings=_settings(), output_path=tmp_path / "out.mp4")
    (tmp_path / "out.mp4").write_bytes(b"fake")
    runner = _FakeRunner(
        [
            _FakeProcessResult(returncode=0, stdout=" ..S xfade   VV->V   Cross fade\n"),
            _FakeProcessResult(returncode=0),
            _FakeProcessResult(returncode=0, stdout="video\naudio\n"),
        ]
    )
    result = VideoEncoder(runner=runner).encode(request)
    assert result.crossfade_count == 1
    assert len(runner.calls) == 3  # filter-check + ffmpeg + ffprobe
    assert runner.calls[0][1:3] == ["-hide_banner", "-filters"]


def test_ffmpeg_missing_required_filter_rejected(tmp_path):
    image = _write_png(tmp_path / "a.png")
    audio = _write_wav(tmp_path / "a.wav", 0.1)
    segments = [
        _segment(image, audio, segment_id="S1", duration_ms=1000, transition_out="CROSSFADE"),
        _segment(image, audio, segment_id="S2", duration_ms=1000, transition_in="CUT", transition_out="CUT"),
    ]
    request = VideoEncodeRequest(segments=segments, settings=_settings(), output_path=tmp_path / "out.mp4")
    runner = _FakeRunner([_FakeProcessResult(returncode=0, stdout="no relevant filters listed here\n")])
    with pytest.raises(FFmpegFilterUnavailableError):
        VideoEncoder(runner=runner).encode(request)
    assert len(runner.calls) == 1  # never reaches the real ffmpeg encode call


# ---------------------------------------------------------------------------
# Validation: motion-lite (Phase 29)
# ---------------------------------------------------------------------------


def test_pan_motion_on_too_small_canvas_rejected(tmp_path):
    """width=10 -> round(10*1.05)=10 (banker's rounding), i.e. zero whole
    pixels of pan travel -- must fail explicitly rather than silently
    produce a no-op pan."""
    image = _write_png(tmp_path / "a.png", size=(10, 10))
    audio = _write_wav(tmp_path / "a.wav", 0.1)
    request = VideoEncodeRequest(
        segments=[_segment(image, audio, motion="PAN_LEFT")],
        settings=_settings(width=10, height=10), output_path=tmp_path / "out.mp4",
    )
    with pytest.raises(MotionSourceTooSmallError):
        VideoEncoder(runner=_FakeRunner([])).encode(request)


def test_static_motion_default_requires_no_filter_check(tmp_path):
    request = _valid_request(tmp_path)
    request.output_path.write_bytes(b"fake mp4 bytes")
    runner = _FakeRunner([_FakeProcessResult(returncode=0), _FakeProcessResult(returncode=0, stdout="video\naudio\n")])
    VideoEncoder(runner=runner).encode(request)
    assert len(runner.calls) == 2  # no filter-availability call needed for an all-STATIC/CUT request


def test_cut_and_hold_transitions_pass_validation(tmp_path):
    image = _write_png(tmp_path / "a.png")
    audio = _write_wav(tmp_path / "a.wav", 1.0)
    request = VideoEncodeRequest(
        segments=[_segment(image, audio, transition_in="CUT", transition_out="HOLD")],
        settings=_settings(), output_path=tmp_path / "out.mp4",
    )
    (tmp_path / "out.mp4").write_bytes(b"fake")
    runner = _FakeRunner([_FakeProcessResult(returncode=0), _FakeProcessResult(returncode=0, stdout="video\naudio\n")])
    VideoEncoder(runner=runner).encode(request)  # must not raise


# ---------------------------------------------------------------------------
# Missing executables
# ---------------------------------------------------------------------------


def test_missing_ffmpeg_executable_rejected(tmp_path, monkeypatch):
    monkeypatch.setattr("shutil.which", lambda name: None if name == "ffmpeg" else "/usr/bin/" + name)
    request = _valid_request(tmp_path)
    with pytest.raises(FFmpegNotFoundError):
        VideoEncoder(runner=_FakeRunner([])).encode(request)


def test_missing_ffprobe_executable_rejected(tmp_path, monkeypatch):
    monkeypatch.setattr("shutil.which", lambda name: "/usr/bin/ffmpeg" if name == "ffmpeg" else None)
    request = _valid_request(tmp_path)
    with pytest.raises(FFprobeNotFoundError):
        VideoEncoder(runner=_FakeRunner([])).encode(request)


def test_explicit_ffmpeg_path_not_found_rejected(tmp_path):
    request = _valid_request(tmp_path)
    request = request.model_copy(
        update={"settings": request.settings.model_copy(update={"ffmpeg_path": tmp_path / "no_ffmpeg_here"})}
    )
    with pytest.raises(FFmpegNotFoundError):
        VideoEncoder(runner=_FakeRunner([])).encode(request)


# ---------------------------------------------------------------------------
# Process behavior
# ---------------------------------------------------------------------------


def test_exit_zero_and_valid_output_succeeds(tmp_path):
    request = _valid_request(tmp_path)
    request.output_path.write_bytes(b"fake mp4 bytes")
    runner = _FakeRunner([_FakeProcessResult(returncode=0), _FakeProcessResult(returncode=0, stdout="video\naudio\n")])

    result = VideoEncoder(runner=runner).encode(request)

    assert result.file_size_bytes > 0
    assert result.duration_ms == 1000
    assert len(runner.calls) == 2  # ffmpeg call + ffprobe verification call


def test_nonzero_exit_raises_process_error(tmp_path):
    request = _valid_request(tmp_path)
    runner = _FakeRunner([_FakeProcessResult(returncode=1, stderr="ffmpeg: something broke")])

    with pytest.raises(VideoEncodingProcessError) as exc_info:
        VideoEncoder(runner=runner).encode(request)
    assert "something broke" in str(exc_info.value)


def test_no_output_file_produced_raises(tmp_path):
    request = _valid_request(tmp_path)
    # ffmpeg "succeeds" but never actually writes the output file.
    runner = _FakeRunner([_FakeProcessResult(returncode=0)])

    with pytest.raises(VideoOutputNotProducedError):
        VideoEncoder(runner=runner).encode(request)


def test_zero_byte_output_raises(tmp_path):
    request = _valid_request(tmp_path)
    request.output_path.write_bytes(b"")  # zero bytes
    runner = _FakeRunner([_FakeProcessResult(returncode=0)])

    with pytest.raises(VideoOutputCorruptError):
        VideoEncoder(runner=runner).encode(request)


def test_ffprobe_reports_missing_stream_raises_corrupt(tmp_path):
    request = _valid_request(tmp_path)
    request.output_path.write_bytes(b"fake mp4 bytes")
    runner = _FakeRunner([_FakeProcessResult(returncode=0), _FakeProcessResult(returncode=0, stdout="video\n")])

    with pytest.raises(VideoOutputCorruptError):
        VideoEncoder(runner=runner).encode(request)


def test_ffprobe_nonzero_exit_raises_corrupt(tmp_path):
    request = _valid_request(tmp_path)
    request.output_path.write_bytes(b"fake mp4 bytes")
    runner = _FakeRunner(
        [_FakeProcessResult(returncode=0), _FakeProcessResult(returncode=1, stderr="ffprobe error")]
    )

    with pytest.raises(VideoOutputCorruptError):
        VideoEncoder(runner=runner).encode(request)


def test_no_ffmpeg_call_made_when_validation_fails(tmp_path):
    audio = _write_wav(tmp_path / "a.wav", 1.0)
    request = VideoEncodeRequest(
        segments=[_segment(tmp_path / "missing.png", audio)], settings=_settings(),
        output_path=tmp_path / "out.mp4",
    )
    runner = _FakeRunner([])
    with pytest.raises(MissingVisualFileError):
        VideoEncoder(runner=runner).encode(request)
    assert runner.calls == []


# ---------------------------------------------------------------------------
# Phase 30: music/SFX validation
# ---------------------------------------------------------------------------


def _music_cue(timestamp_ms, cue_type) -> TimelineCue:
    return TimelineCue(timestamp_ms=timestamp_ms, cue_type=cue_type)


def _sfx_cue(timestamp_ms, reference) -> TimelineCue:
    return TimelineCue(timestamp_ms=timestamp_ms, cue_type=TimelineCueType.SFX_TRIGGER, reference=reference)


def test_missing_music_binding_rejected(tmp_path):
    request = _valid_request(tmp_path)
    request = request.model_copy(
        update={"cues": [_music_cue(0, TimelineCueType.MUSIC_BED_START)]}
    )
    with pytest.raises(MissingMusicAssetError):
        VideoEncoder(runner=_FakeRunner([])).encode(request)


def test_music_bed_file_not_found_rejected(tmp_path):
    request = _valid_request(tmp_path)
    request = request.model_copy(
        update={
            "cues": [_music_cue(0, TimelineCueType.MUSIC_BED_START)],
            "audio_bindings": AudioAssetBindings(music_bed_path=tmp_path / "missing_music.wav"),
        }
    )
    with pytest.raises(MissingMusicAssetError):
        VideoEncoder(runner=_FakeRunner([])).encode(request)


def test_music_bed_unsupported_format_rejected(tmp_path):
    bogus_music = tmp_path / "music.ogg"
    bogus_music.write_bytes(b"OggS")
    request = _valid_request(tmp_path)
    request = request.model_copy(
        update={
            "cues": [_music_cue(0, TimelineCueType.MUSIC_BED_START)],
            "audio_bindings": AudioAssetBindings(music_bed_path=bogus_music),
        }
    )
    with pytest.raises(UnsupportedAudioAssetFormatError):
        VideoEncoder(runner=_FakeRunner([])).encode(request)


def test_unknown_sfx_reference_rejected(tmp_path):
    request = _valid_request(tmp_path)
    request = request.model_copy(update={"cues": [_sfx_cue(500, "not_bound")]})
    with pytest.raises(UnknownSFXReferenceError):
        VideoEncoder(runner=_FakeRunner([])).encode(request)


def test_sfx_file_not_found_rejected(tmp_path):
    request = _valid_request(tmp_path)
    request = request.model_copy(
        update={
            "cues": [_sfx_cue(500, "whoosh")],
            "audio_bindings": AudioAssetBindings(sfx_by_id={"whoosh": tmp_path / "missing_sfx.wav"}),
        }
    )
    with pytest.raises(MissingSFXAssetError):
        VideoEncoder(runner=_FakeRunner([])).encode(request)


def test_sfx_unsupported_format_rejected(tmp_path):
    bogus_sfx = tmp_path / "sfx.ogg"
    bogus_sfx.write_bytes(b"OggS")
    request = _valid_request(tmp_path)
    request = request.model_copy(
        update={
            "cues": [_sfx_cue(500, "whoosh")],
            "audio_bindings": AudioAssetBindings(sfx_by_id={"whoosh": bogus_sfx}),
        }
    )
    with pytest.raises(UnsupportedAudioAssetFormatError):
        VideoEncoder(runner=_FakeRunner([])).encode(request)


def test_invalid_music_cue_sequence_rejected(tmp_path):
    request = _valid_request(tmp_path)
    request = request.model_copy(update={"cues": [_music_cue(0, TimelineCueType.MUSIC_DUCK)]})
    with pytest.raises(InvalidMusicCueSequenceError):
        VideoEncoder(runner=_FakeRunner([])).encode(request)
    # Fails during pure planning -- never even reaches asset validation.


def test_no_extra_runner_calls_for_narration_only_request(tmp_path):
    """No cues at all -- Phase 30's own asset-readability check must add
    zero extra runner calls beyond Phase 28/29's own two (ffmpeg +
    ffprobe)."""
    request = _valid_request(tmp_path)
    request.output_path.write_bytes(b"fake mp4 bytes")
    runner = _FakeRunner([_FakeProcessResult(returncode=0), _FakeProcessResult(returncode=0, stdout="video\naudio\n")])
    result = VideoEncoder(runner=runner).encode(request)
    assert len(runner.calls) == 2
    assert result.has_music is False
    assert result.sfx_event_count == 0
    assert result.music_cue_count == 0


def test_ffmpeg_filter_check_requires_amix_when_music_used(tmp_path):
    music = _write_wav(tmp_path / "music.wav", 2.0)
    request = _valid_request(tmp_path)
    request = request.model_copy(
        update={
            "cues": [_music_cue(0, TimelineCueType.MUSIC_BED_START)],
            "audio_bindings": AudioAssetBindings(music_bed_path=music),
        }
    )
    runner = _FakeRunner([_FakeProcessResult(returncode=0, stdout="no relevant filters here\n")])
    with pytest.raises(FFmpegFilterUnavailableError):
        VideoEncoder(runner=runner).encode(request)


def test_audio_asset_unreadable_rejected(tmp_path):
    music = _write_wav(tmp_path / "music.wav", 2.0)
    request = _valid_request(tmp_path)
    request = request.model_copy(
        update={
            "cues": [_music_cue(0, TimelineCueType.MUSIC_BED_START)],
            "audio_bindings": AudioAssetBindings(music_bed_path=music),
        }
    )
    runner = _FakeRunner(
        [
            _FakeProcessResult(returncode=0, stdout=" ..A amix    N->1     Audio mixing.\n"),
            _FakeProcessResult(returncode=0, stdout="video\n"),  # no "audio" stream reported
        ]
    )
    with pytest.raises(AudioAssetUnreadableError):
        VideoEncoder(runner=runner).encode(request)
    assert len(runner.calls) == 2  # never reaches the real ffmpeg encode call


def test_full_mix_success_reports_result_fields(tmp_path):
    music = _write_wav(tmp_path / "music.wav", 2.0)
    sfx = _write_wav(tmp_path / "sfx.wav", 0.2)
    request = _valid_request(tmp_path, output_path=tmp_path / "out.mp4")
    request = request.model_copy(
        update={
            "cues": [
                _music_cue(0, TimelineCueType.MUSIC_BED_START),
                _music_cue(1000, TimelineCueType.MUSIC_BED_END),
                _sfx_cue(500, "whoosh"),
            ],
            "audio_bindings": AudioAssetBindings(music_bed_path=music, sfx_by_id={"whoosh": sfx}),
        }
    )
    request.output_path.write_bytes(b"fake mp4 bytes")
    runner = _FakeRunner(
        [
            _FakeProcessResult(returncode=0, stdout=" ..A amix    N->1     Audio mixing.\n"),  # filter check
            _FakeProcessResult(returncode=0, stdout="audio\n"),  # music readability
            _FakeProcessResult(returncode=0, stdout="audio\n"),  # sfx readability
            _FakeProcessResult(returncode=0),  # real encode
            _FakeProcessResult(returncode=0, stdout="video\naudio\n"),  # output verification
        ]
    )
    result = VideoEncoder(runner=runner).encode(request)
    assert len(runner.calls) == 5
    assert result.has_music is True
    assert result.sfx_event_count == 1
    assert result.music_cue_count == 2
