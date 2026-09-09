"""The provider-independent LLM interface. Concrete providers implement only this."""

from __future__ import annotations

from typing import Protocol, runtime_checkable

from app.llm.models import LLMRequest, LLMResponse


@runtime_checkable
class LLMProvider(Protocol):
    def generate(self, request: LLMRequest) -> LLMResponse:
        ...
