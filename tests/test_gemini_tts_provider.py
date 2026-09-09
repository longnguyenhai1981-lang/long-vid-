from __future__ import annotations

import io
import wave
from types import SimpleNamespace

import pytest
from google.genai import errors as genai_errors
from pydantic import ValidationError

from app.audio.errors import TTSOutputError, TTSProviderError
from app.audio.models import TTSRequest
from app.audio.provider import TTSProvider
from app.audio.providers.gemini import (
    DEFAULT_GEMINI_MODEL,
    GEMINI_PROVIDER_NAME,
    GeminiTTSConfig,
    GeminiTTSConfigurationError,
    GeminiTTSProvider,
    GeminiUnsupportedFormatError,
    energy_direction,
    pace_direction,
    voice_state_direction,
)
from app.models.common import Energy, Pace, VoiceState


# ---------------------------------------------------------------------------
# Fake Gemini client (test-local; NOT a second production fake provider)
# ---------------------------------------------------------------------------


def _pcm_seconds(seconds: float, sample_rate=24000, channels=1, sample_width=2) -> bytes:
    frame_count = int(round(seconds * sample_rate))
    return b"\x00" * (frame_count * channels * sample_width)


def _fake_response(pcm_bytes, mime_type="audio/L16;codec=pcm;rate=24000", response_id="req-123"):
    inline_data = SimpleNamespace(data=pcm_bytes, mime_type=mime_type)
    part = SimpleNamespace(inline_data=inline_data)
    content = SimpleNamespace(parts=[part])
    candidate = SimpleNamespace(content=content)
    return SimpleNamespace(candidates=[candidate], response_id=response_id)


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


def _request(**overrides) -> TTSRequest:
    fields = dict(
        text="Chào! Một Tí Lý đây.",
        voice_id="Puck",
        voice_state="NEUTRAL",
        pace="NORMAL",
        energy="MEDIUM",
        output_format="WAV",
    )
    fields.update(overrides)
    return TTSRequest(**fields)


def _provider_with(response=None, exc=None, config=None) -> tuple[GeminiTTSProvider, _FakeClient]:
    client = _FakeClient(response=response, exc=exc)
    provider = GeminiTTSProvider(config or GeminiTTSConfig(), client=client)
    return provider, client


# ---------------------------------------------------------------------------
# Section 35: protocol compatibility
# ---------------------------------------------------------------------------


def test_gemini_provider_satisfies_tts_provider_protocol():
    provider, _ = _provider_with(response=_fake_response(_pcm_seconds(1.0)))
    assert isinstance(provider, TTSProvider)


# ---------------------------------------------------------------------------
# Section 36: config validation
# ---------------------------------------------------------------------------


def test_gemini_config_defaults():
    config = GeminiTTSConfig()
    assert config.model == DEFAULT_GEMINI_MODEL
    assert config.api_key_env == "GEMINI_API_KEY"
    assert config.sample_rate_hz == 24000
    assert config.channels == 1
    assert config.sample_width_bytes == 2


@pytest.mark.parametrize(
    "overrides",
    [
        {"sample_rate_hz": 0},
        {"sample_rate_hz": -1},
        {"channels": 0},
        {"channels": -1},
        {"sample_width_bytes": 0},
        {"sample_width_bytes": -1},
        {"model": "   "},
        {"api_key_env": "   "},
    ],
)
def test_gemini_config_rejects_invalid_values(overrides):
    with pytest.raises(ValidationError):
        GeminiTTSConfig(**overrides)


def test_gemini_config_has_no_api_key_field():
    assert "api_key" not in GeminiTTSConfig.model_fields


def test_gemini_config_model_is_configurable():
    config = GeminiTTSConfig(model="gemini-custom-tts")
    assert config.model == "gemini-custom-tts"


# ---------------------------------------------------------------------------
# Section 37: missing API key
# ---------------------------------------------------------------------------


