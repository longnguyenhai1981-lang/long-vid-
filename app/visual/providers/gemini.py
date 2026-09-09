"""Gemini Image provider adapter -- the first real VisualProvider
implementation.

This module is the ONLY place in the repository that imports the Gemini
SDK (`google.genai`) or `httpx`'s error types for image generation.
Mirrors app/audio/providers/gemini.py's GeminiTTSProvider exactly in
structure and policy: no retry here (VisualRenderer already owns
VisualProviderError retry -- retrying here too would multiply attempts
across two layers), narrow SDK error translation, environment-only
secret loading, eager client construction so a missing key fails
immediately with zero network calls.

PHASE 20.1 CORRECTION: Phase 20 originally used
`client.models.generate_images(...)` (the SDK's dedicated Imagen
text-to-image endpoint) with model `imagen-3.0-generate-002`. A real
live call against this project's Gemini Developer API key failed with:

    ValueError: This method is only supported in Gemini Enterprise Agent
    Platform mode, not in Gemini Developer API mode.

(the installed SDK also warns that `generate_images` is deprecated).
`generate_images` requires Vertex AI / Gemini Enterprise Agent Platform
credentials; this project uses a plain Gemini Developer API / AI Studio
key. Phase 20.1 migrates to `client.models.generate_content(...)` with
`response_modalities=["IMAGE"]` and a `types.ImageConfig` -- the same
`generate_content` entrypoint Gemini TTS already uses (`app/audio/
providers/gemini.py`), just requesting the `IMAGE` modality instead of
`AUDIO`. Verified locally in the installed SDK (`google-genai==2.22.0`,
not from memory): `types.Modality.IMAGE` exists ("Indicates the model
should return images"), `GenerateContentConfig.response_modalities`
accepts it, and `types.ImageConfig` ("The image generation configuration
to be used in GenerateContentConfig") exposes `aspect_ratio` -- its
docstring documents `output_mime_type`/`output_compression_quality`/
`image_output_options` as "not supported in Gemini API" (Developer API
mode), so this adapter deliberately never sets them. The response shape
is the same `GenerateContentResponse` (`candidates[0].content.parts[*]
.inline_data`) Gemini TTS already traverses.

Default model: `gemini-2.5-flash-image` (a stable Gemini-native image
model supported by the Gemini Developer API, per this correction's
external verification -- not present as a string anywhere in the locally
installed SDK's source, since model names are never enumerated by the
SDK's type system; validity is decided server-side at call time).
Configurable via `GeminiImageConfig.model` either way.

Phase 20 supports GENERATED_STILL only -- DIAGRAM and every other
VisualMediaType are rejected before any network call. Only PNG output is
supported; the returned MIME type is validated when exposed rather than
assumed, since Developer API mode does not support forcing an output
MIME type via request config.
"""

from __future__ import annotations

import os

import httpx
from google import genai
from google.genai import errors as genai_errors
from google.genai import types as genai_types
from pydantic import model_validator

from app.models.common import MotilyModel, VisualMediaType, VisualOutputFormat, VisualTiState, non_blank
from app.visual.errors import VisualError, VisualOutputError, VisualProviderError
from app.visual.models import VisualRenderRequest, VisualRenderResponse

GEMINI_IMAGE_PROVIDER_NAME = "gemini-image"
DEFAULT_GEMINI_IMAGE_MODEL = "gemini-2.5-flash-image"

# Read directly from google.genai.types.ImageConfig.aspect_ratio's own
# docstring (installed SDK, not memory): "Supported values are '1:1',
# '2:3', '3:2', '3:4', '4:3', '9:16', '16:9', and '21:9'."
_SUPPORTED_ASPECT_RATIOS = {"1:1", "2:3", "3:2", "3:4", "4:3", "9:16", "16:9", "21:9"}

# Phase 20 supports PNG only. Unlike the deprecated Imagen path, Developer
# API mode cannot force an output MIME type via request config
# (ImageConfig.output_mime_type is documented "not supported in Gemini
# API") -- so this is validated against the response's own reported MIME,
# never assumed or requested.
_ACCEPTED_IMAGE_MIME_TYPES = {"image/png"}

# SDK exception family translated to VisualProviderError -- identical
# rationale to GeminiTTSProvider's _RETRYABLE_SDK_ERRORS.
_RETRYABLE_SDK_ERRORS = (genai_errors.APIError, httpx.HTTPError)


class GeminiImageConfigurationError(VisualError):
    """Raised when GeminiImageProvider cannot be constructed for the real
    SDK -- currently, only a missing API key. Deliberately NOT a
    VisualProviderError subclass: VisualRenderer retries
    VisualProviderError, and retrying a missing-configuration failure can
    never succeed."""


