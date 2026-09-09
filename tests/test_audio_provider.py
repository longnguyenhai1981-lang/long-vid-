from __future__ import annotations

import pytest

from app.audio.errors import TTSProviderError
from app.audio.fake import FakeTTSProvider
from app.audio.models import TTSRequest, TTSResponse
from app.audio.provider import TTSProvider


def _response(**overrides):
    fields = dict(audio_bytes=b"RIFF....", provider="fake-tts", audio_format="WAV")
    fields.update(overrides)
    return TTSResponse(**fields)


def _request(**overrides):
    fields = dict(
        text="Đây là câu C001.",
        voice_id="voice-01",
        voice_state="NEUTRAL",
        pace="NORMAL",
        energy="MEDIUM",
        output_format="WAV",
    )
    fields.update(overrides)
    return TTSRequest(**fields)


def test_fake_provider_satisfies_tts_provider_protocol():
    provider = FakeTTSProvider([_response()])
    assert isinstance(provider, TTSProvider)


def test_fake_provider_returns_responses_in_sequence():
    r1, r2 = _response(provider_request_id="one"), _response(provider_request_id="two")
    provider = FakeTTSProvider([r1, r2])
    assert provider.synthesize(_request()) is r1
    assert provider.synthesize(_request()) is r2


def test_fake_provider_raises_configured_exception_then_continues():
    provider = FakeTTSProvider([TTSProviderError("boom"), _response()])
    with pytest.raises(TTSProviderError, match="boom"):
        provider.synthesize(_request())
    assert provider.synthesize(_request()) is not None


def test_fake_provider_records_received_requests_in_order():
    provider = FakeTTSProvider([_response(), _response()])
    req1, req2 = _request(text="first"), _request(text="second")
    provider.synthesize(req1)
    provider.synthesize(req2)
    assert provider.received_requests == [req1, req2]
    assert provider.call_count == 2


def test_fake_provider_exhaustion_raises_provider_error():
    provider = FakeTTSProvider([_response()])
    provider.synthesize(_request())
    with pytest.raises(TTSProviderError):
        provider.synthesize(_request())