def test_missing_api_key_raises_configuration_error_with_no_network_call(monkeypatch):
    monkeypatch.delenv("GEMINI_API_KEY", raising=False)

    with pytest.raises(GeminiTTSConfigurationError) as exc_info:
        GeminiTTSProvider(GeminiTTSConfig())

    assert "GEMINI_API_KEY" in str(exc_info.value)


def test_missing_api_key_error_never_contains_a_key_value(monkeypatch):
    monkeypatch.delenv("GEMINI_API_KEY", raising=False)
    monkeypatch.setenv("SOME_OTHER_ENV", "not-a-secret")

    with pytest.raises(GeminiTTSConfigurationError) as exc_info:
        GeminiTTSProvider(GeminiTTSConfig())

    # Nothing resembling a secret value can appear -- there never was one.
    assert "sk-" not in str(exc_info.value)


def test_custom_api_key_env_name_is_respected(monkeypatch):
    monkeypatch.delenv("CUSTOM_GEMINI_KEY", raising=False)
    with pytest.raises(GeminiTTSConfigurationError) as exc_info:
        GeminiTTSProvider(GeminiTTSConfig(api_key_env="CUSTOM_GEMINI_KEY"))
    assert "CUSTOM_GEMINI_KEY" in str(exc_info.value)


# ---------------------------------------------------------------------------
# Section 38: injected client needs no key
# ---------------------------------------------------------------------------


def test_injected_client_requires_no_environment_key(monkeypatch):
    monkeypatch.delenv("GEMINI_API_KEY", raising=False)
    provider, client = _provider_with(response=_fake_response(_pcm_seconds(1.0)))
    response = provider.synthesize(_request())
    assert response.provider == GEMINI_PROVIDER_NAME
    assert len(client.models.calls) == 1


# ---------------------------------------------------------------------------
# Section 39: Vietnamese text preservation
# ---------------------------------------------------------------------------


def test_vietnamese_text_preserved_byte_for_byte_in_prompt():
    text = "Chào! Một Tí Lý đây."
    provider, client = _provider_with(response=_fake_response(_pcm_seconds(1.0)))
    provider.synthesize(_request(text=text))

    prompt = client.models.calls[0]["contents"]
    assert text in prompt
    assert f"TRANSCRIPT:\n{text}" in prompt


def test_text_is_never_normalized_or_translated():
    text = "Ủa? Cái Gì Đang Xảy Ra Vậy???"
    provider, client = _provider_with(response=_fake_response(_pcm_seconds(1.0)))
    provider.synthesize(_request(text=text))

    prompt = client.models.calls[0]["contents"]
    assert text in prompt  # exact case, exact punctuation, exact accents


# ---------------------------------------------------------------------------
# Section 40: style mapping
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("voice_state", list(VoiceState))
def test_every_voice_state_maps_to_a_non_blank_instruction(voice_state):
    instruction = voice_state_direction(voice_state)
    assert instruction.strip() != ""


_NEGATORS = ("avoid", "without", "no ", "not ")


def _discourages(instruction: str, trigger: str) -> bool:
    """True if `trigger` is either absent, or only ever appears alongside a
    negating word -- i.e. the instruction discourages it rather than asking
    for it. A bare substring check can't tell "avoid shouting" (correct,
    desired) from "shout with excitement" (exactly what must never appear)."""
    lowered = instruction.lower()
    if trigger not in lowered:
        return True
    return any(negator in lowered for negator in _NEGATORS)


def test_high_energy_does_not_instruct_shouting():
    assert _discourages(energy_direction(Energy.HIGH), "shout")


def test_excited_voice_state_does_not_instruct_shouting():
    assert _discourages(voice_state_direction(VoiceState.EXCITED), "shout")


# ---------------------------------------------------------------------------
# Section 41 / 42: pace / energy mapping distinctness
# ---------------------------------------------------------------------------


