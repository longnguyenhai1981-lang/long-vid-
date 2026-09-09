"""Minimal typed TTS provider configuration.

No API key field: Phase 17 ships no concrete remote provider to consume one
(see docs/TECHNICAL_SPEC_v0.1.md). When a real provider is added, its key
must be read from an environment variable at call time -- never hard-coded,
logged, persisted to SQLite, or placed in ModuleRun/manifest metadata.
"""

from __future__ import annotations

from pydantic import model_validator

from app.models.common import AudioFormat, MotilyModel, non_blank


class TTSSettings(MotilyModel):
    provider: str
    voice_id: str
    output_format: AudioFormat = AudioFormat.WAV
    max_provider_retries: int = 1
    default_model: str | None = None

    @model_validator(mode="after")
    def _check_invariants(self) -> "TTSSettings":
        non_blank(self.provider, "provider")
        non_blank(self.voice_id, "voice_id")
        if self.max_provider_retries < 0:
            raise ValueError("max_provider_retries must be >= 0")
        return self


# Selected by human listening after the Phase 18 Gemini Vietnamese voice
# evaluation (scripts/evaluate_gemini_voices.py). This is an
# application-level production preference only -- not a TTSProvider or
# Gemini-provider contract requirement. TTSSettings.voice_id stays a plain
# configurable field; callers may still pass any other voice_id.
CANONICAL_TI_VOICE_ID = "Puck"
