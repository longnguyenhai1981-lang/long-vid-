"""MediaProbe: a small deterministic ffprobe/ffmpeg abstraction (Phase 32
requirement #19) rather than scattering subprocess calls throughout
app/media_qc/rules.py.

Three independent probe classes, each mirroring app/video_encoder/
encoder.py's own subprocess conventions exactly (argument lists only,
never `shell=True`; an injectable `ProcessRunner` for testability):

- `FFprobeClient.probe(path)` -> structured container/stream metadata.
- `FrameSampler.sample(path, duration_ms, count)` -> per-frame mean
  luminance + consecutive-frame differences, at deterministic evenly-
  spaced timeline positions (never every frame).
- `AudioAnalyzer.analyze(path)` -> ffmpeg `volumedetect`'s own
  mean_volume/max_volume, in dBFS.

Every one of these raises `MediaProbeError` (never propagates a raw
`OSError`/`subprocess` exception, and never a crash) when the SPECIFIC
file being inspected cannot be read/decoded -- app/media_qc/rules.py
always catches this and produces a FAIL QCCheckResult instead (Phase 32
requirement #23/#24: QC is diagnostic, never fail-fast). Executable
resolution failures (`FFmpegNotFoundError`/`FFprobeNotFoundError`) are a
different, infrastructure-level category that IS allowed to propagate --
see app/media_qc/errors.py's own module docstring.
"""

from __future__ import annotations

import json
import re
import shutil
import subprocess
import tempfile
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable, Protocol

from app.media_qc.errors import FFmpegNotFoundError, FFprobeNotFoundError, MediaProbeError

_VOLUME_RE = re.compile(r"(mean|max)_volume:\s*(-?inf|-?[\d.]+)\s*dB", re.IGNORECASE)


class ProcessResult(Protocol):
    returncode: int
    stdout: str
    stderr: str


ProcessRunner = Callable[[list[str]], ProcessResult]


def _default_runner(command: list[str]) -> subprocess.CompletedProcess:
    """The real subprocess runner -- argument list only, never
    `shell=True`, never a raw interpolated command string."""
    return subprocess.run(command, capture_output=True, text=True, timeout=120)


def _resolve_executable(explicit_path: Path | str | None, name: str, error_cls: type[Exception]) -> str:
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


@dataclass(frozen=True)
class MediaProbeResult:
    """Structured ffprobe output -- an internal data-transfer object,
    never persisted as a domain artifact (unlike MotilyModel-based
    contracts elsewhere in this project)."""

    duration_ms: int
    width: int
    height: int
    fps: float
    video_codec: str | None
    audio_codec: str | None
    pixel_format: str | None
    video_stream_count: int
    audio_stream_count: int
    raw: dict = field(default_factory=dict, compare=False)
    """The raw parsed ffprobe JSON -- kept available for diagnostics,
    never persisted as a production artifact itself."""


class FFprobeClient:
    def __init__(self, *, runner: ProcessRunner = _default_runner, ffprobe_path: Path | str | None = None):
        self._runner = runner
        self._ffprobe_executable = _resolve_executable(ffprobe_path, "ffprobe", FFprobeNotFoundError)

    def probe(self, media_path: Path) -> MediaProbeResult:
        if not media_path.is_file():
            raise MediaProbeError(f"File not found: {media_path}")
        if media_path.stat().st_size == 0:
            raise MediaProbeError(f"File is zero bytes: {media_path}")

        command = [
            self._ffprobe_executable, "-v", "error", "-print_format", "json",
            "-show_format", "-show_streams", str(media_path),
        ]
        result = self._runner(command)
        if result.returncode != 0:
            raise MediaProbeError(f"ffprobe could not read {media_path}: {_tail(result.stderr)}")

        try:
            raw = json.loads(result.stdout)
        except json.JSONDecodeError as exc:
            raise MediaProbeError(f"ffprobe returned unparseable output for {media_path}: {exc}") from exc

        streams = raw.get("streams", [])
        video_streams = [s for s in streams if s.get("codec_type") == "video"]
        audio_streams = [s for s in streams if s.get("codec_type") == "audio"]
        video_stream = video_streams[0] if video_streams else None
        audio_stream = audio_streams[0] if audio_streams else None

        try:
            duration_seconds = float(raw.get("format", {}).get("duration", 0.0))
        except (TypeError, ValueError):
            duration_seconds = 0.0

        return MediaProbeResult(
            duration_ms=round(duration_seconds * 1000),
            width=int(video_stream["width"]) if video_stream and "width" in video_stream else 0,
            height=int(video_stream["height"]) if video_stream and "height" in video_stream else 0,
            fps=_parse_frame_rate(video_stream.get("r_frame_rate", "0/1")) if video_stream else 0.0,
            video_codec=video_stream.get("codec_name") if video_stream else None,
            audio_codec=audio_stream.get("codec_name") if audio_stream else None,
            pixel_format=video_stream.get("pix_fmt") if video_stream else None,
            video_stream_count=len(video_streams),
            audio_stream_count=len(audio_streams),
            raw=raw,
        )


