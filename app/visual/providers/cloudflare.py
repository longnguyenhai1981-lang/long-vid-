"""Cloudflare Workers AI image provider adapter -- the second real
VisualProvider implementation (Phase 20.2), added because the first real
one (`GeminiImageProvider`, `app/visual/providers/gemini.py`) is
architecturally correct but currently blocked by this project's Gemini
account having zero free-tier image-generation quota. This module exists
to unblock real visual evaluation on a free-tier-friendly provider while
`GeminiImageProvider` stays exactly as-is -- it is not removed, weakened,
or replaced.

This module is the ONLY place in the repository that constructs a
Cloudflare Workers AI request. Uses the existing `httpx` dependency
(already present transitively via `google-genai`, already imported
directly by `app/audio/providers/gemini.py` and
`app/visual/providers/gemini.py` for its error types) -- no new SDK
dependency was added; Cloudflare Workers AI is a plain REST API with no
official Python SDK used here.

Mirrors app/visual/providers/gemini.py's policy exactly: no retry here
(VisualRenderer already owns VisualProviderError retry), narrow error
translation, environment-only secret loading, eager credential
resolution so a missing account id/token fails immediately with zero
network calls, and a provider-owned prompt translator that never shares
its actual style-guidance text with the Gemini adapter (same locked
visual language, independently defined here).

Endpoint (Cloudflare Workers AI REST API, official pattern):

    POST https://api.cloudflare.com/client/v4/accounts/{ACCOUNT_ID}/ai/run/{MODEL_NAME}
    Authorization: Bearer {API_TOKEN}

Default model `@cf/black-forest-labs/flux-1-schnell` returns image bytes
as base64 in `result.image`, and its official examples treat that output
as JPEG -- so this adapter supports GENERATED_STILL + JPG only, exactly
like `GeminiImageProvider` supports GENERATED_STILL + PNG only. Neither
`VisualOutputFormat` nor `VisualRenderer` changed to accommodate this:
callers simply configure
`VisualSettings(output_format=VisualOutputFormat.JPG)` when wiring this
provider, per the existing, unchanged contract.
"""

from __future__ import annotations

import base64
import binascii
import os

import httpx
from pydantic import model_validator

from app.models.common import MotilyModel, VisualMediaType, VisualOutputFormat, VisualTiState, non_blank
from app.visual.errors import VisualError, VisualOutputError, VisualProviderError
from app.visual.models import VisualRenderRequest, VisualRenderResponse

CLOUDFLARE_PROVIDER_NAME = "cloudflare-workers-ai"
DEFAULT_CLOUDFLARE_IMAGE_MODEL = "@cf/black-forest-labs/flux-1-schnell"

# Per Cloudflare Workers AI's own documentation for this specific model
# (not guessed): FLUX.1 Schnell accepts a "steps" parameter up to 8.
_MAX_STEPS = 8

_CLOUDFLARE_API_BASE = "https://api.cloudflare.com/client/v4"

# Cloudflare's official FLUX.1 Schnell examples treat the base64 output
# as JPEG; the response envelope itself carries no mime_type field, so
# this is a fixed, documented value -- never derived from the response.
_OUTPUT_MIME_TYPE = "image/jpeg"


class CloudflareImageConfigurationError(VisualError):
    """Raised when CloudflareImageProvider cannot be constructed for real
    HTTP calls -- currently, a missing account id or API token.
    Deliberately NOT a VisualProviderError subclass: VisualRenderer
    retries VisualProviderError, and retrying a missing-configuration
    failure can never succeed."""


class CloudflareImageUnsupportedMediaTypeError(VisualOutputError):
    """Raised when a VisualRenderRequest asks for a media_type this
    adapter does not support (GENERATED_STILL only). Checked before any
    HTTP call -- DIAGRAM must never silently reach Cloudflare, matching
    the same production policy established for Gemini in Phase 20/20.1."""


class CloudflareImageUnsupportedOutputFormatError(VisualOutputError):
    """Raised when a VisualRenderRequest asks for an output_format this
    adapter does not support (JPG only -- FLUX.1 Schnell's official
    examples treat its base64 output as JPEG)."""


class CloudflareImageConfig(MotilyModel):
    model: str = DEFAULT_CLOUDFLARE_IMAGE_MODEL
    account_id_env: str = "CLOUDFLARE_ACCOUNT_ID"
    api_token_env: str = "CLOUDFLARE_API_TOKEN"
    timeout_seconds: float = 60.0
    steps: int = 4

    @model_validator(mode="after")
    def _check_invariants(self) -> "CloudflareImageConfig":
        non_blank(self.model, "model")
        non_blank(self.account_id_env, "account_id_env")
        non_blank(self.api_token_env, "api_token_env")
        if self.timeout_seconds <= 0:
            raise ValueError("timeout_seconds must be > 0")
        if not (1 <= self.steps <= _MAX_STEPS):
            raise ValueError(f"steps must be between 1 and {_MAX_STEPS}, got {self.steps}")
        return self


