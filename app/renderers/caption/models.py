"""CaptionBuilder input/output contracts.

CaptionManifest (app/models/caption.py) is the single approved business
output -- these are thin wrappers around it, not a competing schema.
"""

from __future__ import annotations

from uuid import UUID

from app.models.caption import CaptionManifest
from app.models.common import MotilyModel

CAPTION_MANIFEST_ARTIFACT_TYPE = "caption_manifest"


class CaptionBuilderInput(MotilyModel):
    project_id: UUID


class CaptionBuilderResult(MotilyModel):
    manifest: CaptionManifest
    module_run_id: UUID
