from __future__ import annotations

import pytest
from pydantic import ValidationError

from app.audio.config import TTSSettings
from app.models.common import AudioFormat


def test_valid_tts_settings_with_defaults():
    settings = TTSSettings(provider="fake-tts", voice_id="voice-01")
    assert settings.output_format == AudioFormat.WAV
    assert settings.max_provider_retries == 1
    assert settings.default_model is None


def test_blank_provider_rejected():
    with pytest.raises(ValidationError):
        TTSSettings(provider="  ", voice_id="voice-01")


def test_blank_voice_id_rejected():
    with pytest.raises(ValidationError):
        TTSSettings(provider="fake-tts", voice_id="  ")


def test_negative_max_provider_retries_rejected():
    with pytest.raises(ValidationError):
        TTSSettings(provider="fake-tts", voice_id="voice-01", max_provider_retries=-1)


def test_zero_max_provider_retries_allowed():
    settings = TTSSettings(provider="fake-tts", voice_id="voice-01", max_provider_retries=0)
    assert settings.max_provider_retries == 0


def test_tts_settings_has_no_api_key_field():
    assert "api_key" not in TTSSettings.model_fields