class GeminiImageUnsupportedMediaTypeError(VisualOutputError):
    """Raised when a VisualRenderRequest asks for a media_type this
    adapter does not support (Phase 20: anything other than
    GENERATED_STILL). Checked before any network call -- DIAGRAM must
    never silently reach Gemini."""


class GeminiImageUnsupportedOutputFormatError(VisualOutputError):
    """Raised when a VisualRenderRequest asks for an output_format this
    adapter does not support (Phase 20: anything other than PNG)."""


class GeminiImageConfig(MotilyModel):
    model: str = DEFAULT_GEMINI_IMAGE_MODEL
    api_key_env: str = "GEMINI_API_KEY"
    default_aspect_ratio: str = "16:9"

    @model_validator(mode="after")
    def _check_invariants(self) -> "GeminiImageConfig":
        non_blank(self.model, "model")
        non_blank(self.api_key_env, "api_key_env")
        if self.default_aspect_ratio not in _SUPPORTED_ASPECT_RATIOS:
            raise ValueError(
                f"default_aspect_ratio must be one of "
                f"{sorted(_SUPPORTED_ASPECT_RATIOS)}, got "
                f"{self.default_aspect_ratio!r}"
            )
        return self


# ---------------------------------------------------------------------------
# Approved visual language (Phase 19/20 style contract) -- deterministic,
# fixed guidance text, never generated or altered per request. Concise on
# purpose, to avoid prompt bloat.
# ---------------------------------------------------------------------------

_STYLE_GUIDANCE = (
    "STYLE: painterly editorial 2D illustration. Semi-real, simplified "
    "anatomy -- not photorealistic, not chibi, not hard cel-shaded. Flat "
    "2-3 tone rendering with soft, short tonal transitions. Warm key "
    "light, cooler shadows. Atmospheric haze only in the background, "
    "never over the primary subject. Restrained detail with one clear "
    "primary visual focus. Avoid embedded text, labels, or captions "
    "unless the concept explicitly requires a specific written word."
)

# Tí's established mouse mascot/character, expressed only as a per-state
# mood/expression note -- deliberately NOT a full character-sheet
# ontology (no canonical reference image, no pose library). See this
# module's docstring and docs/TECHNICAL_SPEC_v0.1.md, Phase 20: character
# consistency across independently generated stills is NOT solved here.
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
    """Translate a provider-neutral VisualRenderRequest into Gemini-specific
    prompt text. This is the ONLY place in the repository allowed to
    create a vendor-specific image prompt -- VisualRenderer stays
    prompt-free. Never invents narrative facts: every content line below
    is copied from a request field, not authored here."""
    lines = [
        _STYLE_GUIDANCE,
        "",
        f"PRIMARY FOCUS: {request.primary_focus}",
        f"CONCEPT: {request.concept}",
    ]
    if request.secondary_elements:
        lines.append(f"SECONDARY ELEMENTS: {', '.join(request.secondary_elements)}")
    if request.context_elements:
        lines.append(f"CONTEXT: {', '.join(request.context_elements)}")
    if request.ti_state is not None:
        lines.append(
            "Tí, the channel's established mouse mascot/character, appears "
            f"in the scene with {_ti_state_direction(request.ti_state)}. Tí "
            "may only observe or react to anything depicted -- never cause "
            "or alter an event, historical or otherwise."
        )
    if request.motion_intent:
        lines.append(
            "COMPOSITION NOTE (still image only -- no motion is rendered, "
            f"use only as framing/pose context): {request.motion_intent}"
        )
    if request.reuse_key:
        lines.append(f"CONTINUITY NOTE: relates to existing asset {request.reuse_key!r}.")
    if request.metadata:
        metadata_text = ", ".join(f"{key}={value}" for key, value in sorted(request.metadata.items()))
        lines.append(f"METADATA: {metadata_text}")
    return "\n".join(lines)