# ---------------------------------------------------------------------------
# Approved visual language (same locked style as Gemini's, Phase 19/20) --
# deliberately NOT imported from app/visual/providers/gemini.py or shared
# in any way (per this phase's own instruction): each provider adapter
# owns its own prompt text independently, even when the content overlaps.
# ---------------------------------------------------------------------------

_STYLE_GUIDANCE = (
    "Style: painterly editorial 2D illustration. Semi-real, simplified "
    "anatomy -- not photorealistic, not chibi, not hard cel-shaded. Flat "
    "2-3 tone rendering with warm key light and cooler shadows. "
    "Atmospheric haze only in the background, never over the primary "
    "subject. Restrained detail with one clear primary visual focus. "
    "Avoid embedded text or captions unless explicitly required."
)

# Tí's established mouse mascot/character, expressed only as a per-state
# mood/expression note -- deliberately NOT a full character-sheet
# ontology (no canonical reference image, no pose library). FLUX
# prompt-only generation does NOT solve canonical Tí consistency across
# independently generated stills any more than Gemini's does; see this
# module's docs/TECHNICAL_SPEC_v0.1.md entry.
_TI_STATE_DIRECTIONS: dict[VisualTiState, str] = {
    VisualTiState.NEUTRAL: "a calm, neutral expression",
    VisualTiState.CURIOUS: "a curious expression, leaning in with interest",
    VisualTiState.SKEPTICAL: "a raised-eyebrow, skeptical expression",
    VisualTiState.CONFUSED: "a puzzled, head-tilted expression",
    VisualTiState.SURPRISED: "a wide-eyed, surprised expression",
    VisualTiState.PANIC: "a brief, readable startled expression, not grotesque",
    VisualTiState.SMUG: "a smug, knowing half-smile",
    VisualTiState.DEADPAN: "a flat, deadpan expression",
    VisualTiState.EXCITED: "a bright, excited expression",
}


def _ti_state_direction(ti_state: VisualTiState) -> str:
    return _TI_STATE_DIRECTIONS[ti_state]


def _build_prompt(request: VisualRenderRequest) -> str:
    """Translate a provider-neutral VisualRenderRequest into Cloudflare/
    FLUX-specific prompt text. Content is copied from request fields
    verbatim, never invented; only style guidance is authored here."""
    lines = [
        _STYLE_GUIDANCE,
        "",
        f"Primary focus: {request.primary_focus}",
        f"Concept: {request.concept}",
    ]
    if request.secondary_elements:
        lines.append(f"Secondary elements: {', '.join(request.secondary_elements)}")
    if request.context_elements:
        lines.append(f"Context: {', '.join(request.context_elements)}")
    if request.ti_state is not None:
        lines.append(
            "Tí, the channel's established mouse mascot/character, appears "
            f"in the scene with {_ti_state_direction(request.ti_state)}. Tí "
            "may only observe or react to anything depicted -- never cause "
            "or alter an event, historical or otherwise."
        )
    if request.motion_intent:
        lines.append(
            "Composition note (still image only -- no motion is rendered, "
            f"use only as framing/pose context): {request.motion_intent}"
        )
    if request.reuse_key:
        lines.append(f"Continuity note: relates to existing asset {request.reuse_key!r}.")
    if request.metadata:
        metadata_text = ", ".join(f"{key}={value}" for key, value in sorted(request.metadata.items()))
        lines.append(f"Metadata: {metadata_text}")
    return "\n".join(lines)


