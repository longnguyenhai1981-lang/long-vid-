from __future__ import annotations

import pytest

from app.llm.errors import LLMProviderError
from app.llm.fake import FakeLLMProvider
from app.llm.models import LLMRequest, LLMResponse
from app.llm.provider import LLMProvider


def _response(text="{}"):
    return LLMResponse(text=text, provider="fake", model="fake-model")


def _request(prompt="hello"):
    return LLMRequest(user_prompt=prompt, model="fake-model")


def test_fake_provider_satisfies_llm_provider_protocol():
    provider = FakeLLMProvider([_response()])
    assert isinstance(provider, LLMProvider)


def test_fake_provider_returns_responses_in_sequence():
    r1, r2 = _response("one"), _response("two")
    provider = FakeLLMProvider([r1, r2])
    assert provider.generate(_request("a")) is r1
    assert provider.generate(_request("b")) is r2


def test_fake_provider_raises_configured_exception_then_continues():
    provider = FakeLLMProvider([LLMProviderError("boom"), _response()])
    with pytest.raises(LLMProviderError, match="boom"):
        provider.generate(_request())
    assert provider.generate(_request()) is not None


def test_fake_provider_records_received_requests_in_order():
    provider = FakeLLMProvider([_response(), _response()])
    req1, req2 = _request("first"), _request("second")
    provider.generate(req1)
    provider.generate(req2)
    assert provider.received_requests == [req1, req2]
    assert provider.call_count == 2


def test_fake_provider_exhaustion_raises_provider_error():
    provider = FakeLLMProvider([_response()])
    provider.generate(_request())
    with pytest.raises(LLMProviderError):
        provider.generate(_request())
