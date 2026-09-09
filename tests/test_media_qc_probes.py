"""Phase 32 focused tests: FFprobeClient/AudioAnalyzer/FrameSampler
(app/media_qc/probes.py).

Uses a fake/injected subprocess runner throughout -- real ffmpeg is
never invoked in this file (see tests/test_media_qc_integration.py for
the opt-in real-ffmpeg tests).
"""

from __future__ import annotations

from pathlib import Path

import pytest

from app.media_qc.errors import FFmpegNotFoundError, FFprobeNotFoundError, MediaProbeError
from app.media_qc.probes import AudioAnalyzer, FFprobeClient, _parse_frame_rate, _sample_positions_ms


class _FakeProcessResult:
    def __init__(self, returncode=0, stdout="", stderr=""):
        self.returncode = returncode
        self.stdout = stdout
        self.stderr = stderr


class _FakeRunner:
    def __init__(self, results):
        self._results = list(results)
        self.calls: list[list[str]] = []

    def __call__(self, command: list[str]):
        self.calls.append(command)
        return self._results[len(self.calls) - 1]


def _touch(path: Path, size: int = 10) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(b"x" * size)
    return path


_STANDARD_PROBE_JSON = (
    '{"streams":[{"codec_type":"video","codec_name":"h264","width":640,"height":360,'
    '"r_frame_rate":"30/1","pix_fmt":"yuv420p"},'
    '{"codec_type":"audio","codec_name":"aac"}],"format":{"duration":"6.000000"}}'
)


# ---------------------------------------------------------------------------
# rational fps parsing (requirement #6)
# ---------------------------------------------------------------------------


def test_parse_standard_30_fps():
    assert _parse_frame_rate("30/1") == 30.0


def test_parse_rational_ntsc_fps():
    assert _parse_frame_rate("30000/1001") == pytest.approx(29.97002997, abs=1e-6)


def test_parse_plain_number_fps():
    assert _parse_frame_rate("25") == 25.0


def test_parse_zero_denominator_fps_returns_zero():
    assert _parse_frame_rate("30/0") == 0.0


def test_parse_malformed_fps_returns_zero():
    assert _parse_frame_rate("not-a-rate") == 0.0


# ---------------------------------------------------------------------------
# FFprobeClient: parsing behavior
# ---------------------------------------------------------------------------


def test_probe_standard_stream(tmp_path):
    video = _touch(tmp_path / "video.mp4")
    runner = _FakeRunner([_FakeProcessResult(returncode=0, stdout=_STANDARD_PROBE_JSON)])
    # ffprobe_path accepts any existing file per _resolve_executable's
    # own contract -- the real executable is never invoked (fake runner).
    client = FFprobeClient(runner=runner, ffprobe_path=_touch(tmp_path / "ffprobe.exe"))
    result = client.probe(video)
    assert result.width == 640
    assert result.height == 360
    assert result.fps == 30.0
    assert result.video_codec == "h264"
    assert result.audio_codec == "aac"
    assert result.pixel_format == "yuv420p"
    assert result.duration_ms == 6000
    assert result.video_stream_count == 1
    assert result.audio_stream_count == 1


def test_probe_missing_audio_stream(tmp_path):
    video = _touch(tmp_path / "video.mp4")
    json_no_audio = '{"streams":[{"codec_type":"video","codec_name":"h264","width":640,"height":360,"r_frame_rate":"30/1","pix_fmt":"yuv420p"}],"format":{"duration":"6.000000"}}'
    runner = _FakeRunner([_FakeProcessResult(returncode=0, stdout=json_no_audio)])
    client = FFprobeClient(runner=runner, ffprobe_path=_touch(tmp_path / "ffprobe.exe"))
    result = client.probe(video)
    assert result.audio_stream_count == 0
    assert result.audio_codec is None


def test_probe_multiple_streams_uses_first_of_each_kind(tmp_path):
    video = _touch(tmp_path / "video.mp4")
    json_multi = (
        '{"streams":['
        '{"codec_type":"video","codec_name":"h264","width":640,"height":360,"r_frame_rate":"30/1","pix_fmt":"yuv420p"},'
        '{"codec_type":"audio","codec_name":"aac"},'
        '{"codec_type":"audio","codec_name":"mp3"}'
        '],"format":{"duration":"6.000000"}}'
    )
    runner = _FakeRunner([_FakeProcessResult(returncode=0, stdout=json_multi)])
    client = FFprobeClient(runner=runner, ffprobe_path=_touch(tmp_path / "ffprobe.exe"))
    result = client.probe(video)
    assert result.audio_stream_count == 2
    assert result.audio_codec == "aac"  # first audio stream


def test_probe_malformed_json_raises_media_probe_error(tmp_path):
    video = _touch(tmp_path / "video.mp4")
    runner = _FakeRunner([_FakeProcessResult(returncode=0, stdout="not json at all {{{")])
    client = FFprobeClient(runner=runner, ffprobe_path=_touch(tmp_path / "ffprobe.exe"))
    with pytest.raises(MediaProbeError):
        client.probe(video)


