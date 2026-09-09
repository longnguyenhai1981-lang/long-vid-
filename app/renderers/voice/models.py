"""Voice Renderer input/output contracts.

VoiceRenderManifest (app/models/audio.py) is the single approved business
output -- these are thin wrappers around it, not a competing schema.
"""

from __future__ import annotations

from uuid import UUID

from app.models.audio import VoiceRenderManifest
from app.models.common import MotilyModel

VOICE_RENDER_MANIFEST_ARTIFACT_TYPE = "voice_render_manifest"


class VoiceRendererInput(MotilyModel):
    project_id: UUID


class VoiceRendererResult(MotilyModel):
    manifest: VoiceRenderManifest
    module_run_id: UUID
    provider_call_count: int
    rendered_take_count: int
    total_duration_seconds: float | None = None
