from __future__ import annotations

import pytest
from pydantic import ValidationError

from app.audio.models import TTSRequest, TTSResponse


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


def test_valid_tts_request():
    req = _request()
    assert req.metadata == {}
    assert req.provider_options == {}


def test_blank_text_rejected():
    with pytest.raises(ValidationError):
        _request(text="   ")


def test_blank_voice_id_rejected():
    with pytest.raises(ValidationError):
        _request(voice_id="   ")


def test_tts_request_rejects_unknown_field():
    with pytest.raises(ValidationError):
        _request(not_a_real_field="oops")


def test_tts_request_metadata_and_provider_options_round_trip():
    req = _request(
        metadata={"chunk_id": "C001", "take_number": "1"},
        provider_options={"stability": "0.5"},
    )
    assert req.metadata == {"chunk_id": "C001", "take_number": "1"}
    assert req.provider_options == {"stability": "0.5"}


def _response(**overrides):
    fields = dict(audio_bytes=b"RIFF....", provider="fake-tts", audio_format="WAV")
    fields.update(overrides)
    return TTSResponse(**fields)


def test_valid_tts_response():
    response = _response()
    assert response.model is None
    assert response.duration_seconds is None
    assert response.provider_request_id is None
    assert response.metadata == {}


def test_empty_audio_bytes_rejected():
    with pytest.raises(ValidationError):
        _response(audio_bytes=b"")


def test_blank_provider_rejected():
    with pytest.raises(ValidationError):
        _response(provider="   ")


def test_non_positive_duration_rejected():
    with pytest.raises(ValidationError):
        _response(duration_seconds=0)
    with pytest.raises(ValidationError):
        _response(duration_seconds=-1.5)


def test_positive_duration_allowed():
    response = _response(duration_seconds=3.25)
    assert response.duration_seconds == 3.25


def test_tts_response_rejects_unknown_field():
    with pytest.raises(ValidationError):
        _response(not_a_real_field="oops")
