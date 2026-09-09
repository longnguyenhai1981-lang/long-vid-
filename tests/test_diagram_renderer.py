"""Phase 25 focused tests: DiagramRenderer (app/diagram_renderer/renderer.py).

Exercises the actual Pillow-based drawing backend directly -- no
VisualRenderer, no VisualProvider, no LLM, nothing network-related.
"""

from __future__ import annotations

from pathlib import Path

import pytest
from PIL import Image

from app.diagram_renderer.errors import DiagramRenderError
from app.diagram_renderer.models import DiagramRenderResult
from app.diagram_renderer.renderer import DiagramRenderer
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
    LineStyle,
)


def _p(x: float, y: float) -> DiagramPoint:
    return DiagramPoint(x=x, y=y)


def _spec_with(*elements, width=200, height=150, background_color="#FFFFFF") -> DiagramSpec:
    return DiagramSpec(
        canvas=DiagramCanvas(width=width, height=height, background_color=background_color),
        elements=list(elements),
    )


def test_line_renders(tmp_path):
    spec = _spec_with(DiagramLine(start=_p(0.1, 0.1), end=_p(0.9, 0.1)))
    result = DiagramRenderer().render(spec, tmp_path / "line.png")
    assert (tmp_path / "line.png").is_file()
    assert isinstance(result, DiagramRenderResult)


def test_dashed_line_renders(tmp_path):
    spec = _spec_with(
        DiagramLine(start=_p(0.1, 0.1), end=_p(0.9, 0.1), style=DiagramStyle(line_style=LineStyle.DASHED))
    )
    DiagramRenderer().render(spec, tmp_path / "dashed.png")
    assert (tmp_path / "dashed.png").is_file()


def test_arrow_renders(tmp_path):
    spec = _spec_with(DiagramArrow(start=_p(0.1, 0.5), end=_p(0.9, 0.5)))
    DiagramRenderer().render(spec, tmp_path / "arrow.png")
    assert (tmp_path / "arrow.png").is_file()


def test_rectangle_renders(tmp_path):
    spec = _spec_with(
        DiagramRectangle(
            top_left=_p(0.1, 0.1), bottom_right=_p(0.5, 0.5), style=DiagramStyle(fill_color="#CCCCCC")
        )
    )
    DiagramRenderer().render(spec, tmp_path / "rect.png")
    assert (tmp_path / "rect.png").is_file()


def test_ellipse_renders(tmp_path):
    spec = _spec_with(DiagramEllipse(center=_p(0.5, 0.5), radius_x=0.2, radius_y=0.1))
    DiagramRenderer().render(spec, tmp_path / "ellipse.png")
    assert (tmp_path / "ellipse.png").is_file()


def test_polyline_renders(tmp_path):
    spec = _spec_with(
        DiagramPolyline(points=[_p(0.1, 0.9), _p(0.3, 0.6), _p(0.5, 0.9), _p(0.7, 0.6)])
    )
    DiagramRenderer().render(spec, tmp_path / "polyline.png")
    assert (tmp_path / "polyline.png").is_file()


def test_closed_filled_polyline_renders(tmp_path):
    spec = _spec_with(
        DiagramPolyline(
            points=[_p(0.2, 0.2), _p(0.8, 0.2), _p(0.5, 0.8)],
            closed=True,
            style=DiagramStyle(fill_color="#FF0000"),
        )
    )
    DiagramRenderer().render(spec, tmp_path / "triangle.png")
    assert (tmp_path / "triangle.png").is_file()


def test_text_label_renders(tmp_path):
    spec = _spec_with(DiagramTextLabel(position=_p(0.5, 0.5), text="F = ma", align="CENTER"))
    DiagramRenderer().render(spec, tmp_path / "text.png")
    assert (tmp_path / "text.png").is_file()


def test_arc_renders(tmp_path):
    spec = _spec_with(
        DiagramArc(center=_p(0.5, 0.5), radius_x=0.3, radius_y=0.3, start_angle_degrees=0, end_angle_degrees=270)
    )
    DiagramRenderer().render(spec, tmp_path / "arc.png")
    assert (tmp_path / "arc.png").is_file()


def test_dot_renders(tmp_path):
    spec = _spec_with(DiagramDot(center=_p(0.5, 0.5), radius=0.03))
    DiagramRenderer().render(spec, tmp_path / "dot.png")
    assert (tmp_path / "dot.png").is_file()