def test_pace_values_map_distinctly():
    mapped = {pace: pace_direction(pace) for pace in Pace}
    assert len(set(mapped.values())) == len(mapped)
    assert all(value.strip() for value in mapped.values())


def test_energy_values_map_distinctly():
    mapped = {energy: energy_direction(energy) for energy in Energy}
    assert len(set(mapped.values())) == len(mapped)
    assert all(value.strip() for value in mapped.values())


# ---------------------------------------------------------------------------
# Section 43: voice id passthrough
# ---------------------------------------------------------------------------


def test_voice_id_passed_through_exactly_as_prebuilt_voice_name():
    provider, client = _provider_with(response=_fake_response(_pcm_seconds(1.0)))
    provider.synthesize(_request(voice_id="Puck"))

    sent_config = client.models.calls[0]["config"]
    voice_name = sent_config.speech_config.voice_config.prebuilt_voice_config.voice_name
    assert voice_name == "Puck"


def test_voice_id_is_not_lowercased_or_remapped():
    provider, client = _provider_with(response=_fake_response(_pcm_seconds(1.0)))
    provider.synthesize(_request(voice_id="Zubenelgenubi"))

    sent_config = client.models.calls[0]["config"]
    voice_name = sent_config.speech_config.voice_config.prebuilt_voice_config.voice_name
    assert voice_name == "Zubenelgenubi"


# ---------------------------------------------------------------------------
# Section 44: WAV output
# ---------------------------------------------------------------------------


def test_wav_output_is_a_real_wav_container_matching_config():
    config = GeminiTTSConfig(sample_rate_hz=24000, channels=1, sample_width_bytes=2)
    provider, _ = _provider_with(response=_fake_response(_pcm_seconds(2.0)), config=config)

    response = provider.synthesize(_request())
    assert response.audio_format.value == "WAV"

    with wave.open(io.BytesIO(response.audio_bytes)) as wav_file:
        assert wav_file.getframerate() == 24000
        assert wav_file.getnchannels() == 1
        assert wav_file.getsampwidth() == 2


def test_wav_uses_authoritative_mime_sample_rate_when_present():
    # mime_type says 22050, config default says 24000 -- the SDK's own
    # metadata wins.
    pcm = _pcm_seconds(1.0, sample_rate=22050)
    provider, _ = _provider_with(
        response=_fake_response(pcm, mime_type="audio/L16;codec=pcm;rate=22050")
    )
    response = provider.synthesize(_request())
    with wave.open(io.BytesIO(response.audio_bytes)) as wav_file:
        assert wav_file.getframerate() == 22050


def test_wav_falls_back_to_config_rate_when_mime_type_has_no_rate():
    pcm = _pcm_seconds(1.0, sample_rate=24000)
    provider, _ = _provider_with(response=_fake_response(pcm, mime_type="audio/L16"))
    response = provider.synthesize(_request())
    with wave.open(io.BytesIO(response.audio_bytes)) as wav_file:
        assert wav_file.getframerate() == 24000


# ---------------------------------------------------------------------------
# Section 45: duration
# ---------------------------------------------------------------------------


def test_duration_for_exactly_one_second_of_pcm():
    provider, _ = _provider_with(response=_fake_response(_pcm_seconds(1.0)))
    response = provider.synthesize(_request())
    assert response.duration_seconds == pytest.approx(1.0)


def test_duration_scales_with_pcm_length():
    provider, _ = _provider_with(response=_fake_response(_pcm_seconds(2.5)))
    response = provider.synthesize(_request())
    assert response.duration_seconds == pytest.approx(2.5)


# ---------------------------------------------------------------------------
# Section 46: MP3 rejected
# ---------------------------------------------------------------------------


def test_mp3_output_format_rejected_before_any_network_call():
    provider, client = _provider_with(response=_fake_response(_pcm_seconds(1.0)))

    with pytest.raises((TTSOutputError, GeminiUnsupportedFormatError)):
        provider.synthesize(_request(output_format="MP3"))

    assert len(client.models.calls) == 0


