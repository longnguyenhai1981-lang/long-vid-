from __future__ import annotations

from types import SimpleNamespace

import pytest
from google.genai import errors as genai_errors
from pydantic import ValidationError

from app.visual.errors import VisualOutputError, VisualProviderError
from app.visual.models import VisualRenderRequest
from app.visual.provider import VisualProvider
from app.visual.providers.gemini import (
    DEFAULT_GEMINI_IMAGE_MODEL,
    GEMINI_IMAGE_PROVIDER_NAME,
    GeminiImageConfig,
    GeminiImageConfigurationError,
    GeminiImageProvider,
    GeminiImageUnsupportedMediaTypeError,
    GeminiImageUnsupportedOutputFormatError,
)


# ---------------------------------------------------------------------------
# Fake Gemini client (test-local; NOT a second production fake provider).
# Mirrors tests/test_gemini_tts_provider.py's generate_content response
# shape exactly -- Phase 20.1 migrated from generate_images to
# generate_content, so this file's fake client now mocks generate_content.
# ---------------------------------------------------------------------------


def _image_part(image_bytes=b"fake-png-bytes", mime_type="image/png"):
    inline_data = SimpleNamespace(data=image_bytes, mime_type=mime_type)
    return SimpleNamespace(inline_data=inline_data, text=None)


def _text_part(text="a text part, not an image"):
    return SimpleNamespace(inline_data=None, text=text)


def _fake_response(parts, response_id="req-123", prompt_feedback=None):
    content = SimpleNamespace(parts=parts)
    candidate = SimpleNamespace(content=content)
    return SimpleNamespace(
        candidates=[candidate], response_id=response_id, prompt_feedback=prompt_feedback
    )


class _FakeModels:
    def __init__(self, response=None, exc=None):
        self._response = response
        self._exc = exc
        self.calls: list[dict] = []

    def generate_content(self, **kwargs):
        self.calls.append(kwargs)
        if self._exc is not None:
            raise self._exc
        return self._response


class _FakeClient:
    def __init__(self, response=None, exc=None):
        self.models = _FakeModels(response, exc)


def _request(**overrides) -> VisualRenderRequest:
    fields = dict(
        render_job_id="V001_R1",
        beat_id="V001",
        media_type="GENERATED_STILL",
        concept="A bridge deck twisting violently in ordinary wind",
        primary_focus="The bridge deck",
        output_format="PNG",
    )
    fields.update(overrides)
    return VisualRenderRequest(**fields)


def _provider_with(response=None, exc=None, config=None) -> tuple[GeminiImageProvider, _FakeClient]:
    client = _FakeClient(response=response, exc=exc)
    provider = GeminiImageProvider(config or GeminiImageConfig(), client=client)
    return provider, client


# ---------------------------------------------------------------------------
# Section 36: protocol compatibility
# ---------------------------------------------------------------------------


def test_gemini_image_provider_satisfies_visual_provider_protocol():
    provider, _ = _provider_with(response=_fake_response([_image_part()]))
    assert isinstance(provider, VisualProvider)


# ---------------------------------------------------------------------------
# Section 37: config validation
# ---------------------------------------------------------------------------


def test_gemini_image_config_defaults():
    config = GeminiImageConfig()
    assert config.model == DEFAULT_GEMINI_IMAGE_MODEL
    assert config.model == "gemini-2.5-flash-image"
    assert config.api_key_env == "GEMINI_API_KEY"
    assert config.default_aspect_ratio == "16:9"


@pytest.mark.parametrize(
    "overrides",
    [
        {"model": "   "},
        {"api_key_env": "   "},
        {"default_aspect_ratio": "5:7"},
        {"default_aspect_ratio": "   "},
    ],
)
def test_gemini_image_config_rejects_invalid_values(overrides):
    with pytest.raises(ValidationError):
        GeminiImageConfig(**overrides)


@pytest.mark.parametrize(
    "aspect_ratio", ["1:1", "2:3", "3:2", "3:4", "4:3", "9:16", "16:9", "21:9"]
)
def test_gemini_image_config_accepts_every_supported_aspect_ratio(aspect_ratio):
    config = GeminiImageConfig(default_aspect_ratio=aspect_ratio)
    assert config.default_aspect_ratio == aspect_ratio


def test_gemini_image_config_has_no_api_key_field():
    assert "api_key" not in GeminiImageConfig.model_fields


def test_gemini_image_config_model_is_configurable():
    config = GeminiImageConfig(model="gemini-custom-image")
    assert config.model == "gemini-custom-image"


