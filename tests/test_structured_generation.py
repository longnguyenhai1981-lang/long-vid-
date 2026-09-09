from __future__ import annotations

import pytest

from app.llm.errors import LLMProviderError, StructuredOutputExhaustedError
from app.llm.fake import FakeLLMProvider
from app.llm.models import LLMRequest, LLMResponse
from app.llm.structured import generate_structured
from app.models.common import GateEvaluation

VALID_JSON = '{"status": "PASS", "reason": "Đúng tinh thần Một Tí Lý"}'
SCHEMA_INVALID_JSON = '{"status": "BANANA", "reason": "vì lý do gì đó"}'
MALFORMED_JSON = '{"status": "PASS", "reason": '


def _request():
    return LLMRequest(user_prompt="Evaluate general audience fit for this idea", model="fake-model")


def _response(text: str) -> LLMResponse:
    return LLMResponse(text=text, provider="fake", model="fake-model")


def test_successful_generation_is_a_single_call():
    provider = FakeLLMProvider([_response(VALID_JSON)])
    result = generate_structured(provider, _request(), GateEvaluation, max_retries=2)

    assert result.attempts == 1
    assert isinstance(result.value, GateEvaluation)
    assert result.value.status.value == "PASS"
    assert result.response.text == VALID_JSON
    assert result.validation_failures == []
    assert provider.call_count == 1


def test_malformed_json_retries_then_succeeds():
    provider = FakeLLMProvider([_response(MALFORMED_JSON), _response(VALID_JSON)])
    result = generate_structured(provider, _request(), GateEvaluation, max_retries=2)

    assert provider.call_count == 2
    assert result.attempts == 2
    assert len(result.validation_failures) == 1
    assert result.validation_failures[0].error_type == "json_decode_error"
    assert result.value.status.value == "PASS"


def test_schema_invalid_json_retries_then_succeeds():
    provider = FakeLLMProvider([_response(SCHEMA_INVALID_JSON), _response(VALID_JSON)])
    result = generate_structured(provider, _request(), GateEvaluation, max_retries=2)

    assert provider.call_count == 2
    assert result.attempts == 2
    assert len(result.validation_failures) == 1
    assert result.validation_failures[0].error_type == "schema_validation_error"
    assert result.value.status.value == "PASS"


def test_retry_exhaustion_after_max_retries():
    provider = FakeLLMProvider(
        [
            _response(SCHEMA_INVALID_JSON),
            _response(MALFORMED_JSON),
            _response(SCHEMA_INVALID_JSON),
        ]
    )
    with pytest.raises(StructuredOutputExhaustedError) as exc_info:
        generate_structured(provider, _request(), GateEvaluation, max_retries=2)

    assert provider.call_count == 3
    assert exc_info.value.attempts == 3
    assert len(exc_info.value.validation_failures) == 3


def test_zero_max_retries_exhausts_after_exactly_one_call():
    provider = FakeLLMProvider([_response(SCHEMA_INVALID_JSON), _response(VALID_JSON)])
    with pytest.raises(StructuredOutputExhaustedError) as exc_info:
        generate_structured(provider, _request(), GateEvaluation, max_retries=0)

    assert provider.call_count == 1
    assert exc_info.value.attempts == 1
    assert len(exc_info.value.validation_failures) == 1


def test_correction_request_differs_and_carries_required_signals():
    provider = FakeLLMProvider([_response(SCHEMA_INVALID_JSON), _response(VALID_JSON)])
    generate_structured(provider, _request(), GateEvaluation, max_retries=2)

    assert provider.call_count == 2
    first_request, second_request = provider.received_requests
    assert second_request.user_prompt != first_request.user_prompt

    correction = second_request.user_prompt
    assert "failed" in correction.lower()
    assert "json" in correction.lower()
    assert '"status"' in correction  # the injected JSON schema names the field
    assert first_request.user_prompt in correction  # original task preserved verbatim


def test_provider_error_propagates_without_validation_retry():
    provider = FakeLLMProvider([LLMProviderError("upstream timeout"), _response(VALID_JSON)])
    with pytest.raises(LLMProviderError, match="upstream timeout"):
        generate_structured(provider, _request(), GateEvaluation, max_retries=2)
    assert provider.call_count == 1


def test_outer_json_code_fence_is_stripped():
    fenced = f"```json\n{VALID_JSON}\n```"
    provider = FakeLLMProvider([_response(fenced)])
    result = generate_structured(provider, _request(), GateEvaluation, max_retries=0)
    assert result.value.status.value == "PASS"
    assert provider.call_count == 1


def test_plain_code_fence_without_language_tag_is_stripped():
    fenced = f"```\n{VALID_JSON}\n```"
    provider = FakeLLMProvider([_response(fenced)])
    result = generate_structured(provider, _request(), GateEvaluation, max_retries=0)
    assert result.value.status.value == "PASS"


def test_json_embedded_in_prose_is_rejected_not_extracted():
    prose = f"Here you go:\n{VALID_JSON}"
    provider = FakeLLMProvider([_response(prose)])
    with pytest.raises(StructuredOutputExhaustedError) as exc_info:
        generate_structured(provider, _request(), GateEvaluation, max_retries=0)
    assert provider.call_count == 1
    assert exc_info.value.attempts == 1
