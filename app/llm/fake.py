"""A deterministic fake LLM provider for tests. No network calls, no API keys."""

from __future__ import annotations

from collections.abc import Sequence

from app.llm.errors import LLMProviderError
from app.llm.models import LLMRequest, LLMResponse


class FakeLLMProvider:
    """Returns/raises a fixed sequence of LLMResponse/Exception values, in order.

    Records every LLMRequest it receives so tests can assert on call count and
    on exactly what was sent -- e.g. that a retry's correction request differs
    from the first request.
    """

    def __init__(self, responses: Sequence[LLMResponse | Exception]):
        self._responses = list(responses)
        self.received_requests: list[LLMRequest] = []

    def generate(self, request: LLMRequest) -> LLMResponse:
        self.received_requests.append(request)
        call_number = len(self.received_requests)
        if call_number > len(self._responses):
            raise LLMProviderError(
                f"FakeLLMProvider exhausted: no response configured for call {call_number}"
            )
        item = self._responses[call_number - 1]
        if isinstance(item, Exception):
            raise item
        return item

    @property
    def call_count(self) -> int:
        return len(self.received_requests)
