from __future__ import annotations

import pytest

from app.visual.errors import VisualProviderError
from app.visual.fake import FakeVisualProvider
from app.visual.models import VisualRenderRequest, VisualRenderResponse
from app.visual.provider import VisualProvider


def _response(**overrides):
    fields = dict(asset_bytes=b"fake-png-bytes", provider="fake-visual", output_format="PNG")
    fields.update(overrides)
    return VisualRenderResponse(**fields)


def _request(**overrides):
    fields = dict(
        render_job_id="V001_R1",
        beat_id="V001",
        media_type="GENERATED_STILL",
        concept="A bridge deck twisting in the wind",
        primary_focus="The bridge deck",
        output_format="PNG",
    )
    fields.update(overrides)
    return VisualRenderRequest(**fields)


def test_fake_provider_satisfies_visual_provider_protocol():
    provider = FakeVisualProvider([_response()])
    assert isinstance(provider, VisualProvider)


def test_fake_provider_returns_responses_in_sequence():
    r1, r2 = _response(provider_request_id="one"), _response(provider_request_id="two")
    provider = FakeVisualProvider([r1, r2])
    assert provider.render(_request()) is r1
    assert provider.render(_request()) is r2


def test_fake_provider_raises_configured_exception_then_continues():
    provider = FakeVisualProvider([VisualProviderError("boom"), _response()])
    with pytest.raises(VisualProviderError, match="boom"):
        provider.render(_request())
    assert provider.render(_request()) is not None


def test_fake_provider_records_received_requests_in_order():
    provider = FakeVisualProvider([_response(), _response()])
    req1 = _request(beat_id="V001", render_job_id="V001_R1")
    req2 = _request(beat_id="V002", render_job_id="V002_R1")
    provider.render(req1)
    provider.render(req2)
    assert provider.received_requests == [req1, req2]
    assert provider.call_count == 2


def test_fake_provider_exhaustion_raises_provider_error():
    provider = FakeVisualProvider([_response()])
    provider.render(_request())
    with pytest.raises(VisualProviderError):
        provider.render(_request())
