"""OPTIONAL live Cloudflare Workers AI image smoke test.

Deliberately excluded from the default `pytest` run: it makes a real
network call, consumes real API quota, and requires real credentials. It
is gated by BOTH a custom marker (`live_cloudflare_image`, registered in
pyproject.toml) and an explicit environment variable, and skips cleanly
-- never errors, never requires CLOUDFLARE_ACCOUNT_ID/
CLOUDFLARE_API_TOKEN -- unless a human opts in on purpose:

    RUN_LIVE_CLOUDFLARE_IMAGE=1 CLOUDFLARE_ACCOUNT_ID=... CLOUDFLARE_API_TOKEN=... \\
        pytest tests/test_cloudflare_image_live_smoke.py -m live_cloudflare_image -q

Generates exactly ONE small, simple, non-sensitive still -- no batch, no
quota-heavy evaluation.
"""

from __future__ import annotations

import os

import pytest

_ENABLED = os.environ.get("RUN_LIVE_CLOUDFLARE_IMAGE") == "1"


@pytest.mark.live_cloudflare_image
@pytest.mark.skipif(
    not _ENABLED,
    reason="Live Cloudflare Image test disabled by default; set RUN_LIVE_CLOUDFLARE_IMAGE=1 to enable",
)
def test_live_cloudflare_image_generates_one_simple_still(tmp_path):
    if not os.environ.get("CLOUDFLARE_ACCOUNT_ID") or not os.environ.get("CLOUDFLARE_API_TOKEN"):
        pytest.skip(
            "CLOUDFLARE_ACCOUNT_ID/CLOUDFLARE_API_TOKEN are not both set; "
            "cannot run the live Cloudflare Image smoke test"
        )

    from app.visual.models import VisualRenderRequest
    from app.visual.providers.cloudflare import CloudflareImageConfig, CloudflareImageProvider

    provider = CloudflareImageProvider(CloudflareImageConfig())
    request = VisualRenderRequest(
        render_job_id="LIVE_SMOKE_R1",
        beat_id="LIVE_SMOKE",
        media_type="GENERATED_STILL",
        concept="An empty suspension bridge under a cloudy sky, clear structural silhouette",
        primary_focus="The empty suspension bridge",
        output_format="JPG",
    )

    response = provider.render(request)

    out_path = tmp_path / "live_smoke.jpg"
    out_path.write_bytes(response.asset_bytes)

    assert out_path.exists()
    assert response.provider == "cloudflare-workers-ai"
    assert len(response.asset_bytes) > 0
