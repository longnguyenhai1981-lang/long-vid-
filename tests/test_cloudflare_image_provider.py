from __future__ import annotations

import base64

import httpx
import pytest
from pydantic import ValidationError

from app.visual.errors import VisualOutputError, VisualProviderError
from app.visual.models import VisualRenderRequest
from app.visual.provider import VisualProvider
from app.visual.providers.cloudflare import (
    CLOUDFLARE_PROVIDER_NAME,
    DEFAULT_CLOUDFLARE_IMAGE_MODEL,
    CloudflareImageConfig,
    CloudflareImageConfigurationError,
    CloudflareImageProvider,
    CloudflareImageUnsupportedMediaTypeError,
    CloudflareImageUnsupportedOutputFormatError,
)


# ---------------------------------------------------------------------------
# Fake Cloudflare HTTP client (test-local; NOT a second production fake
# provider). Exposes only .post(url, headers=, json=, timeout=), the
# same minimal shape a real httpx.Client/module offers.
# ---------------------------------------------------------------------------


def _b64(data: bytes) -> str:
    return base64.b64encode(data).decode("ascii")


class _FakeResponse:
    def __init__(self, status_code=200, payload=None, headers=None, raw_text=None):
        self.status_code = status_code
        self._payload = payload
        self.headers = headers or {}
        self._raw_text = raw_text

    def json(self):
        if self._raw_text is not None:
            raise ValueError("not valid json")
        return self._payload


def _success_response(image_bytes=b"fake-jpeg-bytes", headers=None):
    return _FakeResponse(
        200,
        {"result": {"image": _b64(image_bytes)}, "success": True, "errors": [], "messages": []},
        headers=headers,
    )


class _FakeClient:
    def __init__(self, response=None, exc=None):
        self._response = response
        self._exc = exc
        self.calls: list[dict] = []

    def post(self, url, headers=None, json=None, timeout=None):
        self.calls.append(dict(url=url, headers=headers, json=json, timeout=timeout))
        if self._exc is not None:
            raise self._exc
        return self._response


def _request(**overrides) -> VisualRenderRequest:
    fields = dict(
        render_job_id="V001_R1",
        beat_id="V001",
        media_type="GENERATED_STILL",
        concept="A bridge deck twisting violently in ordinary wind",
        primary_focus="The bridge deck",
        output_format="JPG",
    )
    fields.update(overrides)
    return VisualRenderRequest(**fields)


def _provider_with(response=None, exc=None, config=None) -> tuple[CloudflareImageProvider, _FakeClient]:
    client = _FakeClient(response=response, exc=exc)
    provider = CloudflareImageProvider(
        config or CloudflareImageConfig(), client=client, account_id="test-account", api_token="test-token"
    )
    return provider, client


# ---------------------------------------------------------------------------
# Section 20: config validation
# ---------------------------------------------------------------------------


def test_cloudflare_image_config_defaults():
    config = CloudflareImageConfig()
    assert config.model == DEFAULT_CLOUDFLARE_IMAGE_MODEL
    assert config.account_id_env == "CLOUDFLARE_ACCOUNT_ID"
    assert config.api_token_env == "CLOUDFLARE_API_TOKEN"
    assert config.timeout_seconds == 60.0
    assert config.steps == 4


@pytest.mark.parametrize(
    "overrides",
    [
        {"model": "   "},
        {"account_id_env": "   "},
        {"api_token_env": "   "},
        {"timeout_seconds": 0},
        {"timeout_seconds": -1},
        {"steps": 0},
        {"steps": 9},
        {"steps": -1},
    ],
)
def test_cloudflare_image_config_rejects_invalid_values(overrides):
    with pytest.raises(ValidationError):
        CloudflareImageConfig(**overrides)


@pytest.mark.parametrize("steps", [1, 4, 8])
def test_cloudflare_image_config_accepts_valid_steps(steps):
    config = CloudflareImageConfig(steps=steps)
    assert config.steps == steps


def test_cloudflare_image_config_has_no_secret_fields():
    assert "api_token" not in CloudflareImageConfig.model_fields
    assert "account_id" not in CloudflareImageConfig.model_fields


def test_cloudflare_image_config_model_is_configurable():
    config = CloudflareImageConfig(model="@cf/custom/model")
    assert config.model == "@cf/custom/model"


# ---------------------------------------------------------------------------
# Section 21: missing credentials
# ---------------------------------------------------------------------------


