"""Phase 31 focused tests: CaptionBurnInCommandBuilder and
CaptionBurnInRenderer's own validation/process behavior
(app/captions/burn_in.py).

Uses a fake/injected subprocess runner throughout for the renderer's own
behavior tests -- real ffmpeg is never invoked in this file (see
tests/test_caption_burn_in_integration.py for the opt-in real-ffmpeg
test). Command-builder tests are pure string-construction checks.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from app.captions.burn_in import CaptionBurnInCommandBuilder, CaptionBurnInRenderer
from app.captions.errors import (
    CaptionBurnInProcessError,
    CaptionedVideoOutputError,
    FFmpegNotFoundError,
    FFprobeNotFoundError,
    InputVideoNotFoundError,
    InvalidSubtitlePathError,
    SubtitleFileNotFoundError,
    SubtitleFilterUnavailableError,
)
from app.captions.models import CaptionBurnInRequest, CaptionRenderSettings


def _request(**overrides) -> CaptionBurnInRequest:
    fields = dict(
        input_video_path=Path("input.mp4"), subtitle_path=Path("captions.srt"),
        output_path=Path("out.mp4"),
    )
    fields.update(overrides)
    return CaptionBurnInRequest(**fields)


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


_FILTERS_OUTPUT_WITH_SUBTITLES = " .. subtitles         V->V       Render text subtitles onto input video using the libass library.\n"
_PROBE_JSON = (
    '{"streams":[{"codec_type":"video","codec_name":"h264","width":640,"height":360,"r_frame_rate":"30/1"},'
    '{"codec_type":"audio","codec_name":"aac"}],"format":{"duration":"4.000000"}}'
)


# ---------------------------------------------------------------------------
# CaptionBurnInCommandBuilder: pure construction
# ---------------------------------------------------------------------------


def test_command_includes_input_and_output():
    command = CaptionBurnInCommandBuilder.build(_request(), "ffmpeg")
    assert command[0] == "ffmpeg"
    assert "-y" in command
    assert command[command.index("-i") + 1] == "input.mp4"
    assert command[-1] == "out.mp4"


def test_command_includes_subtitles_filter():
    command = CaptionBurnInCommandBuilder.build(_request(), "ffmpeg")
    vf_value = command[command.index("-vf") + 1]
    assert vf_value.startswith("subtitles='captions.srt':force_style=")


def test_command_applies_font_and_style_settings():
    request = _request(
        settings=CaptionRenderSettings(
            font_family="Arial", font_size=32, bottom_margin=50, outline_width=3, alignment="TOP_CENTER"
        )
    )
    command = CaptionBurnInCommandBuilder.build(request, "ffmpeg")
    vf_value = command[command.index("-vf") + 1]
    assert "FontName=Arial" in vf_value
    assert "FontSize=32" in vf_value
    assert "MarginV=50" in vf_value
    assert "Outline=3" in vf_value
    assert "Alignment=8" in vf_value  # TOP_CENTER -> ASS numpad alignment 8


def test_command_default_alignment_is_bottom_center():
    command = CaptionBurnInCommandBuilder.build(_request(), "ffmpeg")
    vf_value = command[command.index("-vf") + 1]
    assert "Alignment=2" in vf_value


def test_command_uses_h264_output():
    command = CaptionBurnInCommandBuilder.build(_request(), "ffmpeg")
    assert command[command.index("-c:v") + 1] == "libx264"
    assert command[command.index("-pix_fmt") + 1] == "yuv420p"


def test_command_copies_audio_stream():
    command = CaptionBurnInCommandBuilder.build(_request(), "ffmpeg")
    assert command[command.index("-c:a") + 1] == "copy"


def test_command_never_sets_fps_explicitly_preserving_source():
    command = CaptionBurnInCommandBuilder.build(_request(), "ffmpeg")
    assert "-r" not in command


def test_command_is_argument_list_not_shell_string():
    command = CaptionBurnInCommandBuilder.build(_request(), "ffmpeg")
    assert isinstance(command, list)
    assert all(isinstance(arg, str) for arg in command)


def test_command_determinism():
    request1 = _request()
    request2 = _request()
    assert CaptionBurnInCommandBuilder.build(request1, "ffmpeg") == CaptionBurnInCommandBuilder.build(
        request2, "ffmpeg"
    )


# ---------------------------------------------------------------------------
# Escaping: spaces, parentheses, Windows drive letters, backslashes,
# Vietnamese filenames
# ---------------------------------------------------------------------------


def test_escaping_path_with_spaces():
    request = _request(subtitle_path=Path("my folder/my captions.srt"))
    command = CaptionBurnInCommandBuilder.build(request, "ffmpeg")
    vf_value = command[command.index("-vf") + 1]
    assert "'my folder/my captions.srt'" in vf_value


def test_escaping_path_with_parentheses():
    request = _request(subtitle_path=Path("folder (test)/captions.srt"))
    command = CaptionBurnInCommandBuilder.build(request, "ffmpeg")
    vf_value = command[command.index("-vf") + 1]
    assert "'folder (test)/captions.srt'" in vf_value


def test_escaping_windows_drive_letter():
    request = _request(subtitle_path=Path("C:\\videos\\captions.srt"))
    command = CaptionBurnInCommandBuilder.build(request, "ffmpeg")
    vf_value = command[command.index("-vf") + 1]
    assert "'C\\:/videos/captions.srt'" in vf_value


def test_escaping_backslashes_converted_to_forward_slashes():
    request = _request(subtitle_path=Path("a\\b\\captions.srt"))
    command = CaptionBurnInCommandBuilder.build(request, "ffmpeg")
    vf_value = command[command.index("-vf") + 1]
    assert "'a/b/captions.srt'" in vf_value


def test_escaping_vietnamese_filename():
    request = _request(subtitle_path=Path("thư mục có dấu/phụ đề.srt"))
    command = CaptionBurnInCommandBuilder.build(request, "ffmpeg")
    vf_value = command[command.index("-vf") + 1]
    assert "'thư mục có dấu/phụ đề.srt'" in vf_value


def test_apostrophe_in_subtitle_path_rejected():
    request = _request(subtitle_path=Path("caption's.srt"))
    with pytest.raises(InvalidSubtitlePathError):
        CaptionBurnInCommandBuilder.build(request, "ffmpeg")


# ---------------------------------------------------------------------------
# CaptionBurnInRequest: invalid extensions
# ---------------------------------------------------------------------------


def test_invalid_subtitle_extension_rejected():
    with pytest.raises(Exception):
        _request(subtitle_path=Path("captions.vtt"))


def test_invalid_output_extension_rejected():
    with pytest.raises(Exception):
        _request(output_path=Path("out.mov"))


# ---------------------------------------------------------------------------
# CaptionBurnInRenderer: validation / process behavior (fake runner)
# ---------------------------------------------------------------------------


def test_missing_input_video_rejected(tmp_path):
    request = _request(
        input_video_path=tmp_path / "missing.mp4",
        subtitle_path=_touch(tmp_path / "captions.srt"),
        output_path=tmp_path / "out.mp4",
    )
    with pytest.raises(InputVideoNotFoundError):
        CaptionBurnInRenderer(runner=_FakeRunner([])).render(request)


def test_missing_subtitle_file_rejected(tmp_path):
    request = _request(
        input_video_path=_touch(tmp_path / "input.mp4"),
        subtitle_path=tmp_path / "missing.srt",
        output_path=tmp_path / "out.mp4",
    )
    with pytest.raises(SubtitleFileNotFoundError):
        CaptionBurnInRenderer(runner=_FakeRunner([])).render(request)


def test_missing_ffmpeg_capability_rejected(tmp_path, monkeypatch):
    monkeypatch.setattr("shutil.which", lambda name: None if name == "ffmpeg" else "/usr/bin/" + name)
    request = _valid_request(tmp_path)
    with pytest.raises(FFmpegNotFoundError):
        CaptionBurnInRenderer(runner=_FakeRunner([])).render(request)


def test_missing_ffprobe_capability_rejected(tmp_path, monkeypatch):
    monkeypatch.setattr("shutil.which", lambda name: "/usr/bin/ffmpeg" if name == "ffmpeg" else None)
    request = _valid_request(tmp_path)
    with pytest.raises(FFprobeNotFoundError):
        CaptionBurnInRenderer(runner=_FakeRunner([])).render(request)


def test_subtitles_filter_unavailable_rejected(tmp_path):
    request = _valid_request(tmp_path)
    runner = _FakeRunner([_FakeProcessResult(returncode=0, stdout="no relevant filters here\n")])
    with pytest.raises(SubtitleFilterUnavailableError):
        CaptionBurnInRenderer(runner=runner).render(request)


def test_nonzero_exit_raises_process_error(tmp_path):
    request = _valid_request(tmp_path)
    runner = _FakeRunner(
        [
            _FakeProcessResult(returncode=0, stdout=_FILTERS_OUTPUT_WITH_SUBTITLES),
            _FakeProcessResult(returncode=1, stderr="ffmpeg: something broke"),
        ]
    )
    with pytest.raises(CaptionBurnInProcessError) as exc_info:
        CaptionBurnInRenderer(runner=runner).render(request)
    assert "something broke" in str(exc_info.value)


def test_no_output_file_produced_raises(tmp_path):
    request = _valid_request(tmp_path)
    runner = _FakeRunner(
        [_FakeProcessResult(returncode=0, stdout=_FILTERS_OUTPUT_WITH_SUBTITLES), _FakeProcessResult(returncode=0)]
    )
    with pytest.raises(CaptionedVideoOutputError):
        CaptionBurnInRenderer(runner=runner).render(request)


def test_zero_byte_output_raises(tmp_path):
    request = _valid_request(tmp_path)
    request.output_path.write_bytes(b"")
    runner = _FakeRunner(
        [_FakeProcessResult(returncode=0, stdout=_FILTERS_OUTPUT_WITH_SUBTITLES), _FakeProcessResult(returncode=0)]
    )
    with pytest.raises(CaptionedVideoOutputError):
        CaptionBurnInRenderer(runner=runner).render(request)


def test_successful_render_reports_probed_metadata(tmp_path):
    request = _valid_request(tmp_path)
    request.output_path.write_bytes(b"fake mp4 bytes")
    runner = _FakeRunner(
        [
            _FakeProcessResult(returncode=0, stdout=_FILTERS_OUTPUT_WITH_SUBTITLES),
            _FakeProcessResult(returncode=0),
            _FakeProcessResult(returncode=0, stdout=_PROBE_JSON),
        ]
    )
    result = CaptionBurnInRenderer(runner=runner).render(request)
    assert result.width == 640
    assert result.height == 360
    assert result.fps == 30
    assert result.duration_ms == 4000
    assert result.video_codec == "h264"
    assert result.audio_codec == "aac"
    assert result.audio_stream_copied is True


def _valid_request(tmp_path) -> CaptionBurnInRequest:
    return _request(
        input_video_path=_touch(tmp_path / "input.mp4"),
        subtitle_path=_touch(tmp_path / "captions.srt"),
        output_path=tmp_path / "out.mp4",
    )


def _touch(path: Path) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(b"x")
    return path