class GeminiImageProvider:
    """A VisualProvider backed by the Gemini API. Satisfies VisualProvider
    structurally (duck-typed via typing.Protocol) -- no explicit
    inheritance needed."""

    def __init__(self, config: GeminiImageConfig, *, client=None):
        self._config = config
        self._client = client if client is not None else _build_default_client(config)

    def render(self, request: VisualRenderRequest) -> VisualRenderResponse:
        if request.media_type != VisualMediaType.GENERATED_STILL:
            raise GeminiImageUnsupportedMediaTypeError(
                f"GeminiImageProvider only supports "
                f"{VisualMediaType.GENERATED_STILL.value} in Phase 20, got "
                f"{request.media_type.value}"
            )
        if request.output_format != VisualOutputFormat.PNG:
            raise GeminiImageUnsupportedOutputFormatError(
                f"GeminiImageProvider only supports "
                f"{VisualOutputFormat.PNG.value} output in Phase 20, got "
                f"{request.output_format.value}"
            )

        prompt = _build_prompt(request)

        try:
            response = self._client.models.generate_content(
                model=self._config.model,
                contents=prompt,
                config=genai_types.GenerateContentConfig(
                    response_modalities=["IMAGE"],
                    image_config=genai_types.ImageConfig(
                        aspect_ratio=self._config.default_aspect_ratio
                    ),
                ),
            )
        except _RETRYABLE_SDK_ERRORS as exc:
            raise VisualProviderError(_safe_provider_error_message(exc)) from exc

        image_bytes, mime_type = _extract_image_bytes(response)

        metadata = {"mime_type": mime_type} if mime_type else {}

        return VisualRenderResponse(
            asset_bytes=image_bytes,
            provider=GEMINI_IMAGE_PROVIDER_NAME,
            model=self._config.model,
            output_format=VisualOutputFormat.PNG,
            # Gemini's inline_data (google.genai.types.Blob) carries only
            # data/display_name/mime_type -- no dimension fields anywhere.
            # Pillow is deliberately not added solely to decode dimensions
            # -- None is the honest, contract-accepted value (see
            # VisualRenderResponse).
            width=None,
            height=None,
            # GenerateContentResponse.response_id (the same field
            # GeminiTTSProvider already reads) is the official per-call
            # request id when the SDK populates it; None otherwise, never
            # invented.
            provider_request_id=getattr(response, "response_id", None),
            metadata=metadata,
        )


def _build_default_client(config: GeminiImageConfig) -> genai.Client:
    api_key = os.environ.get(config.api_key_env)
    if not api_key:
        raise GeminiImageConfigurationError(
            f"Environment variable {config.api_key_env!r} is not set; "
            "GeminiImageProvider requires a Gemini API key to construct a "
            "real client"
        )
    return genai.Client(api_key=api_key)


def _extract_image_bytes(response) -> tuple[bytes, str | None]:
    """Pull raw image bytes (and, when present, the MIME type) out of a
    GenerateContentResponse, translating every unexpected shape --
    empty response, text-only response, a Responsible-AI-blocked result --
    into VisualOutputError instead of letting an IndexError/AttributeError
    leak out as if it were normal behavior. Mirrors
    GeminiTTSProvider._extract_pcm_bytes's traversal exactly, except it
    looks for the first part carrying inline image data and explicitly
    ignores any text part instead of requiring one."""
    candidates = getattr(response, "candidates", None) or []
    if not candidates:
        raise VisualOutputError(f"Gemini response contained no candidates{_blocked_reason_suffix(response)}")

    content = getattr(candidates[0], "content", None)
    parts = getattr(content, "parts", None) if content is not None else None
    if not parts:
        raise VisualOutputError(
            f"Gemini response candidate contained no content parts{_blocked_reason_suffix(response)}"
        )

    inline_data = None
    for part in parts:
        candidate_inline_data = getattr(part, "inline_data", None)
        if candidate_inline_data is not None:
            inline_data = candidate_inline_data
            break

    if inline_data is None:
        # Every part was text (or some other non-image part) -- a
        # text-only response counts as no image, not a malformed shape.
        raise VisualOutputError(
            f"Gemini response contained no image part{_blocked_reason_suffix(response)}"
        )

    image_bytes = getattr(inline_data, "data", None)
    if not image_bytes:
        raise VisualOutputError("Gemini response image part contained no bytes")

    mime_type = getattr(inline_data, "mime_type", None)
    if mime_type is not None and mime_type not in _ACCEPTED_IMAGE_MIME_TYPES:
        raise VisualOutputError(f"Gemini returned unexpected image MIME type: {mime_type!r}")

    return image_bytes, mime_type


def _blocked_reason_suffix(response) -> str:
    """Surface prompt-level Responsible-AI blocking when present, so an
    empty/text-only response doesn't read as an unexplained failure."""
    prompt_feedback = getattr(response, "prompt_feedback", None)
    block_reason = getattr(prompt_feedback, "block_reason", None) if prompt_feedback is not None else None
    if block_reason:
        return f" (blocked: {block_reason})"
    return ""


def _safe_provider_error_message(exc: Exception) -> str:
    """A concise, external-only description -- never the API key, request
    headers, or a raw/huge response payload."""
    if isinstance(exc, genai_errors.APIError):
        message = f"Gemini API error {exc.code} ({exc.status}): {exc.message or 'no message'}"
    else:
        message = f"Gemini network error: {type(exc).__name__}"
    max_len = 500
    if len(message) > max_len:
        message = message[:max_len] + "... (truncated)"
    return message