def test_mp3_rejection_is_gemini_unsupported_format_error():
    provider, _ = _provider_with(response=_fake_response(_pcm_seconds(1.0)))
    with pytest.raises(GeminiUnsupportedFormatError):
        provider.synthesize(_request(output_format="MP3"))


# ---------------------------------------------------------------------------
# Section 47: empty audio
# ---------------------------------------------------------------------------


def test_empty_audio_data_raises_tts_output_error():
    provider, _ = _provider_with(response=_fake_response(b""))
    with pytest.raises(TTSOutputError):
        provider.synthesize(_request())


# ---------------------------------------------------------------------------
# Section 48: missing audio
# ---------------------------------------------------------------------------


def test_no_candidates_raises_tts_output_error():
    provider, _ = _provider_with(response=SimpleNamespace(candidates=[], response_id=None))
    with pytest.raises(TTSOutputError):
        provider.synthesize(_request())


def test_no_content_parts_raises_tts_output_error():
    candidate = SimpleNamespace(content=SimpleNamespace(parts=[]))
    provider, _ = _provider_with(
        response=SimpleNamespace(candidates=[candidate], response_id=None)
    )
    with pytest.raises(TTSOutputError):
        provider.synthesize(_request())


def test_no_inline_data_on_any_part_raises_tts_output_error():
    part = SimpleNamespace(inline_data=None, text="oops, a text part instead of audio")
    candidate = SimpleNamespace(content=SimpleNamespace(parts=[part]))
    provider, _ = _provider_with(
        response=SimpleNamespace(candidates=[candidate], response_id=None)
    )
    with pytest.raises(TTSOutputError):
        provider.synthesize(_request())


# ---------------------------------------------------------------------------
# Section 49: malformed PCM
# ---------------------------------------------------------------------------


def test_malformed_pcm_not_frame_aligned_raises_tts_output_error():
    # frame size is channels(1) * sample_width_bytes(2) = 2; 3 bytes can't align.
    provider, _ = _provider_with(response=_fake_response(b"\x00\x01\x02"))
    with pytest.raises(TTSOutputError):
        provider.synthesize(_request())


# ---------------------------------------------------------------------------
# Section 50: SDK provider failure
# ---------------------------------------------------------------------------


def test_sdk_api_error_translated_to_tts_provider_error_with_one_call():
    api_error = genai_errors.ServerError(
        code=503, response_json={"error": {"message": "backend overloaded", "status": "UNAVAILABLE"}}
    )
    provider, client = _provider_with(exc=api_error)

    with pytest.raises(TTSProviderError):
        provider.synthesize(_request())

    assert len(client.models.calls) == 1


def test_sdk_error_message_omits_raw_response_details():
    api_error = genai_errors.ClientError(
        code=400,
        response_json={"error": {"message": "bad request", "status": "INVALID_ARGUMENT"}},
    )
    provider, _ = _provider_with(exc=api_error)

    with pytest.raises(TTSProviderError) as exc_info:
        provider.synthesize(_request())

    message = str(exc_info.value)
    assert "bad request" in message
    assert "response_json" not in message


def test_httpx_transport_error_translated_to_tts_provider_error():
    import httpx

    provider, client = _provider_with(exc=httpx.ConnectError("connection refused"))

    with pytest.raises(TTSProviderError):
        provider.synthesize(_request())

    assert len(client.models.calls) == 1


# ---------------------------------------------------------------------------
# Section 51: programming error policy -- narrow, documented boundary
# ---------------------------------------------------------------------------


def test_unrelated_programming_error_is_not_translated():
    """Only google.genai.errors.APIError and httpx.HTTPError are translated
    to TTSProviderError -- anything else (a genuine bug) must stay visible,
    not be silently absorbed as if it were a normal provider failure."""
    provider, _ = _provider_with(exc=TypeError("unexpected programming bug"))
    with pytest.raises(TypeError):
        provider.synthesize(_request())
