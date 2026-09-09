"""A deterministic fake TTS provider for tests. No network calls, no API keys."""

from __future__ import annotations

from collections.abc import Sequence

from app.audio.errors import TTSProviderError
from app.audio.models import TTSRequest, TTSResponse


class FakeTTSProvider:
    """Returns/raises a fixed sequence of TTSResponse/Exception values, in order.

    Records every TTSRequest it receives so tests can assert on call count
    and on exactly what was sent -- e.g. that delivery metadata (voice_state,
    pace, energy) was passed through unchanged for a given chunk/take.
    """

    def __init__(self, responses: Sequence[TTSResponse | Exception]):
        self._responses = list(responses)
        self.received_requests: list[TTSRequest] = []

    def synthesize(self, request: TTSRequest) -> TTSResponse:
        self.received_requests.append(request)
        call_number = len(self.received_requests)
        if call_number > len(self._responses):
            raise TTSProviderError(
                f"FakeTTSProvider exhausted: no response configured for call {call_number}"
            )
        item = self._responses[call_number - 1]
        if isinstance(item, Exception):
            raise item
        return item

    @property
    def call_count(self) -> int:
        return len(self.received_requests)
