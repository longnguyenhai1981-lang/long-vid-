"""Video Renderer input/output contracts.

EncodedVideoAsset (app/models/video.py) is the single approved business
output -- these are thin wrappers around it, not a competing schema.
"""

from __future__ import annotations

from uuid import UUID

from app.models.common import MotilyModel
from app.models.video import EncodedVideoAsset
from app.video_encoder.models import AudioAssetBindings

ENCODED_VIDEO_ASSET_ARTIFACT_TYPE = "encoded_video_asset"


class VideoRendererInput(MotilyModel):
    project_id: UUID
    audio_bindings: AudioAssetBindings | None = None
    """Phase 30: explicit opt-in for music/SFX mix execution. None (the
    default) makes VideoRenderer behave exactly like Phase 29 -- the
    TimelineManifest's own MUSIC_*/SFX_TRIGGER cues are never even
    forwarded to VideoEncoder, regardless of what they contain, so every
    pre-Phase-30 caller needs zero changes. Passing an AudioAssetBindings
    (even an empty one) engages real mix execution: the manifest's cues
    ARE forwarded, and a music cue with no bound asset then fails
    explicitly (MissingMusicAssetError) rather than silently playing no
    music."""


class VideoRendererResult(MotilyModel):
    asset: EncodedVideoAsset
    module_run_id: UUID
