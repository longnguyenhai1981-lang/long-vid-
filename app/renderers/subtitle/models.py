"""SubtitleRenderer input/output contracts.

SubtitleFileAsset/CaptionedVideoAsset (app/models/subtitle.py) are the
single approved business outputs -- these are thin wrappers around them,
not a competing schema.
"""

from __future__ import annotations

from uuid import UUID

from app.captions.models import CaptionRenderSettings
from app.models.common import MotilyModel
from app.models.subtitle import CaptionedVideoAsset, SubtitleFileAsset

SUBTITLE_FILE_ASSET_ARTIFACT_TYPE = "subtitle_file_asset"
CAPTIONED_VIDEO_ASSET_ARTIFACT_TYPE = "captioned_video_asset"


class SubtitleRendererInput(MotilyModel):
    project_id: UUID
    burn_in_settings: CaptionRenderSettings | None = None
    """Phase 31: explicit opt-in for local FFmpeg caption burn-in. None
    (the default) means "SRT export only" -- no EncodedVideoAsset is even
    loaded, and no ffmpeg call is made. Passing a CaptionRenderSettings
    (even the all-defaults one) opts into burning captions into the
    project's current EncodedVideoAsset, producing a new
    CaptionedVideoAsset."""


class SubtitleRendererResult(MotilyModel):
    subtitle_asset: SubtitleFileAsset
    captioned_video_asset: CaptionedVideoAsset | None = None
    module_run_id: UUID