# ---------------------------------------------------------------------------
# Section 38: missing API key
# ---------------------------------------------------------------------------


def test_missing_api_key_raises_configuration_error_with_no_network_call(monkeypatch):
    monkeypatch.delenv("GEMINI_API_KEY", raising=False)

    with pytest.raises(GeminiImageConfigurationError) as exc_info:
        GeminiImageProvider(GeminiImageConfig())

    assert "GEMINI_API_KEY" in str(exc_info.value)


def test_custom_api_key_env_name_is_respected(monkeypatch):
    monkeypatch.delenv("CUSTOM_GEMINI_IMAGE_KEY", raising=False)
    with pytest.raises(GeminiImageConfigurationError) as exc_info:
        GeminiImageProvider(GeminiImageConfig(api_key_env="CUSTOM_GEMINI_IMAGE_KEY"))
    assert "CUSTOM_GEMINI_IMAGE_KEY" in str(exc_info.value)


# ---------------------------------------------------------------------------
# Section 39: injected client needs no key
# ---------------------------------------------------------------------------


def test_injected_client_requires_no_environment_key(monkeypatch):
    monkeypatch.delenv("GEMINI_API_KEY", raising=False)
    provider, client = _provider_with(response=_fake_response([_image_part()]))
    response = provider.render(_request())
    assert response.provider == GEMINI_IMAGE_PROVIDER_NAME
    assert len(client.models.calls) == 1


# ---------------------------------------------------------------------------
# Section 10 (corrective): the configured env var's exact value is passed
# explicitly to genai.Client(api_key=...), never left to SDK ambient
# GOOGLE_API_KEY/GEMINI_API_KEY environment precedence.
# ---------------------------------------------------------------------------


def test_configured_env_key_passed_explicitly_even_when_google_api_key_differs(monkeypatch):
    monkeypatch.setenv("GEMINI_API_KEY", "gemini-key-value")
    monkeypatch.setenv("GOOGLE_API_KEY", "google-key-value")

    captured = {}

    class _RecordingClient:
        def __init__(self, *, api_key=None, **kwargs):
            captured["api_key"] = api_key
            self.models = _FakeModels(response=_fake_response([_image_part()]))

    import app.visual.providers.gemini as gemini_module

    monkeypatch.setattr(gemini_module.genai, "Client", _RecordingClient)

    GeminiImageProvider(GeminiImageConfig())

    assert captured["api_key"] == "gemini-key-value"
    assert captured["api_key"] != "google-key-value"


# ---------------------------------------------------------------------------
# Section 40: GENERATED_STILL accepted
# ---------------------------------------------------------------------------


def test_generated_still_calls_client_exactly_once():
    provider, client = _provider_with(response=_fake_response([_image_part()]))
    provider.render(_request(media_type="GENERATED_STILL"))
    assert len(client.models.calls) == 1


# ---------------------------------------------------------------------------
# Section 41: DIAGRAM rejected before any SDK call
# ---------------------------------------------------------------------------


def test_diagram_rejected_before_any_sdk_call():
    provider, client = _provider_with(response=_fake_response([_image_part()]))
    with pytest.raises(GeminiImageUnsupportedMediaTypeError):
        provider.render(_request(media_type="DIAGRAM"))
    assert len(client.models.calls) == 0


# ---------------------------------------------------------------------------
# Section 42: any other media type rejected
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "media_type",
    ["ASSET_REUSE", "TI_STATE", "LIMITED_MOTION", "EVIDENCE_MEDIA", "AI_HERO_VIDEO"],
)
def test_every_other_media_type_rejected_before_any_sdk_call(media_type):
    provider, client = _provider_with(response=_fake_response([_image_part()]))
    with pytest.raises(GeminiImageUnsupportedMediaTypeError):
        provider.render(_request(media_type=media_type))
    assert len(client.models.calls) == 0


def test_jpg_output_format_rejected_before_any_sdk_call():
    provider, client = _provider_with(response=_fake_response([_image_part()]))
    with pytest.raises(GeminiImageUnsupportedOutputFormatError):
        provider.render(_request(output_format="JPG"))
    assert len(client.models.calls) == 0


# ---------------------------------------------------------------------------
# Section 43 / 44: prompt ownership -- content preserved, not replaced
# ---------------------------------------------------------------------------


