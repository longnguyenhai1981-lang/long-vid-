"""VoiceRenderManifest: metadata describing rendered voice-take audio files
for an already-locked ScriptPlan/VoicePlan pair.

The manifest stores metadata only -- render job identity, which lines/
chunk it covers, its file path, measured duration, and delivery-context
metadata carried through for a future assembly pass. It never stores raw
audio bytes (see app/audio/models.py's TTSResponse docstring for why) and
never stores a machine-specific absolute path -- file_path is relative to
whatever audio-output root the AudioFileStore that wrote it was configured
with (see docs/TECHNICAL_SPEC_v0.1.md, Phase 17).
"""

from __future__ import annotations

from datetime import datetime
from uuid import UUID, uuid4

from pydantic import Field, model_validator

from app.models.common import AudioFormat, MotilyModel, MusicState, non_blank


class RenderedVoiceTake(MotilyModel):
    render_job_id: str
    chunk_id: str
    take_number: int = Field(ge=1, le=3)
    line_ids: list[str] = Field(min_length=1)
    file_path: str
    duration_seconds: float | None = None
    provider_request_id: str | None = None
    music_state: MusicState
    sfx_opportunity: str | None = None

    @model_validator(mode="after")
    def _check_invariants(self) -> "RenderedVoiceTake":
        non_blank(self.render_job_id, "render_job_id")
        non_blank(self.chunk_id, "chunk_id")
        non_blank(self.file_path, "file_path")
        if self.duration_seconds is not None and self.duration_seconds <= 0:
            raise ValueError("duration_seconds must be > 0 when supplied")
        return self


class VoiceRenderManifest(MotilyModel):
    id: UUID = Field(default_factory=uuid4)
    script_plan_id: UUID
    voice_plan_id: UUID
    provider: str
    voice_id: str
    output_format: AudioFormat
    renders: list[RenderedVoiceTake] = Field(min_length=1)
    created_at: datetime

    @model_validator(mode="after")
    def _check_invariants(self) -> "VoiceRenderManifest":
        non_blank(self.provider, "provider")
        non_blank(self.voice_id, "voice_id")
        if self.created_at.tzinfo is None:
            raise ValueError("created_at must be timezone-aware")
        return self
