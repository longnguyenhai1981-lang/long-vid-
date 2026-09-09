"""SubtitleFileAsset / CaptionedVideoAsset: Phase 31's two small,
metadata-only persisted artifacts -- mirror EncodedVideoAsset's own
"metadata only, never raw bytes" convention (app/models/video.py)
exactly. SRT bytes live on disk under app/captions/storage.py's
SubtitleFileStore; the burned-in MP4's own bytes live under
app/video_encoder/storage.py's VideoFileStore -- neither is ever stored
in SQLite.
"""

from __future__ import annotations

from datetime import datetime
from uuid import UUID, uuid4

from pydantic import Field, model_validator

from app.models.common import MotilyModel, non_blank

_SUPPORTED_SUBTITLE_FORMATS = frozenset({"SRT"})


class SubtitleFileAsset(MotilyModel):
    """Phase 31 only ever produces SRT -- VTT/ASS are deliberately not
    added here (ASS may be used internally for FFmpeg burn-in styling,
    but SRT remains the sole exported/public artifact format; see
    app/captions/burn_in.py)."""

    id: UUID = Field(default_factory=uuid4)
    project_id: UUID
    caption_manifest_id: UUID
    format: str = "SRT"
    file_path: str
    cue_count: int
    total_duration_ms: int
    file_size_bytes: int
    encoding: str = "utf-8"
    created_at: datetime

    @model_validator(mode="after")
    def _check_invariants(self) -> "SubtitleFileAsset":
        non_blank(self.file_path, "file_path")
        if self.format.upper() not in _SUPPORTED_SUBTITLE_FORMATS:
            raise ValueError(
                f"unsupported subtitle format {self.format!r}; expected one of "
                f"{sorted(_SUPPORTED_SUBTITLE_FORMATS)}"
            )
        if self.cue_count <= 0:
            raise ValueError("cue_count must be > 0")
        if self.total_duration_ms <= 0:
            raise ValueError("total_duration_ms must be > 0")
        if self.file_size_bytes <= 0:
            raise ValueError("file_size_bytes must be > 0")
        if self.created_at.tzinfo is None:
            raise ValueError("created_at must be timezone-aware")
        return self


class CaptionedVideoAsset(MotilyModel):
    """A NEW encoded MP4 with captions burned in -- deliberately a
    separate model from EncodedVideoAsset (never an in-place update to
    it): burn-in produces a genuinely different pixel stream from a
    different source (an already-encoded MP4 + an SRT), not just added
    metadata on the same file. `source_encoded_video_asset_id` traces
    back to the EncodedVideoAsset this was burned from."""

    id: UUID = Field(default_factory=uuid4)
    project_id: UUID
    source_encoded_video_asset_id: UUID
    caption_manifest_id: UUID
    subtitle_file_asset_id: UUID
    file_path: str
    container: str = "mp4"
    video_codec: str
    audio_codec: str
    audio_stream_copied: bool
    width: int
    height: int
    fps: int
    duration_ms: int
    file_size_bytes: int
    created_at: datetime

    @model_validator(mode="after")
    def _check_invariants(self) -> "CaptionedVideoAsset":
        non_blank(self.file_path, "file_path")
        non_blank(self.video_codec, "video_codec")
        non_blank(self.audio_codec, "audio_codec")
        if self.width <= 0 or self.height <= 0:
            raise ValueError("width/height must be > 0")
        if self.fps <= 0:
            raise ValueError("fps must be > 0")
        if self.duration_ms <= 0:
            raise ValueError("duration_ms must be > 0")
        if self.file_size_bytes <= 0:
            raise ValueError("file_size_bytes must be > 0")
        if self.created_at.tzinfo is None:
            raise ValueError("created_at must be timezone-aware")
        return self
