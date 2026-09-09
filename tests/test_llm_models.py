from __future__ import annotations

import pytest
from pydantic import ValidationError

from app.llm.models import LLMRequest, LLMResponse, TokenUsage


def test_valid_llm_request():
    req = LLMRequest(user_prompt="Explain antibiotic resistance", model="fake-model")
    assert req.system_prompt is None
    assert req.metadata == {}
    assert req.temperature is None
    assert req.max_output_tokens is None


def test_blank_user_prompt_rejected():
    with pytest.raises(ValidationError):
        LLMRequest(user_prompt="   ", model="fake-model")


def test_blank_model_rejected():
    with pytest.raises(ValidationError):
        LLMRequest(user_prompt="Explain X", model="   ")


def test_negative_temperature_rejected():
    with pytest.raises(ValidationError):
        LLMRequest(user_prompt="Explain X", model="fake-model", temperature=-0.1)


def test_zero_temperature_allowed():
    req = LLMRequest(user_prompt="Explain X", model="fake-model", temperature=0.0)
    assert req.temperature == 0.0


def test_non_positive_max_output_tokens_rejected():
    with pytest.raises(ValidationError):
        LLMRequest(user_prompt="Explain X", model="fake-model", max_output_tokens=0)
    with pytest.raises(ValidationError):
        LLMRequest(user_prompt="Explain X", model="fake-model", max_output_tokens=-10)


def test_llm_request_rejects_unknown_field():
    with pytest.raises(ValidationError):
        LLMRequest(user_prompt="Explain X", model="fake-model", not_a_real_field="oops")


def test_valid_llm_response_with_full_usage():
    response = LLMResponse(
        text="hello",
        provider="fake",
        model="fake-model",
        usage=TokenUsage(input_tokens=10, output_tokens=5, total_tokens=15),
    )
    assert response.usage.total_tokens == 15


def test_llm_response_default_usage_is_all_none():
    response = LLMResponse(text="hello", provider="fake", model="fake-model")
    assert response.usage.input_tokens is None
    assert response.usage.output_tokens is None
    assert response.usage.total_tokens is None


def test_blank_provider_rejected():
    with pytest.raises(ValidationError):
        LLMResponse(text="hello", provider="  ", model="fake-model")


def test_blank_model_rejected_on_response():
    with pytest.raises(ValidationError):
        LLMResponse(text="hello", provider="fake", model="  ")


def test_negative_token_count_rejected():
    with pytest.raises(ValidationError):
        TokenUsage(input_tokens=-1)
    with pytest.raises(ValidationError):
        TokenUsage(output_tokens=-1)
    with pytest.raises(ValidationError):
        TokenUsage(total_tokens=-1)


def test_contradictory_total_tokens_rejected():
    with pytest.raises(ValidationError):
        TokenUsage(input_tokens=10, output_tokens=5, total_tokens=999)


def test_consistent_total_tokens_allowed():
    usage = TokenUsage(input_tokens=10, output_tokens=5, total_tokens=15)
    assert usage.total_tokens == 15


def test_partial_usage_without_contradiction_check_allowed():
    usage = TokenUsage(input_tokens=10, output_tokens=None, total_tokens=None)
    assert usage.input_tokens == 10