class FrameSampler:
    """Extracts a small, fixed number of frames at deterministic
    evenly-spaced timeline positions (never a full per-frame scan) into
    a dedicated temp directory that is always cleaned up (success or
    failure) via `tempfile.TemporaryDirectory`'s own context-manager
    guarantee -- no extracted frame is ever left behind, and none is
    ever persisted as a production artifact."""

    def __init__(self, *, runner: ProcessRunner = _default_runner, ffmpeg_path: Path | str | None = None):
        self._runner = runner
        self._ffmpeg_executable = _resolve_executable(ffmpeg_path, "ffmpeg", FFmpegNotFoundError)

    def sample_luminances_and_differences(
        self, video_path: Path, duration_ms: int, sample_count: int
    ) -> tuple[list[float], list[float]]:
        """Returns (luminances, consecutive_differences) -- `luminances`
        has exactly `sample_count` entries (mean 0-255 grayscale value
        per sampled frame); `consecutive_differences` has
        `sample_count - 1` entries (mean absolute grayscale difference
        between each adjacent pair of sampled frames, in timeline
        order)."""
        from PIL import Image, ImageChops, ImageStat, UnidentifiedImageError

        positions_ms = _sample_positions_ms(duration_ms, sample_count)

        try:
            with tempfile.TemporaryDirectory(prefix="motily_qc_") as temp_dir_name:
                temp_dir = Path(temp_dir_name)
                images = []
                for index, position_ms in enumerate(positions_ms):
                    frame_path = temp_dir / f"frame{index}.png"
                    command = [
                        self._ffmpeg_executable, "-y", "-hide_banner", "-loglevel", "error",
                        "-ss", f"{position_ms / 1000:.3f}", "-i", str(video_path),
                        "-frames:v", "1", str(frame_path),
                    ]
                    result = self._runner(command)
                    if result.returncode != 0 or not frame_path.is_file():
                        raise MediaProbeError(
                            f"Could not extract a sample frame at {position_ms}ms from "
                            f"{video_path}: {_tail(result.stderr)}"
                        )
                    try:
                        with Image.open(frame_path) as image:
                            images.append(image.convert("L").copy())
                    except (UnidentifiedImageError, OSError) as exc:
                        raise MediaProbeError(
                            f"Extracted sample frame at {position_ms}ms from {video_path} "
                            f"could not be read: {exc}"
                        ) from exc
        except OSError as exc:
            raise MediaProbeError(f"Could not create a temp directory for frame sampling: {exc}") from exc

        luminances = [ImageStat.Stat(image).mean[0] for image in images]
        differences = [
            ImageStat.Stat(ImageChops.difference(images[i], images[i + 1])).mean[0]
            for i in range(len(images) - 1)
        ]
        return luminances, differences


class AudioAnalyzer:
    """ffmpeg's own `volumedetect` filter -- a deterministic, built-in
    measurement (mean_volume/max_volume, in dBFS) requiring no custom DSP
    of any kind."""

    def __init__(self, *, runner: ProcessRunner = _default_runner, ffmpeg_path: Path | str | None = None):
        self._runner = runner
        self._ffmpeg_executable = _resolve_executable(ffmpeg_path, "ffmpeg", FFmpegNotFoundError)

    def analyze(self, media_path: Path) -> tuple[float, float]:
        """Returns (mean_volume_db, max_volume_db)."""
        command = [
            self._ffmpeg_executable, "-hide_banner", "-i", str(media_path),
            "-af", "volumedetect", "-f", "null", "-",
        ]
        result = self._runner(command)
        # volumedetect writes its own summary to stderr regardless of
        # overall ffmpeg exit code conventions for a null-muxer run;
        # ffmpeg itself still exits 0 on a normal decode.
        if result.returncode != 0:
            raise MediaProbeError(f"ffmpeg could not analyze audio in {media_path}: {_tail(result.stderr)}")

        values = {}
        for match in _VOLUME_RE.finditer(result.stderr):
            key, raw_value = match.group(1).lower(), match.group(2).lower()
            values[key] = float("-inf") if raw_value == "-inf" else float(raw_value)

        if "mean" not in values or "max" not in values:
            raise MediaProbeError(
                f"ffmpeg's volumedetect output did not contain both mean_volume and "
                f"max_volume for {media_path}"
            )
        return values["mean"], values["max"]


def _sample_positions_ms(duration_ms: int, sample_count: int) -> list[int]:
    """Evenly spaced positions between 10% and 90% of duration_ms
    inclusive -- exactly 10/30/50/70/90% for the default
    sample_count=5 (Phase 32's own preferred example)."""
    if sample_count == 1:
        return [round(duration_ms * 0.5)]
    step = 80.0 / (sample_count - 1)
    return [round(duration_ms * (10.0 + index * step) / 100.0) for index in range(sample_count)]


def _parse_frame_rate(raw: str) -> float:
    """Converts ffprobe's own rational r_frame_rate string (e.g.
    "30000/1001" or "30/1") to an exact float via true division -- never
    a raw string comparison."""
    if "/" in raw:
        numerator, denominator = raw.split("/", 1)
        try:
            denominator_value = int(denominator)
            if denominator_value == 0:
                return 0.0
            return int(numerator) / denominator_value
        except ValueError:
            return 0.0
    try:
        return float(raw)
    except ValueError:
        return 0.0


def _tail(text: str, max_len: int = 2000) -> str:
    text = text or ""
    if len(text) <= max_len:
        return text
    return "..." + text[-max_len:]