def test_prompt_contains_concept_primary_focus_secondary_and_context_elements():
    provider, client = _provider_with(response=_fake_response([_image_part()]))
    provider.render(
        _request(
            concept="An exact, specific concept sentence",
            primary_focus="An exact, specific focus phrase",
            secondary_elements=["a crowd", "a red flag"],
            context_elements=["1940s newsreel style"],
        )
    )
    prompt = client.models.calls[0]["contents"]
    assert "An exact, specific concept sentence" in prompt
    assert "An exact, specific focus phrase" in prompt
    assert "a crowd" in prompt
    assert "a red flag" in prompt
    assert "1940s newsreel style" in prompt


def test_prompt_contains_style_guidance():
    provider, client = _provider_with(response=_fake_response([_image_part()]))
    provider.render(_request())
    prompt = client.models.calls[0]["contents"]
    assert "painterly editorial 2D" in prompt
    assert "not photorealistic" in prompt


def test_concept_text_is_preserved_verbatim_not_replaced():
    concept = "Ủa? Cái Gì Đang Xảy Ra Vậy???"
    provider, client = _provider_with(response=_fake_response([_image_part()]))
    provider.render(_request(concept=concept))
    prompt = client.models.calls[0]["contents"]
    assert concept in prompt  # exact case, exact punctuation, exact accents


def test_ti_state_mentions_ti_without_full_character_ontology():
    provider, client = _provider_with(response=_fake_response([_image_part()]))
    provider.render(_request(ti_state="CURIOUS"))
    prompt = client.models.calls[0]["contents"]
    assert "Tí" in prompt
    assert "curious" in prompt.lower()


def test_ti_state_present_preserves_historical_causality_rule():
    provider, client = _provider_with(response=_fake_response([_image_part()]))
    provider.render(_request(ti_state="NEUTRAL"))
    prompt = client.models.calls[0]["contents"]
    assert "never cause or alter" in prompt


def test_no_ti_state_omits_ti_direction_entirely():
    provider, client = _provider_with(response=_fake_response([_image_part()]))
    provider.render(_request(ti_state=None))
    prompt = client.models.calls[0]["contents"]
    assert "Tí" not in prompt


def test_aspect_ratio_and_model_passed_to_sdk_config():
    config = GeminiImageConfig(default_aspect_ratio="1:1")
    provider, client = _provider_with(response=_fake_response([_image_part()]), config=config)
    provider.render(_request())
    sent_config = client.models.calls[0]["config"]
    assert sent_config.image_config.aspect_ratio == "1:1"
    assert sent_config.response_modalities == ["IMAGE"]
    assert client.models.calls[0]["model"] == config.model


# ---------------------------------------------------------------------------
# Section 45: response bytes
# ---------------------------------------------------------------------------


def test_response_contains_exact_asset_bytes():
    provider, _ = _provider_with(response=_fake_response([_image_part(image_bytes=b"exact-fake-png-bytes")]))
    response = provider.render(_request())
    assert response.asset_bytes == b"exact-fake-png-bytes"
    assert response.provider == GEMINI_IMAGE_PROVIDER_NAME
    assert response.model == DEFAULT_GEMINI_IMAGE_MODEL
    assert response.output_format.value == "PNG"


def test_response_dimensions_are_none_not_faked():
    provider, _ = _provider_with(response=_fake_response([_image_part()]))
    response = provider.render(_request())
    assert response.width is None
    assert response.height is None


def test_response_id_is_used_as_provider_request_id_when_present():
    provider, _ = _provider_with(response=_fake_response([_image_part()], response_id="req-abc"))
    response = provider.render(_request())
    assert response.provider_request_id == "req-abc"


def test_provider_request_id_is_none_when_sdk_does_not_populate_it():
    provider, _ = _provider_with(response=_fake_response([_image_part()], response_id=None))
    response = provider.render(_request())
    assert response.provider_request_id is None


# ---------------------------------------------------------------------------
# Image extraction: first valid image part used, text parts ignored
# ---------------------------------------------------------------------------


def test_first_image_part_used_when_a_text_part_precedes_it():
    provider, _ = _provider_with(
        response=_fake_response([_text_part(), _image_part(image_bytes=b"the-real-image")])
    )
    response = provider.render(_request())
    assert response.asset_bytes == b"the-real-image"


def test_text_only_response_raises_visual_output_error():
    provider, _ = _provider_with(response=_fake_response([_text_part()]))
    with pytest.raises(VisualOutputError):
        provider.render(_request())


