from __future__ import annotations

import pytest

from app.visual.errors import VisualProviderUnavailableError
from app.visual.fake import FakeVisualProvider
from app.visual.models import VisualRenderRequest, VisualRenderResponse
from app.visual.provider import VisualProvider
from app.visual.router import MediaTypeVisualProvider


def _request(**overrides) -> VisualRenderRequest:
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


def _response(**overrides) -> VisualRenderResponse:
    fields = dict(asset_bytes=b"fake-png-bytes", provider="fake-visual", output_format="PNG")
    fields.update(overrides)
    return VisualRenderResponse(**fields)


def test_media_type_visual_provider_satisfies_visual_provider_protocol():
    router = MediaTypeVisualProvider({})
    assert isinstance(router, VisualProvider)


def test_dispatches_to_the_provider_configured_for_the_requests_media_type():
    still_provider = FakeVisualProvider([_response(provider_request_id="still-1")])
    diagram_provider = FakeVisualProvider([_response(provider_request_id="diagram-1")])
    router = MediaTypeVisualProvider(
        {
            "GENERATED_STILL": still_provider,
            "DIAGRAM": diagram_provider,
        }
    )

    response = router.render(_request(media_type="GENERATED_STILL"))

    assert response.provider_request_id == "still-1"
    assert still_provider.call_count == 1
    assert diagram_provider.call_count == 0


def test_raises_unavailable_when_no_provider_is_configured_for_the_media_type():
    still_provider = FakeVisualProvider([_response()])
    router = MediaTypeVisualProvider({"GENERATED_STILL": still_provider})

    with pytest.raises(VisualProviderUnavailableError):
        router.render(_request(media_type="DIAGRAM"))

    assert still_provider.call_count == 0


def test_no_fallback_guessing_an_unmapped_media_type_never_falls_back_to_any_configured_provider():
    only_provider = FakeVisualProvider([_response()])
    router = MediaTypeVisualProvider({"ASSET_REUSE": only_provider})

    with pytest.raises(VisualProviderUnavailableError):
        router.render(_request(media_type="GENERATED_STILL"))

    assert only_provider.call_count == 0
