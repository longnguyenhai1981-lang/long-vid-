"""DiagramRenderer: deterministic local diagram rendering (Phase 25).

Draws a DiagramSpec (app/models/diagram.py) directly to a PNG using
Pillow's ImageDraw -- no AI generation, no LLM-based layout, no
computer-vision placement, no auto-layout of any kind. Every element's
normalized [0.0, 1.0] coordinate is converted to an exact pixel position
via DiagramCanvas.width/height (see `_to_pixel`/`_to_pixel_scalar`); the
same DiagramSpec always produces the same output pixels, since Pillow's
raster drawing operations are themselves deterministic for fixed inputs
on a fixed Pillow version (already relied on identically by
app/ti_compositor/compositor.py, Phase 22).

Background policy (Phase 25 requirement #5 -- documented explicitly, not
left ambiguous): DiagramCanvas.background_color, when set (default
"#FFFFFF", opaque white), paints the entire canvas that color before any
element is drawn, and the output image is RGB (no alpha channel). When
explicitly set to None, the canvas is created as RGBA with a fully
transparent background (every pixel's alpha starts at 0) instead, and
each drawn element keeps its own opacity -- there is no third option, and
no per-project default beyond DiagramCanvas.background_color itself.

Font: every TEXT_LABEL uses Pillow's own bundled bitmap default font via
`PIL.ImageFont.load_default(size=...)` (Pillow >=10.1's sized variant,
confirmed available on this project's pinned Pillow>=11.0 -- see
pyproject.toml). No external font file is ever downloaded, bundled, or
read from the host system -- this keeps rendering fully offline and
licensing-free, at the cost of a fixed, plain typeface with no bold/
italic variants. Phase 25 explicitly excludes fancy typography.

Output is always PNG -- the only format DiagramCanvas/DiagramRenderResult
ever produce, matching TiCompositor's PNG-only policy (Phase 22) for the
same "deterministic lossless raster output" reason.
"""

from __future__ import annotations

import math
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont

from app.diagram_renderer.errors import DiagramRenderError
from app.diagram_renderer.models import DiagramRenderResult
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
    TextAlign,
)

_DASH_LENGTH_PX = 10
_GAP_LENGTH_PX = 6
_ARROWHEAD_SPREAD_DEGREES = 25.0
_TEXT_ANCHOR_BY_ALIGN = {
    TextAlign.LEFT: "la",
    TextAlign.CENTER: "ma",
    TextAlign.RIGHT: "ra",
}


class DiagramRenderer:
    """Deterministic local diagram rendering. Unlike VisualProvider/
    TiCompositor, there is no injected dependency and no configuration --
    every input a render needs is fully self-contained in the DiagramSpec
    passed to render(), and Pillow is already a hard project dependency
    (Phase 22)."""

    def render(self, spec: DiagramSpec, output_path: Path) -> DiagramRenderResult:
        canvas = spec.canvas
        image = _new_canvas(canvas)
        draw = ImageDraw.Draw(image)

        for element in spec.elements:
            _draw_element(draw, element, canvas)

        try:
            output_path.parent.mkdir(parents=True, exist_ok=True)
            image.save(output_path, format="PNG")
        except OSError as exc:
            raise DiagramRenderError(f"Failed to write diagram output to {output_path}: {exc}") from exc

        return DiagramRenderResult(output_path=output_path, width=canvas.width, height=canvas.height)


def _new_canvas(canvas: DiagramCanvas) -> Image.Image:
    if canvas.background_color is None:
        return Image.new("RGBA", (canvas.width, canvas.height), (0, 0, 0, 0))
    return Image.new("RGB", (canvas.width, canvas.height), canvas.background_color)


def _to_pixel(point: DiagramPoint, canvas: DiagramCanvas) -> tuple[int, int]:
    return (round(point.x * canvas.width), round(point.y * canvas.height))


def _to_pixel_x(value: float, canvas: DiagramCanvas) -> int:
    return round(value * canvas.width)


def _to_pixel_y(value: float, canvas: DiagramCanvas) -> int:
    return round(value * canvas.height)


def _to_pixel_scalar(value: float, canvas: DiagramCanvas) -> int:
    """For a size that should stay visually uniform regardless of the
    canvas's aspect ratio (DOT's single radius) -- scaled by the smaller
    of the two canvas dimensions."""
    return round(value * min(canvas.width, canvas.height))


def _draw_element(draw: ImageDraw.ImageDraw, element, canvas: DiagramCanvas) -> None:
    if isinstance(element, DiagramLine):
        _draw_line(draw, element, canvas)
    elif isinstance(element, DiagramArrow):
        _draw_arrow(draw, element, canvas)
    elif isinstance(element, DiagramRectangle):
        _draw_rectangle(draw, element, canvas)
    elif isinstance(element, DiagramEllipse):
        _draw_ellipse(draw, element, canvas)
    elif isinstance(element, DiagramPolyline):
        _draw_polyline(draw, element, canvas)
    elif isinstance(element, DiagramTextLabel):
        _draw_text_label(draw, element, canvas)
    elif isinstance(element, DiagramArc):
        _draw_arc(draw, element, canvas)
    elif isinstance(element, DiagramDot):
        _draw_dot(draw, element, canvas)
    else:  # pragma: no cover -- unreachable: DiagramSpec.elements is a
        # discriminated union over exactly these types; guarded
        # defensively rather than assumed (see this module's docstring).
        raise DiagramRenderError(f"Unsupported diagram element type: {type(element)!r}")


