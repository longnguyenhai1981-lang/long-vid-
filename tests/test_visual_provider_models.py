from __future__ import annotations

import pytest
from pydantic import ValidationError

from app.models.common import VisualOutputFormat
from app.visual.models import VisualRenderRequest, VisualRenderResponse


def test_visual_output_format_has_exactly_two_values():
    assert {member.value for member in VisualOutputFormat} == {"PNG", "JPG"}


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


def test_valid_visual_render_request():
    req = _request()
    assert req.secondary_elements == []
    assert req.context_elements == []
    assert req.ti_state is None
    assert req.motion_intent is None
    assert req.reuse_key is None
    assert req.metadata == {}


def test_blank_render_job_id_rejected():
    with pytest.raises(ValidationError):
        _request(render_job_id="  ")


def test_blank_beat_id_rejected():
    with pytest.raises(ValidationError):
        _request(beat_id="  ")


def test_blank_concept_rejected():
    with pytest.raises(ValidationError):
        _request(concept="  ")


def test_blank_primary_focus_rejected():
    with pytest.raises(ValidationError):
        _request(primary_focus="  ")


def test_visual_render_request_rejects_unknown_field():
    with pytest.raises(ValidationError):
        _request(not_a_real_field="oops")


def _response(**overrides):
    fields = dict(asset_bytes=b"fake-png-bytes", provider="fake-visual", output_format="PNG")
    fields.update(overrides)
    return VisualRenderResponse(**fields)


def test_valid_visual_render_response():
    response = _response()
    assert response.model is None
    assert response.width is None
    assert response.height is None
    assert response.provider_request_id is None
    assert response.metadata == {}


def test_empty_asset_bytes_rejected():
    with pytest.raises(ValidationError):
        _response(asset_bytes=b"")


def test_blank_provider_rejected():
    with pytest.raises(ValidationError):
        _response(provider="  ")


@pytest.mark.parametrize("field", ["width", "height"])
def test_non_positive_dimension_rejected(field):
    with pytest.raises(ValidationError):
        _response(**{field: 0})
    with pytest.raises(ValidationError):
        _response(**{field: -10})


def test_positive_dimensions_allowed():
    response = _response(width=512, height=384)
    assert response.width == 512
    assert response.height == 384


def test_visual_render_response_rejects_unknown_field():
    with pytest.raises(ValidationError):
        _response(not_a_real_field="oops")
