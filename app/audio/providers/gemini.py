"""Gemini TTS provider adapter -- the first real TTSProvider implementation.

This module is the ONLY place in the repository that imports the Gemini
SDK (`google.genai`) or `httpx`'s error types. Nothing here reasons about
content: one TTSRequest in, one TTSProvider.synthesize() call to Gemini,
one TTSResponse out. Gemini is used purely as a speech synthesizer -- it
is never asked to rewrite, translate, or comment on the transcript, and
this adapter never itself performs any retry (VoiceRenderer already owns
that policy for TTSProviderError; retrying here too would multiply
attempts across two layers).
"""

from __future__ import annotations

import io
import os
import re
import wave

import httpx
from google import genai
from google.genai import errors as genai_errors
from google.genai import types as genai_types
from pydantic import model_validator

from app.audio.errors import TTSError, TTSOutputError, TTSProviderError
from app.audio.models import TTSRequest, TTSResponse
from app.models.common import AudioFormat, Energy, MotilyModel, Pace, VoiceState, non_blank

GEMINI_PROVIDER_NAME = "gemini"
DEFAULT_GEMINI_MODEL = "gemini-2.5-flash-preview-tts"

_MIME_SAMPLE_RATE_RE = re.compile(r"rate=(\d+)")

# SDK exception family translated to TTSProviderError. google.genai.errors
# .APIError (and its ClientError/ServerError subclasses) covers the API
# itself returning an error; httpx.HTTPError covers a transport-level
# failure (connection/timeout) that never reached the API at all -- both
# are "provider/network" failures per the Phase 18 spec. Anything else
# (e.g. a TypeError from a genuine adapter bug) is deliberately left
# uncaught here, per the same spec's instruction not to blindly catch
# every Exception.
_RETRYABLE_SDK_ERRORS = (genai_errors.APIError, httpx.HTTPError)


class GeminiTTSConfigurationError(TTSError):
    """Raised when GeminiTTSProvider cannot be constructed for the real SDK
    -- currently, only a missing API key. Deliberately NOT a
    TTSProviderError subclass: VoiceRenderer retries TTSProviderError, and
    retrying a missing-configuration failure can never succeed."""


class GeminiUnsupportedFormatError(TTSOutputError):
    """Raised when a TTSRequest asks for an output_format Gemini's adapter
    does not support (Phase 18: anything other than WAV)."""


class GeminiTTSConfig(MotilyModel):
    model: str = DEFAULT_GEMINI_MODEL
    api_key_env: str = "GEMINI_API_KEY"
    sample_rate_hz: int = 24000
    channels: int = 1
    sample_width_bytes: int = 2

    @model_validator(mode="after")
    def _check_invariants(self) -> "GeminiTTSConfig":
        non_blank(self.model, "model")
        non_blank(self.api_key_env, "api_key_env")
        if self.sample_rate_hz <= 0:
            raise ValueError("sample_rate_hz must be > 0")
        if self.channels <= 0:
            raise ValueError("channels must be > 0")
        if self.sample_width_bytes <= 0:
            raise ValueError("sample_width_bytes must be > 0")
        return self


# ---------------------------------------------------------------------------
# Deterministic VoiceState/Pace/Energy -> director-instruction mapping.
#
# Deliberately small, fixed, and non-extreme -- naturalness over extreme
# expressiveness (see app/engines/voice_plan/prompt.py's identical
# principle for VoicePlan generation itself). These are looked up, never
# computed, so the same VoiceState/Pace/Energy always produces the exact
# same instruction text.
# ---------------------------------------------------------------------------

_VOICE_STATE_DIRECTIONS: dict[VoiceState, str] = {
    VoiceState.NEUTRAL: "Natural, conversational, relaxed.",
    VoiceState.CURIOUS: "Curious and engaged, as if noticing something interesting.",
    VoiceState.SKEPTICAL: "Slightly skeptical and questioning, still conversational.",
    VoiceState.EXCITED: "Clearly excited but controlled; avoid shouting.",
    VoiceState.SERIOUS: "Focused and serious, calm and precise.",
    VoiceState.DEADPAN: "Dry, restrained deadpan delivery.",
    VoiceState.PANIC: "Brief controlled panic, fast but still intelligible.",
    VoiceState.LOW_ENERGY: "Low-energy, slightly tired, natural rather than monotone.",
}

_PACE_DIRECTIONS: dict[Pace, str] = {
    Pace.SLOW: "a slightly slower pace",
    Pace.NORMAL: "a natural conversational pace",
    Pace.FAST: "a faster, energetic pace while remaining clear",
}

_ENERGY_DIRECTIONS: dict[Energy, str] = {
    Energy.LOW: "restrained energy",
    Energy.MEDIUM: "moderate, natural energy",
    Energy.HIGH: "high energy without shouting or exaggeration",
}


def voice_state_direction(voice_state: VoiceState) -> str:
    return _VOICE_STATE_DIRECTIONS[voice_state]


def pace_direction(pace: Pace) -> str:
    return _PACE_DIRECTIONS[pace]


def energy_direction(energy: Energy) -> str:
    return _ENERGY_DIRECTIONS[energy]


