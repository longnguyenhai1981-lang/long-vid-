from __future__ import annotations

import pytest
from pydantic import ValidationError

from app.visual.config import VisualSettings


def test_valid_visual_settings_defaults():
    settings = VisualSettings(provider="fake-visual")
    assert settings.output_format.value == "PNG"
    assert settings.max_provider_retries == 1


def test_blank_provider_rejected():
    with pytest.raises(ValidationError):
        VisualSettings(provider="  ")


def test_negative_max_provider_retries_rejected():
    with pytest.raises(ValidationError):
        VisualSettings(provider="fake-visual", max_provider_retries=-1)


def test_zero_max_provider_retries_allowed():
    settings = VisualSettings(provider="fake-visual", max_provider_retries=0)
    assert settings.max_provider_retries == 0


def test_no_api_key_field():
    assert "api_key" not in VisualSettings.model_fields


def test_visual_settings_rejects_unknown_field():
    with pytest.raises(ValidationError):
        VisualSettings(provider="fake-visual", not_a_real_field="oops")