def test_missing_account_id_raises_configuration_error_with_no_network_call(monkeypatch):
    monkeypatch.delenv("CLOUDFLARE_ACCOUNT_ID", raising=False)
    monkeypatch.setenv("CLOUDFLARE_API_TOKEN", "some-token")

    with pytest.raises(CloudflareImageConfigurationError) as exc_info:
        CloudflareImageProvider(CloudflareImageConfig())

    assert "CLOUDFLARE_ACCOUNT_ID" in str(exc_info.value)


def test_missing_api_token_raises_configuration_error_with_no_network_call(monkeypatch):
    monkeypatch.setenv("CLOUDFLARE_ACCOUNT_ID", "some-account")
    monkeypatch.delenv("CLOUDFLARE_API_TOKEN", raising=False)

    with pytest.raises(CloudflareImageConfigurationError) as exc_info:
        CloudflareImageProvider(CloudflareImageConfig())

    assert "CLOUDFLARE_API_TOKEN" in str(exc_info.value)


def test_configuration_error_never_contains_a_secret_value(monkeypatch):
    monkeypatch.delenv("CLOUDFLARE_ACCOUNT_ID", raising=False)
    monkeypatch.setenv("CLOUDFLARE_API_TOKEN", "super-secret-token-value")

    with pytest.raises(CloudflareImageConfigurationError) as exc_info:
        CloudflareImageProvider(CloudflareImageConfig())

    assert "super-secret-token-value" not in str(exc_info.value)


# ---------------------------------------------------------------------------
# Section 22: injected client requires no env credentials, no network
# ---------------------------------------------------------------------------


def test_injected_client_and_explicit_credentials_require_no_environment(monkeypatch):
    monkeypatch.delenv("CLOUDFLARE_ACCOUNT_ID", raising=False)
    monkeypatch.delenv("CLOUDFLARE_API_TOKEN", raising=False)

    provider, client = _provider_with(response=_success_response())
    response = provider.render(_request())

    assert response.provider == CLOUDFLARE_PROVIDER_NAME
    assert len(client.calls) == 1


# ---------------------------------------------------------------------------
# Section 23: GENERATED_STILL accepted
# ---------------------------------------------------------------------------


def test_generated_still_calls_client_exactly_once():
    provider, client = _provider_with(response=_success_response())
    provider.render(_request(media_type="GENERATED_STILL"))
    assert len(client.calls) == 1


# ---------------------------------------------------------------------------
# Section 24: DIAGRAM rejected before any HTTP call
# ---------------------------------------------------------------------------


def test_diagram_rejected_before_any_http_call():
    provider, client = _provider_with(response=_success_response())
    with pytest.raises(CloudflareImageUnsupportedMediaTypeError):
        provider.render(_request(media_type="DIAGRAM"))
    assert len(client.calls) == 0


# ---------------------------------------------------------------------------
# Section 25: every other media type rejected
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "media_type",
    ["ASSET_REUSE", "TI_STATE", "LIMITED_MOTION", "EVIDENCE_MEDIA", "AI_HERO_VIDEO"],
)
def test_every_other_media_type_rejected_before_any_http_call(media_type):
    provider, client = _provider_with(response=_success_response())
    with pytest.raises(CloudflareImageUnsupportedMediaTypeError):
        provider.render(_request(media_type=media_type))
    assert len(client.calls) == 0


def test_png_output_format_rejected_before_any_http_call():
    provider, client = _provider_with(response=_success_response())
    with pytest.raises(CloudflareImageUnsupportedOutputFormatError):
        provider.render(_request(output_format="PNG"))
    assert len(client.calls) == 0


# ---------------------------------------------------------------------------
# Section 26: request shape
# ---------------------------------------------------------------------------


def test_request_contains_exact_endpoint_auth_header_prompt_and_steps():
    config = CloudflareImageConfig(steps=6)
    provider, client = _provider_with(response=_success_response(), config=config)
    provider.render(_request())

    call = client.calls[0]
    assert call["url"] == (
        "https://api.cloudflare.com/client/v4/accounts/test-account/ai/run/"
        "@cf/black-forest-labs/flux-1-schnell"
    )
    assert call["headers"]["Authorization"] == "Bearer test-token"
    assert call["json"]["steps"] == 6
    assert "prompt" in call["json"]
    assert call["timeout"] == config.timeout_seconds


