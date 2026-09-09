"""Phase 26 focused tests: layer-composition model/contract validation
(app/layer_compositor/models.py).

Pure pydantic-level tests -- no Pillow, no rendering, no VisualRenderer.
"""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from app.layer_compositor.models import (
    BackgroundLayer,
    LayerAnchor,
    LayerCompositionSpec,
    LayerScale,
    LayerSourceType,
    OverlayLayer,
)


def _background(id="bg", path="bg.png") -> BackgroundLayer:
    return BackgroundLayer(id=id, source_path=path)


def _overlay(id="ov", path="ov.png", **overrides) -> OverlayLayer:
    fields = dict(id=id, source_path=path)
    fields.update(overrides)
    return OverlayLayer(**fields)


# ---------------------------------------------------------------------------
# LayerSourceType / discriminated union
# ---------------------------------------------------------------------------


def test_layer_source_type_has_exactly_two_values():
    assert {member.value for member in LayerSourceType} == {"BACKGROUND", "OVERLAY"}


def test_layer_anchor_has_exactly_nine_values():
    assert {member.value for member in LayerAnchor} == {
        "TOP_LEFT", "TOP_CENTER", "TOP_RIGHT",
        "CENTER_LEFT", "CENTER", "CENTER_RIGHT",
        "BOTTOM_LEFT", "BOTTOM_CENTER", "BOTTOM_RIGHT",
    }


def test_background_layer_has_no_placement_fields():
    background = _background()
    assert not hasattr(background, "anchor")
    assert not hasattr(background, "scale")
    assert not hasattr(background, "z_index")


# ---------------------------------------------------------------------------
# LayerScale
# ---------------------------------------------------------------------------


def test_scale_neither_supplied_is_valid():
    scale = LayerScale()
    assert scale.relative_height is None
    assert scale.relative_width is None


def test_scale_relative_height_alone_is_valid():
    scale = LayerScale(relative_height=0.3)
    assert scale.relative_height == 0.3


def test_scale_relative_width_alone_is_valid():
    scale = LayerScale(relative_width=0.5)
    assert scale.relative_width == 0.5


def test_scale_both_supplied_rejected():
    with pytest.raises(ValidationError):
        LayerScale(relative_height=0.3, relative_width=0.5)


@pytest.mark.parametrize("field", ["relative_height", "relative_width"])
@pytest.mark.parametrize("value", [0.0, -0.1, 1.1])
def test_scale_out_of_bounds_rejected(field, value):
    with pytest.raises(ValidationError):
        LayerScale(**{field: value})


@pytest.mark.parametrize("field", ["relative_height", "relative_width"])
def test_scale_boundary_value_one_accepted(field):
    scale = LayerScale(**{field: 1.0})
    assert getattr(scale, field) == 1.0


# ---------------------------------------------------------------------------
# LayerCompositionSpec: exactly one background
# ---------------------------------------------------------------------------


def test_exactly_one_background_required_zero_rejected():
    with pytest.raises(ValidationError):
        LayerCompositionSpec(layers=[_overlay()], output_path="out.png")


def test_exactly_one_background_required_multiple_rejected():
    with pytest.raises(ValidationError):
        LayerCompositionSpec(
            layers=[_background(id="bg1"), _background(id="bg2")], output_path="out.png"
        )


def test_exactly_one_background_accepted():
    spec = LayerCompositionSpec(layers=[_background()], output_path="out.png")
    assert len(spec.layers) == 1


# ---------------------------------------------------------------------------
# Duplicate / blank layer ids
# ---------------------------------------------------------------------------


def test_duplicate_layer_id_rejected():
    with pytest.raises(ValidationError):
        LayerCompositionSpec(
            layers=[_background(id="dup"), _overlay(id="dup")], output_path="out.png"
        )


def test_blank_layer_id_rejected():
    with pytest.raises(ValidationError):
        BackgroundLayer(id="   ", source_path="bg.png")


def test_distinct_layer_ids_accepted():
    spec = LayerCompositionSpec(
        layers=[_background(id="bg"), _overlay(id="ov1"), _overlay(id="ov2")],
        output_path="out.png",
    )
    assert len(spec.layers) == 3


# ---------------------------------------------------------------------------
# PNG-only output enforced
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("output_path", ["out.jpg", "out.jpeg", "out.gif", "out"])
def test_non_png_output_path_rejected(output_path):
    with pytest.raises(ValidationError):
        LayerCompositionSpec(layers=[_background()], output_path=output_path)


def test_png_output_path_accepted():
    spec = LayerCompositionSpec(layers=[_background()], output_path="out.png")
    assert str(spec.output_path).endswith(".png")


# ---------------------------------------------------------------------------
# OverlayLayer margin
# ---------------------------------------------------------------------------


def test_negative_margin_rejected():
    with pytest.raises(ValidationError):
        _overlay(margin=-1)


def test_z_index_defaults_to_zero():
    assert _overlay().z_index == 0


def test_z_index_can_be_negative():
    overlay = _overlay(z_index=-5)
    assert overlay.z_index == -5