class CloudflareImageProvider:
    """A VisualProvider backed by Cloudflare Workers AI. Satisfies
    VisualProvider structurally (duck-typed via typing.Protocol) -- no
    explicit inheritance needed."""

    def __init__(
        self,
        config: CloudflareImageConfig,
        *,
        client=None,
        account_id: str | None = None,
        api_token: str | None = None,
    ):
        self._config = config
        # A real httpx.Client-like object exposing .post(url, headers=,
        # json=, timeout=); defaults to the httpx module itself, whose
        # top-level post() has the identical shape. Tests inject a fake
        # exposing only .post(...), requiring no network.
        self._client = client if client is not None else httpx
        # account_id/api_token may be supplied directly (test-only
        # convenience, mirroring how a test injects `client=` instead of
        # letting a real one be built) or resolved from the environment.
        # Resolution happens eagerly here so a missing credential fails
        # immediately, with zero network calls -- mirroring
        # GeminiImageProvider's eager client construction.
        self._account_id = (
            account_id if account_id is not None else _read_required_env(config.account_id_env, "account id")
        )
        self._api_token = (
            api_token if api_token is not None else _read_required_env(config.api_token_env, "API token")
        )

    def render(self, request: VisualRenderRequest) -> VisualRenderResponse:
        if request.media_type != VisualMediaType.GENERATED_STILL:
            raise CloudflareImageUnsupportedMediaTypeError(
                f"CloudflareImageProvider only supports "
                f"{VisualMediaType.GENERATED_STILL.value}, got {request.media_type.value}"
            )
        if request.output_format != VisualOutputFormat.JPG:
            raise CloudflareImageUnsupportedOutputFormatError(
                f"CloudflareImageProvider only supports "
                f"{VisualOutputFormat.JPG.value} output, got {request.output_format.value}"
            )

        prompt = _build_prompt(request)
        url = f"{_CLOUDFLARE_API_BASE}/accounts/{self._account_id}/ai/run/{self._config.model}"
        headers = {"Authorization": f"Bearer {self._api_token}"}
        payload = {"prompt": prompt, "steps": self._config.steps}

        try:
            response = self._client.post(url, headers=headers, json=payload, timeout=self._config.timeout_seconds)
        except httpx.HTTPError as exc:
            raise VisualProviderError(_safe_transport_error_message(exc)) from exc

        if response.status_code >= 400:
            raise VisualProviderError(_safe_status_error_message(response))

        image_bytes = _extract_image_bytes(response)

        return VisualRenderResponse(
            asset_bytes=image_bytes,
            provider=CLOUDFLARE_PROVIDER_NAME,
            model=self._config.model,
            output_format=VisualOutputFormat.JPG,
            # Cloudflare's response envelope carries no dimension
            # metadata at all -- None is the honest, contract-accepted
            # value (see VisualRenderResponse); Pillow is deliberately
            # not added solely to decode it.
            width=None,
            height=None,
            # "cf-ray" is Cloudflare's own standard per-request ray id,
            # present on its API responses when exposed; None otherwise,
            # never invented.
            provider_request_id=response.headers.get("cf-ray"),
            metadata={"mime_type": _OUTPUT_MIME_TYPE},
        )


def _read_required_env(env_name: str, label: str) -> str:
    value = os.environ.get(env_name)
    if not value:
        raise CloudflareImageConfigurationError(
            f"Environment variable {env_name!r} is not set; CloudflareImageProvider "
            f"requires a Cloudflare {label} to make a real request"
        )
    return value


def _extract_image_bytes(response) -> bytes:
    """Pull raw image bytes out of a Cloudflare Workers AI JSON envelope,
    translating every unexpected shape into VisualOutputError (malformed
    output) or VisualProviderError (an explicit API-level failure)
    instead of letting a KeyError/TypeError/binascii.Error leak out as if
    it were normal behavior."""
    try:
        data = response.json()
    except ValueError as exc:
        raise VisualOutputError("Cloudflare response was not valid JSON") from exc

    if not isinstance(data, dict):
        raise VisualOutputError("Cloudflare response JSON was not an object")

    if not data.get("success"):
        raise VisualProviderError(f"Cloudflare API reported failure: {_format_errors(data.get('errors'))}")

    result = data.get("result")
    if not isinstance(result, dict):
        raise VisualOutputError("Cloudflare response contained no result object")

    image_b64 = result.get("image")
    if not image_b64:
        raise VisualOutputError("Cloudflare response result contained no image")

    try:
        image_bytes = base64.b64decode(image_b64, validate=True)
    except (binascii.Error, ValueError) as exc:
        raise VisualOutputError("Cloudflare response image was not valid base64") from exc

    if not image_bytes:
        raise VisualOutputError("Cloudflare response image decoded to empty bytes")

    return image_bytes


def _format_errors(errors) -> str:
    if not errors:
        return "no error detail provided"
    parts = []
    for error in errors if isinstance(errors, list) else [errors]:
        if isinstance(error, dict):
            parts.append(f"{error.get('code', '?')}: {error.get('message', 'no message')}")
    message = "; ".join(parts) if parts else "no error detail provided"
    max_len = 300
    if len(message) > max_len:
        message = message[:max_len] + "... (truncated)"
    return message


def _safe_transport_error_message(exc: Exception) -> str:
    """A concise, external-only description -- never the token, request
    headers, or a raw/huge response payload."""
    return f"Cloudflare network error: {type(exc).__name__}"


def _safe_status_error_message(response) -> str:
    message = f"Cloudflare API HTTP {response.status_code}"
    try:
        data = response.json()
    except ValueError:
        return message
    if isinstance(data, dict) and data.get("errors"):
        message = f"{message}: {_format_errors(data.get('errors'))}"
    return message