def test_default_model_used_when_not_overridden():
    provider, client = _provider_with(response=_success_response())
    provider.render(_request())
    assert DEFAULT_CLOUDFLARE_IMAGE_MODEL in client.calls[0]["url"]


# ---------------------------------------------------------------------------
# Section 27: prompt ownership -- content preserved, style guidance present
# ---------------------------------------------------------------------------


def test_prompt_contains_concept_primary_focus_secondary_and_context_elements():
    provider, client = _provider_with(response=_success_response())
    provider.render(
        _request(
            concept="An exact, specific concept sentence",
            primary_focus="An exact, specific focus phrase",
            secondary_elements=["a crowd", "a red flag"],
            context_elements=["1940s newsreel style"],
        )
    )
    prompt = client.calls[0]["json"]["prompt"]
    assert "An exact, specific concept sentence" in prompt
    assert "An exact, specific focus phrase" in prompt
    assert "a crowd" in prompt
    assert "a red flag" in prompt
    assert "1940s newsreel style" in prompt


def test_prompt_contains_locked_style_guidance():
    provider, client = _provider_with(response=_success_response())
    provider.render(_request())
    prompt = client.calls[0]["json"]["prompt"]
    assert "painterly editorial 2D" in prompt
    assert "not photorealistic" in prompt


def test_concept_text_is_preserved_verbatim_not_replaced():
    concept = "Ủa? Cái Gì Đang Xảy Ra Vậy???"
    provider, client = _provider_with(response=_success_response())
    provider.render(_request(concept=concept))
    prompt = client.calls[0]["json"]["prompt"]
    assert concept in prompt


def test_ti_state_present_preserves_historical_causality_rule():
    provider, client = _provider_with(response=_success_response())
    provider.render(_request(ti_state="NEUTRAL"))
    prompt = client.calls[0]["json"]["prompt"]
    assert "Tí" in prompt
    assert "never cause or alter" in prompt


def test_no_ti_state_omits_ti_direction_entirely():
    provider, client = _provider_with(response=_success_response())
    provider.render(_request(ti_state=None))
    prompt = client.calls[0]["json"]["prompt"]
    assert "Tí" not in prompt


def test_gemini_and_cloudflare_prompt_builders_are_independent_functions():
    """Section 10's "do not share a giant prompt string" requirement,
    proven structurally: the two adapters' prompt builders are distinct
    module-level objects, not one shared function/constant."""
    from app.visual.providers.cloudflare import _build_prompt as cloudflare_build_prompt
    from app.visual.providers.cloudflare import _STYLE_GUIDANCE as cloudflare_style
    from app.visual.providers.gemini import _build_prompt as gemini_build_prompt
    from app.visual.providers.gemini import _STYLE_GUIDANCE as gemini_style

    assert cloudflare_build_prompt is not gemini_build_prompt
    assert cloudflare_style is not gemini_style


# ---------------------------------------------------------------------------
# Section 28: valid response
# ---------------------------------------------------------------------------


def test_response_contains_exact_asset_bytes_and_identity_fields():
    provider, _ = _provider_with(response=_success_response(image_bytes=b"exact-fake-jpeg-bytes"))
    response = provider.render(_request())
    assert response.asset_bytes == b"exact-fake-jpeg-bytes"
    assert response.provider == CLOUDFLARE_PROVIDER_NAME
    assert response.model == DEFAULT_CLOUDFLARE_IMAGE_MODEL
    assert response.output_format.value == "JPG"


def test_response_dimensions_are_none_not_faked():
    provider, _ = _provider_with(response=_success_response())
    response = provider.render(_request())
    assert response.width is None
    assert response.height is None


def test_cf_ray_header_used_as_provider_request_id_when_present():
    provider, _ = _provider_with(response=_success_response(headers={"cf-ray": "abc123-XYZ"}))
    response = provider.render(_request())
    assert response.provider_request_id == "abc123-XYZ"


def test_provider_request_id_is_none_when_no_ray_header():
    provider, _ = _provider_with(response=_success_response(headers={}))
    response = provider.render(_request())
    assert response.provider_request_id is None


def test_metadata_reports_jpeg_mime_type():
    provider, _ = _provider_with(response=_success_response())
    response = provider.render(_request())
    assert response.metadata["mime_type"] == "image/jpeg"


# ---------------------------------------------------------------------------
# Section 29: invalid base64
# ---------------------------------------------------------------------------


