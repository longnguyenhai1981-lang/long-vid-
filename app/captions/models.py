"""Typed contracts for Phase 31's optional local caption burn-in
execution (app/captions/burn_in.py). Mirrors app/video_encoder/models.py's
own shape: plain, dependency-free data contracts, no subprocess, no
filesystem I/O beyond what pydantic's own Path type implies --
app/captions/burn_in.py is the only module that acts on them.
"""

from __future__ import annotations

from pathlib import Path

from pydantic import Field, model_validator

from app.models.common import MotilyModel, non_blank

_SUPPORTED_OUTPUT_SUFFIXES = frozenset({".mp4"})
_SUPPORTED_SUBTITLE_SUFFIXES = frozenset({".srt"})
_ALIGNMENT_VALUES = frozenset({"BOTTOM_CENTER", "TOP_CENTER"})


class CaptionRenderSettings(MotilyModel):
    """A deliberately small execution-side style contract (requirement
    #14) -- no per-word color, no karaoke, no arbitrary x/y animation, no
    bouncing text, no per-speaker themes, no arbitrary shadow stacks.
    `font_family=None` (the default) lets the resolved ffmpeg/libass
    build's own fontconfig default apply -- this package never downloads,
    packages, or ships a font file (requirement #15)."""

    font_family: str | None = None
    font_size: int = 28
    bottom_margin: int = 40
    max_lines: int = 2
    outline_width: int = 2
    alignment: str = "BOTTOM_CENTER"

    @model_validator(mode="after")
    def _check_invariants(self) -> "CaptionRenderSettings":
        if self.font_family is not None:
            non_blank(self.font_family, "font_family")
        if self.font_size <= 0:
            raise ValueError(f"font_size must be > 0; got {self.font_size}")
        if self.bottom_margin < 0:
            raise ValueError(f"bottom_margin must be >= 0; got {self.bottom_margin}")
        if self.max_lines <= 0:
            raise ValueError(f"max_lines must be > 0; got {self.max_lines}")
        if self.outline_width < 0:
            raise ValueError(f"outline_width must be >= 0; got {self.outline_width}")
        if self.alignment not in _ALIGNMENT_VALUES:
            raise ValueError(
                f"alignment must be one of {sorted(_ALIGNMENT_VALUES)}; got {self.alignment!r}"
            )
        return self


class CaptionBurnInRequest(MotilyModel):
    """One burn-in job: an existing encoded MP4 + an existing SRT file,
    both already on disk -- this contract never triggers video/audio/
    subtitle generation of any kind."""

    input_video_path: Path
    subtitle_path: Path
    output_path: Path
    settings: CaptionRenderSettings = Field(default_factory=CaptionRenderSettings)
    ffmpeg_path: Path | None = None
    ffprobe_path: Path | None = None

    @model_validator(mode="after")
    def _check_invariants(self) -> "CaptionBurnInRequest":
        non_blank(str(self.input_video_path), "input_video_path")
        non_blank(str(self.subtitle_path), "subtitle_path")
        non_blank(str(self.output_path), "output_path")
        if self.subtitle_path.suffix.lower() not in _SUPPORTED_SUBTITLE_SUFFIXES:
            raise ValueError(
                f"unsupported subtitle format {self.subtitle_path.suffix!r}; expected one "
                f"of {sorted(_SUPPORTED_SUBTITLE_SUFFIXES)}"
            )
        if self.output_path.suffix.lower() not in _SUPPORTED_OUTPUT_SUFFIXES:
            raise ValueError(
                f"unsupported output format {self.output_path.suffix!r}; expected one of "
                f"{sorted(_SUPPORTED_OUTPUT_SUFFIXES)}"
            )
        return self


class CaptionBurnInResult(MotilyModel):
    output_path: Path
    width: int
    height: int
    fps: int
    duration_ms: int
    file_size_bytes: int
    video_codec: str
    audio_codec: str
    audio_stream_copied: bool
    ffmpeg_command: list[str]

    @model_validator(mode="after")
    def _check_invariants(self) -> "CaptionBurnInResult":
        if self.width <= 0 or self.height <= 0:
            raise ValueError("width/height must be > 0")
        if self.fps <= 0:
            raise ValueError("fps must be > 0")
        if self.duration_ms <= 0:
            raise ValueError("duration_ms must be > 0")
        if self.file_size_bytes <= 0:
            raise ValueError("file_size_bytes must be > 0")
        return self