def _build_director_prompt(request: TTSRequest) -> str:
    direction = (
        f"{voice_state_direction(request.voice_state)} "
        f"Maintain {pace_direction(request.pace)} with "
        f"{energy_direction(request.energy)}."
    )
    return (
        "DIRECTOR'S NOTES:\n"
        f"{direction}\n\n"
        "Read ONLY the transcript below.\n"
        "Do not add, remove, paraphrase, translate, or comment on it.\n\n"
        "TRANSCRIPT:\n"
        f"{request.text}"
    )


class GeminiTTSProvider:
    """A TTSProvider backed by the Gemini API. Satisfies TTSProvider
    structurally (duck-typed via typing.Protocol) -- no explicit
    inheritance needed."""

    def __init__(self, config: GeminiTTSConfig, *, client=None):
        self._config = config
        self._client = client if client is not None else _build_default_client(config)

    def synthesize(self, request: TTSRequest) -> TTSResponse:
        if request.output_format != AudioFormat.WAV:
            raise GeminiUnsupportedFormatError(
                f"GeminiTTSProvider only supports {AudioFormat.WAV.value} output "
                f"in Phase 18, got {request.output_format.value}"
            )

        prompt = _build_director_prompt(request)

        try:
            response = self._client.models.generate_content(
                model=self._config.model,
                contents=prompt,
                config=genai_types.GenerateContentConfig(
                    response_modalities=["AUDIO"],
                    speech_config=genai_types.SpeechConfig(
                        voice_config=genai_types.VoiceConfig(
                            prebuilt_voice_config=genai_types.PrebuiltVoiceConfig(
                                voice_name=request.voice_id
                            )
                        )
                    ),
                ),
            )
        except _RETRYABLE_SDK_ERRORS as exc:
            raise TTSProviderError(_safe_provider_error_message(exc)) from exc

        pcm_bytes, mime_sample_rate = _extract_pcm_bytes(response)
        sample_rate = mime_sample_rate or self._config.sample_rate_hz

        wav_bytes, duration_seconds = _wrap_pcm_as_wav(
            pcm_bytes,
            sample_rate=sample_rate,
            channels=self._config.channels,
            sample_width_bytes=self._config.sample_width_bytes,
        )

        return TTSResponse(
            audio_bytes=wav_bytes,
            provider=GEMINI_PROVIDER_NAME,
            model=self._config.model,
            audio_format=AudioFormat.WAV,
            duration_seconds=duration_seconds,
            provider_request_id=getattr(response, "response_id", None),
            metadata={
                "sample_rate_hz": str(sample_rate),
                "channels": str(self._config.channels),
                "sample_width_bytes": str(self._config.sample_width_bytes),
            },
        )


def _build_default_client(config: GeminiTTSConfig) -> genai.Client:
    api_key = os.environ.get(config.api_key_env)
    if not api_key:
        raise GeminiTTSConfigurationError(
            f"Environment variable {config.api_key_env!r} is not set; "
            "GeminiTTSProvider requires a Gemini API key to construct a "
            "real client"
        )
    return genai.Client(api_key=api_key)


def _extract_pcm_bytes(response) -> tuple[bytes, int | None]:
    """Pull raw PCM bytes (and, when present, the authoritative sample
    rate) out of a GenerateContentResponse, translating every unexpected
    shape into TTSOutputError instead of letting an IndexError/
    AttributeError leak out as if it were normal control flow."""
    candidates = getattr(response, "candidates", None) or []
    if not candidates:
        raise TTSOutputError("Gemini response contained no candidates")

    content = getattr(candidates[0], "content", None)
    parts = getattr(content, "parts", None) if content is not None else None
    if not parts:
        raise TTSOutputError("Gemini response candidate contained no content parts")

    inline_data = None
    for part in parts:
        candidate_inline_data = getattr(part, "inline_data", None)
        if candidate_inline_data is not None:
            inline_data = candidate_inline_data
            break

    if inline_data is None or not inline_data.data:
        raise TTSOutputError("Gemini response contained no inline audio data")

    sample_rate = None
    mime_type = getattr(inline_data, "mime_type", None)
    if mime_type:
        match = _MIME_SAMPLE_RATE_RE.search(mime_type)
        if match:
            sample_rate = int(match.group(1))

    return inline_data.data, sample_rate


def _wrap_pcm_as_wav(
    pcm_bytes: bytes, *, sample_rate: int, channels: int, sample_width_bytes: int
) -> tuple[bytes, float]:
    frame_size = channels * sample_width_bytes
    if len(pcm_bytes) % frame_size != 0:
        raise TTSOutputError(
            f"Gemini PCM byte length {len(pcm_bytes)} is not a whole number of "
            f"frames (frame size {frame_size} = {channels} channel(s) x "
            f"{sample_width_bytes} byte(s) per sample)"
        )
    frame_count = len(pcm_bytes) // frame_size
    duration_seconds = frame_count / sample_rate

    buffer = io.BytesIO()
    with wave.open(buffer, "wb") as wav_file:
        wav_file.setnchannels(channels)
        wav_file.setsampwidth(sample_width_bytes)
        wav_file.setframerate(sample_rate)
        wav_file.writeframes(pcm_bytes)

    return buffer.getvalue(), duration_seconds


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