def test_invalid_base64_raises_visual_output_error():
    bad_response = _FakeResponse(
        200, {"result": {"image": "not-valid-base64!!!"}, "success": True, "errors": []}
    )
    provider, _ = _provider_with(response=bad_response)
    with pytest.raises(VisualOutputError):
        provider.render(_request())


# ---------------------------------------------------------------------------
# Section 30: empty image
# ---------------------------------------------------------------------------


def test_empty_image_field_raises_visual_output_error():
    empty_response = _FakeResponse(200, {"result": {"image": ""}, "success": True, "errors": []})
    provider, _ = _provider_with(response=empty_response)
    with pytest.raises(VisualOutputError):
        provider.render(_request())


def test_missing_result_object_raises_visual_output_error():
    response = _FakeResponse(200, {"success": True, "errors": []})
    provider, _ = _provider_with(response=response)
    with pytest.raises(VisualOutputError):
        provider.render(_request())


def test_non_json_response_raises_visual_output_error():
    response = _FakeResponse(200, None, raw_text="<html>not json</html>")
    provider, _ = _provider_with(response=response)
    with pytest.raises(VisualOutputError):
        provider.render(_request())


# ---------------------------------------------------------------------------
# Section 31: API failure envelope (success=false)
# ---------------------------------------------------------------------------


def test_success_false_envelope_translated_to_visual_provider_error():
    response = _FakeResponse(
        200,
        {
            "success": False,
            "errors": [{"code": 5007, "message": "Model overloaded"}],
            "result": None,
        },
    )
    provider, client = _provider_with(response=response)
    with pytest.raises(VisualProviderError, match="5007"):
        provider.render(_request())
    assert len(client.calls) == 1


def test_success_false_error_message_omits_full_raw_response():
    huge_field = "x" * 5000
    response = _FakeResponse(
        200,
        {
            "success": False,
            "errors": [{"code": 1000, "message": huge_field}],
            "result": None,
        },
    )
    provider, _ = _provider_with(response=response)
    with pytest.raises(VisualProviderError) as exc_info:
        provider.render(_request())
    assert len(str(exc_info.value)) < len(huge_field)


# ---------------------------------------------------------------------------
# Section 32: HTTP-level errors
# ---------------------------------------------------------------------------


def test_http_status_error_translated_to_visual_provider_error():
    response = _FakeResponse(500, {"success": False, "errors": [{"code": 1101, "message": "internal error"}]})
    provider, client = _provider_with(response=response)
    with pytest.raises(VisualProviderError):
        provider.render(_request())
    assert len(client.calls) == 1


def test_rate_limit_status_translated_to_visual_provider_error():
    response = _FakeResponse(429, {"success": False, "errors": [{"code": 10000, "message": "rate limited"}]})
    provider, client = _provider_with(response=response)
    with pytest.raises(VisualProviderError):
        provider.render(_request())
    assert len(client.calls) == 1


def test_httpx_transport_error_translated_to_visual_provider_error():
    provider, client = _provider_with(exc=httpx.ConnectError("connection refused"))
    with pytest.raises(VisualProviderError):
        provider.render(_request())
    assert len(client.calls) == 1


def test_unrelated_programming_error_is_not_translated():
    """Only httpx.HTTPError (network) and an explicit non-2xx status are
    translated to VisualProviderError -- anything else (a genuine bug)
    must stay visible."""
    provider, _ = _provider_with(exc=TypeError("unexpected programming bug"))
    with pytest.raises(TypeError):
        provider.render(_request())


# ---------------------------------------------------------------------------
# Section 33: no Pillow, dimensions stay None (also see response tests above)
# ---------------------------------------------------------------------------


def test_no_pillow_import_anywhere_in_this_module():
    import app.visual.providers.cloudflare as cloudflare_module

    source = open(cloudflare_module.__file__, encoding="utf-8").read()
    assert "import PIL" not in source
    assert "from PIL" not in source


# ---------------------------------------------------------------------------
# No nested retry
# ---------------------------------------------------------------------------


def test_adapter_never_retries_itself_on_provider_error():
    provider, client = _provider_with(exc=httpx.TimeoutException("timed out"))
    with pytest.raises(VisualProviderError):
        provider.render(_request())
    assert len(client.calls) == 1


def test_protocol_conformance():
    provider, _ = _provider_with(response=_success_response())
    assert isinstance(provider, VisualProvider)