def test_probe_nonzero_exit_raises_media_probe_error(tmp_path):
    video = _touch(tmp_path / "video.mp4")
    runner = _FakeRunner([_FakeProcessResult(returncode=1, stderr="invalid data")])
    client = FFprobeClient(runner=runner, ffprobe_path=_touch(tmp_path / "ffprobe.exe"))
    with pytest.raises(MediaProbeError):
        client.probe(video)


def test_probe_missing_file_raises_media_probe_error(tmp_path):
    runner = _FakeRunner([])
    client = FFprobeClient(runner=runner, ffprobe_path=_touch(tmp_path / "ffprobe.exe"))
    with pytest.raises(MediaProbeError):
        client.probe(tmp_path / "missing.mp4")
    assert runner.calls == []  # never even attempts to probe a missing file


def test_probe_zero_byte_file_raises_media_probe_error(tmp_path):
    zero_byte = tmp_path / "zero.mp4"
    zero_byte.write_bytes(b"")
    runner = _FakeRunner([])
    client = FFprobeClient(runner=runner, ffprobe_path=_touch(tmp_path / "ffprobe.exe"))
    with pytest.raises(MediaProbeError):
        client.probe(zero_byte)


def test_probe_path_with_spaces_and_windows_style(tmp_path):
    video_dir = tmp_path / "my folder"
    video = _touch(video_dir / "my video.mp4")
    runner = _FakeRunner([_FakeProcessResult(returncode=0, stdout=_STANDARD_PROBE_JSON)])
    client = FFprobeClient(runner=runner, ffprobe_path=_touch(tmp_path / "ffprobe.exe"))
    client.probe(video)
    assert str(video) in runner.calls[0]
    assert isinstance(runner.calls[0], list)
    assert all(isinstance(arg, str) for arg in runner.calls[0])


def test_ffprobe_not_found_raises(tmp_path):
    with pytest.raises(FFprobeNotFoundError):
        FFprobeClient(runner=_FakeRunner([]), ffprobe_path=tmp_path / "no_ffprobe_here")


# ---------------------------------------------------------------------------
# AudioAnalyzer: volumedetect parsing
# ---------------------------------------------------------------------------


def test_audio_analyzer_parses_mean_and_max_volume(tmp_path):
    stderr = (
        "[Parsed_volumedetect_0 @ 0x1] n_samples: 48000\n"
        "[Parsed_volumedetect_0 @ 0x1] mean_volume: -21.1 dB\n"
        "[Parsed_volumedetect_0 @ 0x1] max_volume: -18.1 dB\n"
    )
    runner = _FakeRunner([_FakeProcessResult(returncode=0, stderr=stderr)])
    analyzer = AudioAnalyzer(runner=runner, ffmpeg_path=_touch(tmp_path / "ffmpeg.exe"))
    mean_db, max_db = analyzer.analyze(tmp_path / "video.mp4")
    assert mean_db == -21.1
    assert max_db == -18.1


def test_audio_analyzer_handles_negative_infinity(tmp_path):
    stderr = (
        "[Parsed_volumedetect_0 @ 0x1] mean_volume: -inf dB\n"
        "[Parsed_volumedetect_0 @ 0x1] max_volume: -inf dB\n"
    )
    runner = _FakeRunner([_FakeProcessResult(returncode=0, stderr=stderr)])
    analyzer = AudioAnalyzer(runner=runner, ffmpeg_path=_touch(tmp_path / "ffmpeg.exe"))
    mean_db, max_db = analyzer.analyze(tmp_path / "video.mp4")
    assert mean_db == float("-inf")
    assert max_db == float("-inf")


def test_audio_analyzer_missing_volume_output_raises(tmp_path):
    runner = _FakeRunner([_FakeProcessResult(returncode=0, stderr="no relevant output here")])
    analyzer = AudioAnalyzer(runner=runner, ffmpeg_path=_touch(tmp_path / "ffmpeg.exe"))
    with pytest.raises(MediaProbeError):
        analyzer.analyze(tmp_path / "video.mp4")


def test_audio_analyzer_nonzero_exit_raises(tmp_path):
    runner = _FakeRunner([_FakeProcessResult(returncode=1, stderr="decode error")])
    analyzer = AudioAnalyzer(runner=runner, ffmpeg_path=_touch(tmp_path / "ffmpeg.exe"))
    with pytest.raises(MediaProbeError):
        analyzer.analyze(tmp_path / "video.mp4")


def test_ffmpeg_not_found_raises(tmp_path):
    with pytest.raises(FFmpegNotFoundError):
        AudioAnalyzer(runner=_FakeRunner([]), ffmpeg_path=tmp_path / "no_ffmpeg_here")


# ---------------------------------------------------------------------------
# Deterministic sample positions
# ---------------------------------------------------------------------------


def test_sample_positions_five_samples_are_10_30_50_70_90_percent():
    positions = _sample_positions_ms(10_000, 5)
    assert positions == [1000, 3000, 5000, 7000, 9000]


def test_sample_positions_single_sample_is_midpoint():
    assert _sample_positions_ms(10_000, 1) == [5000]


def test_sample_positions_deterministic_repeated_call():
    assert _sample_positions_ms(6000, 5) == _sample_positions_ms(6000, 5)