def _draw_line_segment(
    draw: ImageDraw.ImageDraw, p0: tuple[int, int], p1: tuple[int, int], style: DiagramStyle
) -> None:
    if style.line_style is LineStyle.DASHED:
        _draw_dashed_segment(draw, p0, p1, style.stroke_color, style.stroke_width)
    else:
        draw.line([p0, p1], fill=style.stroke_color, width=style.stroke_width)


def _draw_dashed_segment(
    draw: ImageDraw.ImageDraw,
    p0: tuple[int, int],
    p1: tuple[int, int],
    color: str,
    width: int,
) -> None:
    x0, y0 = p0
    x1, y1 = p1
    total_length = math.hypot(x1 - x0, y1 - y0)
    if total_length == 0:
        return
    dx, dy = (x1 - x0) / total_length, (y1 - y0) / total_length

    distance = 0.0
    draw_dash = True
    while distance < total_length:
        segment_length = _DASH_LENGTH_PX if draw_dash else _GAP_LENGTH_PX
        segment_end = min(distance + segment_length, total_length)
        if draw_dash:
            start = (x0 + dx * distance, y0 + dy * distance)
            end = (x0 + dx * segment_end, y0 + dy * segment_end)
            draw.line([start, end], fill=color, width=width)
        distance = segment_end
        draw_dash = not draw_dash


def _arrowhead_polygon(
    p0: tuple[int, int], p1: tuple[int, int], size: int
) -> list[tuple[float, float]]:
    angle = math.atan2(p1[1] - p0[1], p1[0] - p0[0])
    spread = math.radians(_ARROWHEAD_SPREAD_DEGREES)
    left = (p1[0] - size * math.cos(angle - spread), p1[1] - size * math.sin(angle - spread))
    right = (p1[0] - size * math.cos(angle + spread), p1[1] - size * math.sin(angle + spread))
    return [p1, left, right]


def _draw_line(draw: ImageDraw.ImageDraw, element: DiagramLine, canvas: DiagramCanvas) -> None:
    p0, p1 = _to_pixel(element.start, canvas), _to_pixel(element.end, canvas)
    _draw_line_segment(draw, p0, p1, element.style)


def _draw_arrow(draw: ImageDraw.ImageDraw, element: DiagramArrow, canvas: DiagramCanvas) -> None:
    p0, p1 = _to_pixel(element.start, canvas), _to_pixel(element.end, canvas)
    _draw_line_segment(draw, p0, p1, element.style)
    draw.polygon(_arrowhead_polygon(p0, p1, element.style.arrowhead_size), fill=element.style.stroke_color)


def _draw_rectangle(draw: ImageDraw.ImageDraw, element: DiagramRectangle, canvas: DiagramCanvas) -> None:
    top_left = _to_pixel(element.top_left, canvas)
    bottom_right = _to_pixel(element.bottom_right, canvas)
    draw.rectangle(
        [top_left, bottom_right],
        outline=element.style.stroke_color,
        fill=element.style.fill_color,
        width=element.style.stroke_width,
    )


def _draw_ellipse(draw: ImageDraw.ImageDraw, element: DiagramEllipse, canvas: DiagramCanvas) -> None:
    bbox = _ellipse_bbox(element.center, element.radius_x, element.radius_y, canvas)
    draw.ellipse(
        bbox,
        outline=element.style.stroke_color,
        fill=element.style.fill_color,
        width=element.style.stroke_width,
    )


def _draw_polyline(draw: ImageDraw.ImageDraw, element: DiagramPolyline, canvas: DiagramCanvas) -> None:
    pixel_points = [_to_pixel(point, canvas) for point in element.points]
    style = element.style

    if element.closed and style.fill_color is not None:
        draw.polygon(pixel_points, fill=style.fill_color)

    segments = list(zip(pixel_points, pixel_points[1:]))
    if element.closed:
        segments.append((pixel_points[-1], pixel_points[0]))
    for p0, p1 in segments:
        _draw_line_segment(draw, p0, p1, style)


def _draw_text_label(draw: ImageDraw.ImageDraw, element: DiagramTextLabel, canvas: DiagramCanvas) -> None:
    position = _to_pixel(element.position, canvas)
    font = ImageFont.load_default(size=element.style.font_size)
    anchor = _TEXT_ANCHOR_BY_ALIGN[element.align]
    draw.text(position, element.text, font=font, fill=element.style.text_color, anchor=anchor)


def _draw_arc(draw: ImageDraw.ImageDraw, element: DiagramArc, canvas: DiagramCanvas) -> None:
    bbox = _ellipse_bbox(element.center, element.radius_x, element.radius_y, canvas)
    draw.arc(
        bbox,
        start=element.start_angle_degrees,
        end=element.end_angle_degrees,
        fill=element.style.stroke_color,
        width=element.style.stroke_width,
    )


def _draw_dot(draw: ImageDraw.ImageDraw, element: DiagramDot, canvas: DiagramCanvas) -> None:
    cx, cy = _to_pixel(element.center, canvas)
    radius_px = _to_pixel_scalar(element.radius, canvas)
    fill_color = element.style.fill_color or element.style.stroke_color
    draw.ellipse([cx - radius_px, cy - radius_px, cx + radius_px, cy + radius_px], fill=fill_color)


def _ellipse_bbox(
    center: DiagramPoint, radius_x: float, radius_y: float, canvas: DiagramCanvas
) -> list[int]:
    cx, cy = _to_pixel(center, canvas)
    rx, ry = _to_pixel_x(radius_x, canvas), _to_pixel_y(radius_y, canvas)
    return [cx - rx, cy - ry, cx + rx, cy + ry]
