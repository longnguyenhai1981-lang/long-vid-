"""CaptionBurnInRenderer: optional, opt-in local FFmpeg execution that
burns an already-exported SRT into an already-encoded MP4 (Phase 31
requirements #16-18).

This module never regenerates visuals, never rebuilds narration, never
rebuilds the music/SFX graph, and never changes transition/motion
timing -- it takes an EncodedVideoAsset's own MP4 file and an SRT file as
pure, already-resolved inputs (mirroring app/video_encoder/encoder.py's
own "every path it touches must already exist" boundary) and produces
exactly one new MP4 with subtitles rendered into the pixels via
ffmpeg's `subtitles` filter (libass).

Video is always re-encoded (the `subtitles` filter modifies pixel data,
so there is no way to avoid it); audio is always stream-copied
(`-c:a copy`) rather than re-encoded -- this pipeline's own inputs are
always an AAC-in-MP4 file this module itself never chose the codec for,
so a copy is both safe and strictly more deterministic than a second lossy
re-encode pass, and it guarantees narration/music/SFX timing and content
are untouched by definition (there are no audio samples to alter).

FFmpeg subtitle-path escaping (requirement #18): the `subtitles` filter's
own `filename` argument lives inside a `-vf` filtergraph mini-language
whose only special characters are `:`, `'`, and (as an escape character)
`\\`. Backslashes are normalized to forward slashes (Windows paths work
fine with forward slashes in ffmpeg), the whole path is wrapped in single
quotes, and a drive-letter colon is escaped as `\\:` so it is not
misread as the option-separator colon. Confirmed empirically (real
ffmpeg, this exact escaping) to correctly handle spaces, parentheses,
Windows drive letters, backslashes, and Vietnamese Unicode filenames/
directory names. A literal single quote in the path is NOT supported --
confirmed empirically that this ffmpeg/libass build cannot reliably
preserve one inside a `subtitles` filter's filename argument (the
quote is silently dropped and, depending on position, the rest of the
filter string is corrupted) -- so such a path is rejected explicitly
(InvalidSubtitlePathError) rather than risking a silently broken filter
graph. This is a path-only restriction; the SRT's own TEXT content may
contain apostrophes freely (that is parsed by libass's own SRT reader,
unrelated to this filtergraph-argument escaping).

Required-filter check: before building the real command, this module
queries the resolved `ffmpeg` executable's own `-filters` output once and
confirms `subtitles` is actually present in this local build
(SubtitleFilterUnavailableError if not) -- never a silent fallback, never
an automatic download of a different build or font.
"""

from __future__ import annotations

import json
import re
import shutil
import subprocess
from pathlib import Path
from typing import Callable, Protocol

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
from app.captions.models import CaptionBurnInRequest, CaptionBurnInResult

_ALIGNMENT_ASS_VALUE = {"BOTTOM_CENTER": 2, "TOP_CENTER": 8}
_STDERR_TAIL_CHARS = 2000


class ProcessResult(Protocol):
    returncode: int
    stdout: str
    stderr: str


ProcessRunner = Callable[[list[str]], ProcessResult]


def _default_runner(command: list[str]) -> subprocess.CompletedProcess:
    """The real subprocess runner -- argument list only, never
    `shell=True`, never a raw interpolated command string."""
    return subprocess.run(command, capture_output=True, text=True, timeout=600)