def test_all_element_types_together_render_without_error(tmp_path):
    spec = _spec_with(
        DiagramLine(start=_p(0.05, 0.05), end=_p(0.3, 0.05)),
        DiagramArrow(start=_p(0.5, 0.5), end=_p(0.9, 0.5)),
        DiagramRectangle(top_left=_p(0.1, 0.6), bottom_right=_p(0.4, 0.9)),
        DiagramEllipse(center=_p(0.7, 0.2), radius_x=0.1, radius_y=0.05),
        DiagramPolyline(points=[_p(0.05, 0.95), _p(0.15, 0.85), _p(0.25, 0.95)]),
        DiagramTextLabel(position=_p(0.5, 0.05), text="Hello Diagram", align="CENTER"),
        DiagramArc(center=_p(0.8, 0.8), radius_x=0.1, radius_y=0.1, start_angle_degrees=0, end_angle_degrees=270),
        DiagramDot(center=_p(0.5, 0.85), radius=0.02),
    )
    result = DiagramRenderer().render(spec, tmp_path / "all.png")
    assert result.width == 200 and result.height == 150


# ---------------------------------------------------------------------------
# PNG output / dimensions / background policy
# ---------------------------------------------------------------------------


def test_output_is_a_valid_png(tmp_path):
    spec = _spec_with(DiagramLine(start=_p(0.1, 0.1), end=_p(0.9, 0.9)))
    output_path = tmp_path / "out.png"
    DiagramRenderer().render(spec, output_path)

    with Image.open(output_path) as image:
        assert image.format == "PNG"


def test_output_dimensions_equal_canvas_dimensions(tmp_path):
    spec = _spec_with(DiagramLine(start=_p(0.1, 0.1), end=_p(0.9, 0.9)), width=321, height=234)
    output_path = tmp_path / "sized.png"
    result = DiagramRenderer().render(spec, output_path)

    assert result.width == 321
    assert result.height == 234
    with Image.open(output_path) as image:
        assert image.size == (321, 234)


def test_white_background_is_opaque_rgb(tmp_path):
    spec = _spec_with(DiagramLine(start=_p(0.05, 0.05), end=_p(0.1, 0.05)), background_color="#FFFFFF")
    output_path = tmp_path / "white_bg.png"
    DiagramRenderer().render(spec, output_path)

    with Image.open(output_path) as image:
        assert image.mode == "RGB"
        assert image.getpixel((0, 0)) == (255, 255, 255)


def test_transparent_background_is_rgba_zero_alpha(tmp_path):
    spec = _spec_with(DiagramLine(start=_p(0.05, 0.05), end=_p(0.1, 0.05)), background_color=None)
    output_path = tmp_path / "transparent_bg.png"
    DiagramRenderer().render(spec, output_path)

    with Image.open(output_path) as image:
        assert image.mode == "RGBA"
        assert image.getpixel((0, 0))[3] == 0  # untouched corner pixel is fully transparent


# ---------------------------------------------------------------------------
# Determinism / no provider or LLM calls
# ---------------------------------------------------------------------------


def test_deterministic_repeated_output_for_identical_input(tmp_path):
    spec = _spec_with(
        DiagramArrow(start=_p(0.1, 0.5), end=_p(0.9, 0.5)),
        DiagramTextLabel(position=_p(0.5, 0.1), text="Deterministic", align="CENTER"),
    )
    renderer = DiagramRenderer()
    first_path, second_path = tmp_path / "first.png", tmp_path / "second.png"

    renderer.render(spec, first_path)
    renderer.render(spec, second_path)

    assert first_path.read_bytes() == second_path.read_bytes()


def test_diagram_renderer_has_no_network_or_provider_dependency():
    """DiagramRenderer() takes no configuration at all -- structurally
    incapable of holding an injected VisualProvider, API client, or
    network handle (no __init__ defined; a bogus constructor argument is
    rejected by object.__init__ itself)."""
    DiagramRenderer()  # succeeds with zero arguments
    with pytest.raises(TypeError):
        DiagramRenderer(visual_provider="not a real dependency")


# ---------------------------------------------------------------------------
# Render failure
# ---------------------------------------------------------------------------


def test_render_failure_when_output_path_unwritable(tmp_path):
    spec = _spec_with(DiagramLine(start=_p(0.1, 0.1), end=_p(0.9, 0.9)))
    blocking_dir = tmp_path / "blocked.png"
    blocking_dir.mkdir()  # a directory sits where the output file should go

    with pytest.raises(DiagramRenderError):
        DiagramRenderer().render(spec, blocking_dir)
