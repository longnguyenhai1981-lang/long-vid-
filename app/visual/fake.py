"""A deterministic fake visual provider for tests. No network calls, no API
keys, no image codec dependency.

Mirrors app/audio/fake.py's FakeTTSProvider exactly: fake responses carry
tiny, arbitrary placeholder bytes (not a real decodable PNG/JPG), just as
FakeTTSProvider's fixture audio_bytes are not real decodable WAV -- codec
validity is a concrete provider's concern (see docs/TECHNICAL_SPEC_v0.1.md,
Phase 19), not this contract's.
"""

from __future__ import annotations

from collections.abc import Sequence

from app.visual.errors import VisualProviderError
from app.visual.models import VisualRenderRequest, VisualRenderResponse


class FakeVisualProvider:
    """Returns/raises a fixed sequence of VisualRenderResponse/Exception
    values, in order.

    Records every VisualRenderRequest it receives so tests can assert on
    call count and on exactly what was sent -- e.g. that a beat's concept/
    primary_focus/secondary_elements were passed through unchanged."""

    def __init__(self, responses: Sequence[VisualRenderResponse | Exception]):
        self._responses = list(responses)
        self.received_requests: list[VisualRenderRequest] = []

    def render(self, request: VisualRenderRequest) -> VisualRenderResponse:
        self.received_requests.append(request)
        call_number = len(self.received_requests)
        if call_number > len(self._responses):
            raise VisualProviderError(
                f"FakeVisualProvider exhausted: no response configured for call {call_number}"
            )
        item = self._responses[call_number - 1]
        if isinstance(item, Exception):
            raise item
        return item

    @property
    def call_count(self) -> int:
        return len(self.received_requests)