class CaptionBurnInRenderer:
    def __init__(self, *, runner: ProcessRunner = _default_runner):
        self._runner = runner

    def render(self, request: CaptionBurnInRequest) -> CaptionBurnInResult:
        if not request.input_video_path.is_file():
            raise InputVideoNotFoundError(f"Input video not found: {request.input_video_path}")
        if not request.subtitle_path.is_file():
            raise SubtitleFileNotFoundError(f"Subtitle file not found: {request.subtitle_path}")

        ffmpeg_executable = _resolve_executable(request.ffmpeg_path, "ffmpeg", FFmpegNotFoundError)
        ffprobe_executable = _resolve_executable(request.ffprobe_path, "ffprobe", FFprobeNotFoundError)
        self._check_subtitles_filter_available(ffmpeg_executable)

        try:
            request.output_path.parent.mkdir(parents=True, exist_ok=True)
        except OSError as exc:
            raise CaptionedVideoOutputError(
                f"Cannot create output directory {request.output_path.parent}: {exc}"
            ) from exc

        command = CaptionBurnInCommandBuilder.build(request, ffmpeg_executable)
        result = self._runner(command)
        if result.returncode != 0:
            raise CaptionBurnInProcessError(
                f"ffmpeg exited with code {result.returncode}: {_tail(result.stderr)}"
            )

        if not request.output_path.is_file():
            raise CaptionedVideoOutputError(
                f"ffmpeg exited 0 but no output file exists at {request.output_path}"
            )
        file_size_bytes = request.output_path.stat().st_size
        if file_size_bytes == 0:
            raise CaptionedVideoOutputError(f"Output file at {request.output_path} is zero bytes")

        probe = self._probe_output(ffprobe_executable, request.output_path)

        return CaptionBurnInResult(
            output_path=request.output_path,
            width=probe["width"],
            height=probe["height"],
            fps=probe["fps"],
            duration_ms=probe["duration_ms"],
            file_size_bytes=file_size_bytes,
            video_codec=probe["video_codec"],
            audio_codec=probe["audio_codec"],
            audio_stream_copied=True,
            ffmpeg_command=command,
        )

    def _check_subtitles_filter_available(self, ffmpeg_executable: str) -> None:
        command = [ffmpeg_executable, "-hide_banner", "-filters"]
        result = self._runner(command)
        if result.returncode != 0:
            raise SubtitleFilterUnavailableError(
                f"Failed to query ffmpeg's available filters: {_tail(result.stderr)}"
            )
        if not _filter_listed(result.stdout, "subtitles"):
            raise SubtitleFilterUnavailableError(
                "The resolved ffmpeg build does not report the `subtitles` filter in "
                "`ffmpeg -filters` -- this module never downloads or substitutes a "
                "different ffmpeg build automatically"
            )

    def _probe_output(self, ffprobe_executable: str, output_path: Path) -> dict:
        command = [
            ffprobe_executable, "-v", "error", "-print_format", "json",
            "-show_format", "-show_streams", str(output_path),
        ]
        result = self._runner(command)
        if result.returncode != 0:
            raise CaptionedVideoOutputError(
                f"ffprobe could not inspect {output_path}: {_tail(result.stderr)}"
            )
        try:
            probe = json.loads(result.stdout)
        except json.JSONDecodeError as exc:
            raise CaptionedVideoOutputError(f"ffprobe returned unparseable output: {exc}") from exc

        video_stream = next((s for s in probe.get("streams", []) if s.get("codec_type") == "video"), None)
        audio_stream = next((s for s in probe.get("streams", []) if s.get("codec_type") == "audio"), None)
        if video_stream is None or audio_stream is None:
            raise CaptionedVideoOutputError(
                f"ffprobe did not find both a video and an audio stream in {output_path}"
            )

        fps = _parse_frame_rate(video_stream.get("r_frame_rate", "0/1"))
        duration_seconds = float(probe.get("format", {}).get("duration", 0.0))
        return {
            "width": int(video_stream["width"]),
            "height": int(video_stream["height"]),
            "fps": fps,
            "duration_ms": round(duration_seconds * 1000),
            "video_codec": video_stream.get("codec_name", ""),
            "audio_codec": audio_stream.get("codec_name", ""),
        }


class CaptionBurnInCommandBuilder:
    """Pure command construction, testable without running ffmpeg."""

    @staticmethod
    def build(request: CaptionBurnInRequest, ffmpeg_executable: str) -> list[str]:
        settings = request.settings
        filter_arg = (
            f"subtitles={_escape_subtitle_filter_path(request.subtitle_path)}:"
            f"force_style='{_force_style(settings)}'"
        )
        command = [
            ffmpeg_executable, "-y",
            "-i", str(request.input_video_path),
            "-vf", filter_arg,
            "-c:v", "libx264", "-pix_fmt", "yuv420p",
            "-c:a", "copy",
            str(request.output_path),
        ]
        return command


def _force_style(settings) -> str:
    parts = [
        f"FontSize={settings.font_size}",
        f"Outline={settings.outline_width}",
        f"MarginV={settings.bottom_margin}",
        f"Alignment={_ALIGNMENT_ASS_VALUE[settings.alignment]}",
    ]
    if settings.font_family is not None:
        parts.insert(0, f"FontName={settings.font_family}")
    return ",".join(parts)


def _escape_subtitle_filter_path(path: Path) -> str:
    raw = str(path)
    if "'" in raw:
        raise InvalidSubtitlePathError(
            f"Subtitle path contains a single quote, which this local ffmpeg/libass "
            f"build cannot reliably preserve inside a `subtitles` filter argument: {path}"
        )
    raw = raw.replace("\\", "/")
    raw = raw.replace(":", "\\:")
    return f"'{raw}'"


def _filter_listed(ffmpeg_filters_output: str, filter_name: str) -> bool:
    pattern = re.compile(rf"^\s*\S+\s+{re.escape(filter_name)}\s", re.MULTILINE)
    return pattern.search(ffmpeg_filters_output) is not None


def _parse_frame_rate(raw: str) -> int:
    if "/" in raw:
        numerator, denominator = raw.split("/", 1)
        denominator_value = int(denominator)
        if denominator_value == 0:
            return 0
        return round(int(numerator) / denominator_value)
    return round(float(raw))


def _resolve_executable(explicit_path: Path | None, name: str, error_cls: type[Exception]) -> str:
    if explicit_path is not None:
        if not Path(explicit_path).is_file():
            raise error_cls(f"Configured {name} executable not found at {explicit_path}")
        return str(explicit_path)
    resolved = shutil.which(name)
    if resolved is None:
        raise error_cls(
            f"{name} executable not found on PATH and no explicit path was configured -- "
            f"this module never downloads or installs one automatically"
        )
    return resolved


def _tail(text: str, max_len: int = _STDERR_TAIL_CHARS) -> str:
    text = text or ""
    if len(text) <= max_len:
        return text
    return "..." + text[-max_len:]
