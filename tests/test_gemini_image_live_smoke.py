"""OPTIONAL live Gemini Image smoke test.

Deliberately excluded from the default `pytest` run: it makes a real
network call, consumes real API quota, and requires a real secret. It is
gated by BOTH a custom marker (`live_image`, registered in
pyproject.toml) and an explicit environment variable, and skips cleanly
-- never errors, never requires GEMINI_API_KEY -- unless a human opts in
on purpose:

    RUN_LIVE_GEMINI_IMAGE=1 GEMINI_API_KEY=... pytest tests/test_gemini_image_live_smoke.py -m live_image -q

Generates exactly ONE small, simple, non-sensitive still -- no batch, no
quota-heavy evaluation.
"""

from __future__ import annotations

import os

import pytest

_ENABLED = os.environ.get("RUN_LIVE_GEMINI_IMAGE") == "1"


@pytest.mark.live_image
@pytest.mark.skipif(
    not _ENABLED,
    reason="Live Gemini Image test disabled by default; set RUN_LIVE_GEMINI_IMAGE=1 to enable",
)
def test_live_gemini_image_generates_one_simple_still(tmp_path):
    if not os.environ.get("GEMINI_API_KEY"):
        pytest.skip("GEMINI_API_KEY is not set; cannot run the live Gemini Image smoke test")

    from app.visual.models import VisualRenderRequest
    from app.visual.providers.gemini import GeminiImageConfig, GeminiImageProvider

    provider = GeminiImageProvider(GeminiImageConfig())
    request = VisualRenderRequest(
        render_job_id="LIVE_SMOKE_R1",
        beat_id="LIVE_SMOKE",
        media_type="GENERATED_STILL",
        concept="An empty suspension bridge under a cloudy sky, clear structural silhouette",
        primary_focus="The empty suspension bridge",
        output_format="PNG",
    )

    response = provider.render(request)

    out_path = tmp_path / "live_smoke.png"
    out_path.write_bytes(response.asset_bytes)

    assert out_path.exists()
    assert response.provider == "gemini-image"
    assert len(response.asset_bytes) > 0
    assert out_path.read_bytes()[:8] == b"\x89PNG\r\n\x1a\n"
