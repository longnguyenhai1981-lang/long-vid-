"""Provider-independent TTS request/response contracts.

Mirrors app/llm/models.py's LLMRequest/LLMResponse split exactly: a generic
request/response pair that stays the same across every concrete provider,
with vendor-specific detail confined to provider_options/metadata.
"""

from __future__ import annotations

from pydantic import Field, model_validator

from app.models.common import AudioFormat, Energy, MotilyModel, Pace, VoiceState, non_blank


class TTSRequest(MotilyModel):
    text: str
    voice_id: str
    voice_state: VoiceState
    pace: Pace
    energy: Energy
    output_format: AudioFormat
    metadata: dict[str, str] = Field(default_factory=dict)
    provider_options: dict[str, str] = Field(default_factory=dict)

    @model_validator(mode="after")
    def _check_invariants(self) -> "TTSRequest":
        non_blank(self.text, "text")
        non_blank(self.voice_id, "voice_id")
        return self


class TTSResponse(MotilyModel):
    """A provider's synthesis result.

    audio_bytes holds the raw encoded audio in memory only. TTSResponse is
    never persisted (no artifact type, no ModuleRun field, no ORM column
    stores it) and never JSON-serialized -- the renderer reads audio_bytes
    once, writes it to a file via AudioFileStore, and discards the response.
    Only the resulting file path is ever recorded (see
    app/models/audio.py's RenderedVoiceTake.file_path).
    """

    audio_bytes: bytes
    provider: str
    model: str | None = None
    audio_format: AudioFormat
    duration_seconds: float | None = None
    provider_request_id: str | None = None
    metadata: dict[str, str] = Field(default_factory=dict)

    @model_validator(mode="after")
    def _check_invariants(self) -> "TTSResponse":
        if not self.audio_bytes:
            raise ValueError("audio_bytes cannot be empty")
        non_blank(self.provider, "provider")
        if self.duration_seconds is not None and self.duration_seconds <= 0:
            raise ValueError("duration_seconds must be > 0 when supplied")
        return self
