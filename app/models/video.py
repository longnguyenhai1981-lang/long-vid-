"""EncodedVideoAsset: metadata describing one locally-encoded MP4 file for
an already-locked TimelineManifest (Phase 28).

Mirrors every prior manifest's own "metadata only, never raw bytes"
convention (see e.g. app/models/visual_render.py's RenderedVisualAsset
docstring): the MP4 bytes themselves are never stored in SQLite, only a
relative file path -- relative to whatever video-output root the caller
(app/renderers/video/renderer.py) is configured with. TimelineManifest
remains the single source of truth for timing; this model only records
what was actually encoded from it and where.

Phase 29 adds crossfade_count/motion_profile_used -- both optional,
default to "none used" -- propagated unchanged from VideoEncodeResult
rather than introducing a second, competing video-output model.

Phase 30 adds has_music/sfx_event_count/music_cue_count, same
propagate-unchanged-from-VideoEncodeResult convention -- still no second,
competing media-output artifact.
"""

from __future__ import annotations

from datetime import datetime
from uuid import UUID, uuid4

from pydantic import Field, model_validator

from app.models.common import MotilyModel, non_blank
from app.models.timeline import VisualMotionType

_SUPPORTED_CONTAINERS = frozenset({"mp4"})


class EncodedVideoAsset(MotilyModel):
    id: UUID = Field(default_factory=uuid4)
    project_id: UUID
    timeline_manifest_id: UUID
    file_path: str
    container: str = "mp4"
    video_codec: str
    audio_codec: str
    width: int
    height: int
    fps: int
    duration_ms: int
    file_size_bytes: int
    created_at: datetime
    crossfade_count: int = 0
    """Phase 29: propagated from VideoEncodeResult.crossfade_count -- how
    many adjacent-segment joins in this encode were a real CROSSFADE."""
    motion_profile_used: list[VisualMotionType] = Field(default_factory=list)
    """Phase 29: propagated from VideoEncodeResult.motion_profile_used --
    the distinct non-STATIC VisualMotionType values actually applied."""
    has_music: bool = False
    """Phase 30: propagated from VideoEncodeResult.has_music."""
    sfx_event_count: int = 0
    """Phase 30: propagated from VideoEncodeResult.sfx_event_count."""
    music_cue_count: int = 0
    """Phase 30: propagated from VideoEncodeResult.music_cue_count."""

    @model_validator(mode="after")
    def _check_invariants(self) -> "EncodedVideoAsset":
        non_blank(self.file_path, "file_path")
        non_blank(self.video_codec, "video_codec")
        non_blank(self.audio_codec, "audio_codec")
        if self.container.lower() not in _SUPPORTED_CONTAINERS:
            raise ValueError(
                f"unsupported container {self.container!r}; expected one of {sorted(_SUPPORTED_CONTAINERS)}"
            )
        if self.width <= 0 or self.height <= 0:
            raise ValueError("width/height must be > 0")
        if self.fps <= 0:
            raise ValueError("fps must be > 0")
        if self.duration_ms <= 0:
            raise ValueError("duration_ms must be > 0")
        if self.file_size_bytes <= 0:
            raise ValueError("file_size_bytes must be > 0")
        if self.crossfade_count < 0:
            raise ValueError("crossfade_count must be >= 0")
        if self.sfx_event_count < 0:
            raise ValueError("sfx_event_count must be >= 0")
        if self.music_cue_count < 0:
            raise ValueError("music_cue_count must be >= 0")
        if self.created_at.tzinfo is None:
            raise ValueError("created_at must be timezone-aware")
        return self
