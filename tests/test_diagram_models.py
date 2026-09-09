"""Phase 25 focused tests: diagram model/contract validation
(app/models/diagram.py, and VisualBeat.diagram_spec in app/models/visual.py).

Pure pydantic-level tests -- no Pillow, no rendering, no VisualRenderer.
"""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from app.models.diagram import (
    DiagramArc,
    DiagramArrow,
    DiagramCanvas,
    DiagramDot,
    DiagramEllipse,
    DiagramLine,
    DiagramPoint,
    DiagramPolyline,
    DiagramRectangle,
    DiagramSpec,
    DiagramStyle,
    DiagramTextLabel,
)
from app.models.visual import VisualBeat


def _p(x: float, y: float) -> DiagramPoint:
    return DiagramPoint(x=x, y=y)


def _minimal_line() -> DiagramLine:
    return DiagramLine(start=_p(0.1, 0.1), end=_p(0.9, 0.9))


# ---------------------------------------------------------------------------
# VisualBeat.diagram_spec wiring
# ---------------------------------------------------------------------------


def test_diagram_beat_accepts_diagram_spec():
    spec = DiagramSpec(canvas=DiagramCanvas(width=100, height=100), elements=[_minimal_line()])
    beat = VisualBeat(
        beat_id="V1",
        script_line_ids=["L001"],
        narrative_node="Q0",
        visual_level="L1_ESTABLISH",
        visual_function="STORY",
        media_type="DIAGRAM",
        complexity="C1",
        concept="c",
        primary_focus="f",
        diagram_spec=spec,
    )
    assert beat.diagram_spec is spec


def test_non_diagram_beat_accepts_none_diagram_spec():
    beat = VisualBeat(
        beat_id="V1",
        script_line_ids=["L001"],
        narrative_node="Q0",
        visual_level="L1_ESTABLISH",
        visual_function="STORY",
        media_type="GENERATED_STILL",
        complexity="C1",
        concept="c",
        primary_focus="f",
    )
    assert beat.diagram_spec is None


# ---------------------------------------------------------------------------
# DiagramPoint: normalized coordinate bounds
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("x,y", [(0.0, 0.0), (1.0, 1.0), (0.5, 0.25)])
def test_normalized_coordinates_within_bounds_validate(x, y):
    point = DiagramPoint(x=x, y=y)
    assert point.x == x and point.y == y


@pytest.mark.parametrize("x,y", [(-0.01, 0.5), (1.01, 0.5), (0.5, -0.01), (0.5, 1.01)])
def test_coordinates_outside_bounds_rejected(x, y):
    with pytest.raises(ValidationError):
        DiagramPoint(x=x, y=y)


# ---------------------------------------------------------------------------
# DiagramCanvas
# ---------------------------------------------------------------------------


def test_valid_canvas_accepted():
    canvas = DiagramCanvas(width=800, height=600)
    assert canvas.background_color == "#FFFFFF"


@pytest.mark.parametrize("width,height", [(0, 100), (100, 0), (-10, 100), (100, -10)])
def test_invalid_canvas_dimensions_rejected(width, height):
    with pytest.raises(ValidationError):
        DiagramCanvas(width=width, height=height)


def test_canvas_transparent_background_is_explicit_none():
    canvas = DiagramCanvas(width=100, height=100, background_color=None)
    assert canvas.background_color is None


# ---------------------------------------------------------------------------
# DiagramSpec: element list / duplicate ids
# ---------------------------------------------------------------------------


def test_empty_element_list_rejected():
    with pytest.raises(ValidationError):
        DiagramSpec(canvas=DiagramCanvas(width=100, height=100), elements=[])


def test_duplicate_element_ids_rejected():
    with pytest.raises(ValidationError):
        DiagramSpec(
            canvas=DiagramCanvas(width=100, height=100),
            elements=[
                DiagramLine(element_id="e1", start=_p(0.0, 0.0), end=_p(0.5, 0.5)),
                DiagramLine(element_id="e1", start=_p(0.1, 0.1), end=_p(0.6, 0.6)),
            ],
        )


def test_distinct_element_ids_accepted():
    spec = DiagramSpec(
        canvas=DiagramCanvas(width=100, height=100),
        elements=[
            DiagramLine(element_id="e1", start=_p(0.0, 0.0), end=_p(0.5, 0.5)),
            DiagramLine(element_id="e2", start=_p(0.1, 0.1), end=_p(0.6, 0.6)),
        ],
    )
    assert len(spec.elements) == 2


