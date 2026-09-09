"""OPTIONAL live Gemini TTS smoke test.

Deliberately excluded from the default `pytest` run: it makes a real
network call, consumes real API quota, and requires a real secret. It is
gated by BOTH a custom marker (`live_tts`, registered in pyproject.toml)
and an explicit environment variable, and skips cleanly -- never errors,
never requires GEMINI_API_KEY -- unless a human opts in on purpose:

    RUN_LIVE_GEMINI_TTS=1 GEMINI_API_KEY=... pytest tests/test_gemini_tts_live_smoke.py -m live_tts -q
"""

from __future__ import annotations

import os

import pytest

_ENABLED = os.environ.get("RUN_LIVE_GEMINI_TTS") == "1"


@pytest.mark.live_tts
@pytest.mark.skipif(
    not _ENABLED,
    reason="Live Gemini TTS test disabled by default; set RUN_LIVE_GEMINI_TTS=1 to enable",
)
def test_live_gemini_tts_synthesizes_one_short_vietnamese_sample(tmp_path):
    if not os.environ.get("GEMINI_API_KEY"):
        pytest.skip("GEMINI_API_KEY is not set; cannot run the live Gemini TTS smoke test")

    import wave

    from app.audio.models import TTSRequest
    from app.audio.providers.gemini import GeminiTTSConfig, GeminiTTSProvider

    provider = GeminiTTSProvider(GeminiTTSConfig())
    request = TTSRequest(
        text="Chào! Một Tí Lý đây.",
        voice_id="Puck",
        voice_state="NEUTRAL",
        pace="NORMAL",
        energy="MEDIUM",
        output_format="WAV",
    )

    response = provider.synthesize(request)

    out_path = tmp_path / "live_smoke.wav"
    out_path.write_bytes(response.audio_bytes)

    assert out_path.exists()
    assert response.provider == "gemini"
    assert response.duration_seconds is not None and response.duration_seconds > 0
    with wave.open(str(out_path)) as wav_file:
        assert wav_file.getnframes() > 0