def test_empty_parts_list_raises_visual_output_error():
    provider, _ = _provider_with(response=_fake_response([]))
    with pytest.raises(VisualOutputError):
        provider.render(_request())


def test_no_candidates_raises_visual_output_error():
    provider, _ = _provider_with(response=SimpleNamespace(candidates=[], response_id=None, prompt_feedback=None))
    with pytest.raises(VisualOutputError):
        provider.render(_request())


def test_empty_image_bytes_raises_visual_output_error():
    provider, _ = _provider_with(response=_fake_response([_image_part(image_bytes=b"")]))
    with pytest.raises(VisualOutputError):
        provider.render(_request())


def test_blocked_prompt_feedback_reason_surfaced_in_error():
    prompt_feedback = SimpleNamespace(block_reason="SAFETY", block_reason_message="blocked")
    provider, _ = _provider_with(
        response=_fake_response([_text_part()], prompt_feedback=prompt_feedback)
    )
    with pytest.raises(VisualOutputError, match="SAFETY"):
        provider.render(_request())


# ---------------------------------------------------------------------------
# Section 46: MIME validation
# ---------------------------------------------------------------------------


def test_expected_image_mime_accepted():
    provider, _ = _provider_with(response=_fake_response([_image_part(mime_type="image/png")]))
    response = provider.render(_request())
    assert response.metadata["mime_type"] == "image/png"


def test_unexpected_mime_rejected():
    provider, _ = _provider_with(response=_fake_response([_image_part(mime_type="image/jpeg")]))
    with pytest.raises(VisualOutputError):
        provider.render(_request())


def test_missing_mime_does_not_raise():
    provider, _ = _provider_with(response=_fake_response([_image_part(mime_type=None)]))
    response = provider.render(_request())
    assert response.metadata == {}


# ---------------------------------------------------------------------------
# Section 48: SDK error translation
# ---------------------------------------------------------------------------


def test_sdk_api_error_translated_to_visual_provider_error_with_one_call():
    api_error = genai_errors.ServerError(
        code=503, response_json={"error": {"message": "backend overloaded", "status": "UNAVAILABLE"}}
    )
    provider, client = _provider_with(exc=api_error)

    with pytest.raises(VisualProviderError):
        provider.render(_request())

    assert len(client.models.calls) == 1


def test_sdk_error_message_omits_raw_response_details():
    api_error = genai_errors.ClientError(
        code=400,
        response_json={"error": {"message": "bad request", "status": "INVALID_ARGUMENT"}},
    )
    provider, _ = _provider_with(exc=api_error)

    with pytest.raises(VisualProviderError) as exc_info:
        provider.render(_request())

    message = str(exc_info.value)
    assert "bad request" in message
    assert "response_json" not in message


def test_httpx_transport_error_translated_to_visual_provider_error():
    import httpx

    provider, client = _provider_with(exc=httpx.ConnectError("connection refused"))

    with pytest.raises(VisualProviderError):
        provider.render(_request())

    assert len(client.models.calls) == 1


def test_unrelated_programming_error_is_not_translated():
    """Only google.genai.errors.APIError and httpx.HTTPError are translated
    to VisualProviderError -- anything else (a genuine bug) must stay
    visible, not be silently absorbed as if it were a normal provider
    failure."""
    provider, _ = _provider_with(exc=TypeError("unexpected programming bug"))
    with pytest.raises(TypeError):
        provider.render(_request())


def test_value_error_from_wrong_api_mode_is_not_silently_translated():
    """The exact real-world failure this correction fixes: a client-side
    ValueError raised before any HTTP call when the SDK method is used in
    the wrong API mode. It must never be misclassified as a retryable
    VisualProviderError."""
    provider, client = _provider_with(
        exc=ValueError(
            "This method is only supported in Gemini Enterprise Agent "
            "Platform mode, not in Gemini Developer API mode."
        )
    )
    with pytest.raises(ValueError):
        provider.render(_request())
    assert len(client.models.calls) == 1


# ---------------------------------------------------------------------------
# Section 49: no nested retry
# ---------------------------------------------------------------------------


def test_adapter_never_retries_itself_on_provider_error():
    api_error = genai_errors.ServerError(
        code=503, response_json={"error": {"message": "down", "status": "UNAVAILABLE"}}
    )
    provider, client = _provider_with(exc=api_error)

    with pytest.raises(VisualProviderError):
        provider.render(_request())

    # Exactly one call -- the adapter itself never retries; only
    # VisualRenderer owns retry policy.
    assert len(client.models.calls) == 1