def test_unsupported_element_subtype_rejected():
    with pytest.raises(ValidationError):
        DiagramSpec(
            canvas=DiagramCanvas(width=100, height=100),
            elements=[{"type": "BOGUS_SHAPE"}],
        )


# ---------------------------------------------------------------------------
# Zero-length LINE/ARROW disallowed
# ---------------------------------------------------------------------------


def test_zero_length_line_rejected():
    with pytest.raises(ValidationError):
        DiagramLine(start=_p(0.5, 0.5), end=_p(0.5, 0.5))


def test_zero_length_arrow_rejected():
    with pytest.raises(ValidationError):
        DiagramArrow(start=_p(0.5, 0.5), end=_p(0.5, 0.5))


def test_nonzero_line_and_arrow_accepted():
    DiagramLine(start=_p(0.1, 0.1), end=_p(0.2, 0.1))
    DiagramArrow(start=_p(0.1, 0.1), end=_p(0.2, 0.1))


# ---------------------------------------------------------------------------
# RECTANGLE / ELLIPSE / DOT shape validity
# ---------------------------------------------------------------------------


def test_zero_area_rectangle_rejected():
    with pytest.raises(ValidationError):
        DiagramRectangle(top_left=_p(0.2, 0.2), bottom_right=_p(0.2, 0.5))


def test_inverted_rectangle_rejected():
    with pytest.raises(ValidationError):
        DiagramRectangle(top_left=_p(0.5, 0.5), bottom_right=_p(0.2, 0.2))


@pytest.mark.parametrize("radius_x,radius_y", [(0.0, 0.1), (0.1, 0.0), (-0.1, 0.1), (1.1, 0.1)])
def test_invalid_ellipse_radii_rejected(radius_x, radius_y):
    with pytest.raises(ValidationError):
        DiagramEllipse(center=_p(0.5, 0.5), radius_x=radius_x, radius_y=radius_y)


def test_invalid_dot_radius_rejected():
    with pytest.raises(ValidationError):
        DiagramDot(center=_p(0.5, 0.5), radius=0.0)


# ---------------------------------------------------------------------------
# POLYLINE
# ---------------------------------------------------------------------------


def test_polyline_requires_at_least_two_points():
    with pytest.raises(ValidationError):
        DiagramPolyline(points=[_p(0.1, 0.1)])


def test_polyline_with_two_points_accepted():
    polyline = DiagramPolyline(points=[_p(0.1, 0.1), _p(0.2, 0.2)])
    assert len(polyline.points) == 2


# ---------------------------------------------------------------------------
# TEXT_LABEL
# ---------------------------------------------------------------------------


def test_blank_text_label_rejected():
    with pytest.raises(ValidationError):
        DiagramTextLabel(position=_p(0.5, 0.5), text="   ")


def test_nonblank_text_label_accepted():
    label = DiagramTextLabel(position=_p(0.5, 0.5), text="F = ma")
    assert label.text == "F = ma"


# ---------------------------------------------------------------------------
# ARC angle validity
# ---------------------------------------------------------------------------


def test_arc_zero_sweep_rejected():
    with pytest.raises(ValidationError):
        DiagramArc(
            center=_p(0.5, 0.5), radius_x=0.2, radius_y=0.2,
            start_angle_degrees=45, end_angle_degrees=45,
        )


def test_arc_sweep_over_360_rejected():
    with pytest.raises(ValidationError):
        DiagramArc(
            center=_p(0.5, 0.5), radius_x=0.2, radius_y=0.2,
            start_angle_degrees=0, end_angle_degrees=400,
        )


def test_arc_valid_sweep_accepted():
    arc = DiagramArc(
        center=_p(0.5, 0.5), radius_x=0.2, radius_y=0.2,
        start_angle_degrees=0, end_angle_degrees=270,
    )
    assert arc.end_angle_degrees == 270


# ---------------------------------------------------------------------------
# DiagramStyle
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "field,value", [("stroke_width", 0), ("font_size", 0), ("arrowhead_size", 0)]
)
def test_style_non_positive_values_rejected(field, value):
    with pytest.raises(ValidationError):
        DiagramStyle(**{field: value})


def test_style_defaults_are_sane():
    style = DiagramStyle()
    assert style.stroke_width > 0
    assert style.font_size > 0
    assert style.fill_color is None
