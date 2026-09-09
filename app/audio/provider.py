"""The provider-independent TTS interface. Concrete providers implement only this."""

from __future__ import annotations

from typing import Protocol, runtime_checkable

from app.audio.models import TTSRequest, TTSResponse


@runtime_checkable
class TTSProvider(Protocol):
    def synthesize(self, request: TTSRequest) -> TTSResponse:
        ...
