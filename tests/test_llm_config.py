from __future__ import annotations

import pytest
from pydantic import ValidationError

from app.llm.config import LLMSettings


def test_valid_llm_settings_with_defaults():
    settings = LLMSettings(provider="fake", default_model="fake-model")
    assert settings.max_structured_retries == 2
    assert settings.default_temperature is None
    assert settings.default_max_output_tokens is None


def test_blank_provider_rejected():
    with pytest.raises(ValidationError):
        LLMSettings(provider="  ", default_model="fake-model")


def test_blank_default_model_rejected():
    with pytest.raises(ValidationError):
        LLMSettings(provider="fake", default_model="  ")


def test_negative_max_structured_retries_rejected():
    with pytest.raises(ValidationError):
        LLMSettings(provider="fake", default_model="fake-model", max_structured_retries=-1)


def test_zero_max_structured_retries_allowed():
    settings = LLMSettings(provider="fake", default_model="fake-model", max_structured_retries=0)
    assert settings.max_structured_retries == 0


def test_llm_settings_has_no_api_key_field():
    assert "api_key" not in LLMSettings.model_fields
