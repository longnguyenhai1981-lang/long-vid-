"""Deterministic diagram specification contracts (Phase 25).

Pure, dependency-free pydantic contracts describing a diagram
declaratively: an explicit canvas plus an explicit, ordered list of
elements, each given in normalized [0.0, 1.0] coordinates. No rendering
logic lives here -- app/diagram_renderer/renderer.py (Pillow-based) is the
only module that turns a DiagramSpec into pixels. This module only
defines WHAT to draw, never HOW or WHERE on screen it ends up, and never
performs any layout: every coordinate is authored explicitly, there is no
auto-placement, collision resolution, or LLM-derived positioning.

DiagramSpec lives here, in app/models/, rather than in the renderer-layer
app/diagram_renderer/ package, because VisualBeat.diagram_spec (see
app/models/visual.py) must be able to reference it without VisualBeat
depending on a Pillow-based rendering package. This mirrors
app/models/ti_assets.py's TiState, which app/models/visual.py also
references directly while the Pillow-based app/ti_compositor/ package
consumes it separately -- neither renderer-layer package is imported by
app/models/.
"""

from __future__ import annotations

from enum import Enum
from typing import Annotated, Literal, Union

from pydantic import Field, model_validator

from app.models.common import MotilyModel, non_blank

_NORMALIZED_MIN = 0.0
_NORMALIZED_MAX = 1.0


def _check_normalized(value: float, field_name: str) -> float:
    if not (_NORMALIZED_MIN <= value <= _NORMALIZED_MAX):
        raise ValueError(f"{field_name} must be within [0.0, 1.0]; got {value}")
    return value


class DiagramElementType(str, Enum):
    """The fixed diagram primitive vocabulary (Phase 25) -- no other
    element type. Deliberately small: enough for simple physics/mechanism
    diagrams (forces, angles, simple shapes, labels), not a general
    drawing/SVG/charting engine."""

    LINE = "LINE"
    ARROW = "ARROW"
    RECTANGLE = "RECTANGLE"
    ELLIPSE = "ELLIPSE"
    POLYLINE = "POLYLINE"
    TEXT_LABEL = "TEXT_LABEL"
    ARC = "ARC"
    DOT = "DOT"


class LineStyle(str, Enum):
    """A deliberately small line vocabulary -- no dash-pattern
    customization beyond solid/dashed."""

    SOLID = "SOLID"
    DASHED = "DASHED"


class TextAlign(str, Enum):
    LEFT = "LEFT"
    CENTER = "CENTER"
    RIGHT = "RIGHT"


class DiagramPoint(MotilyModel):
    """A position in normalized [0.0, 1.0] diagram space -- resolution
    independent. DiagramRenderer converts this to an exact pixel position
    using DiagramCanvas.width/height; nothing here knows about pixels."""

    x: float
    y: float

    @model_validator(mode="after")
    def _check_bounds(self) -> "DiagramPoint":
        _check_normalized(self.x, "x")
        _check_normalized(self.y, "y")
        return self


class DiagramStyle(MotilyModel):
    """The minimum explicit styling Phase 25 supports -- no gradients, no
    textures, no external fonts, no per-element font family. Colors are
    plain strings (a "#RRGGBB" hex value or any Pillow-recognized color
    name) rather than a dedicated color model, since Pillow itself accepts
    either directly and a wrapper model would add no validation Pillow
    doesn't already need to accept at render time."""

    stroke_color: str = "#000000"
    stroke_width: int = 2
    fill_color: str | None = None
    line_style: LineStyle = LineStyle.SOLID
    font_size: int = 16
    text_color: str = "#000000"
    arrowhead_size: int = 10

    @model_validator(mode="after")
    def _check_invariants(self) -> "DiagramStyle":
        if self.stroke_width <= 0:
            raise ValueError("stroke_width must be > 0")
        if self.font_size <= 0:
            raise ValueError("font_size must be > 0")
        if self.arrowhead_size <= 0:
            raise ValueError("arrowhead_size must be > 0")
        return self


def _check_relative_size(value: float, field_name: str) -> float:
    """Radii/sizes share DiagramPoint's normalized space: a fraction of
    the relevant canvas dimension, in (0.0, 1.0]. Zero or negative has no
    rendering meaning; the exclusive lower bound rules out a degenerate
    zero-size shape."""
    if not (0.0 < value <= _NORMALIZED_MAX):
        raise ValueError(f"{field_name} must be within (0.0, 1.0]; got {value}")
    return value


class DiagramLine(MotilyModel):
    type: Literal[DiagramElementType.LINE] = DiagramElementType.LINE
    element_id: str | None = None
    start: DiagramPoint
    end: DiagramPoint
    style: DiagramStyle = Field(default_factory=DiagramStyle)

    @model_validator(mode="after")
    def _check_invariants(self) -> "DiagramLine":
        if self.start == self.end:
            raise ValueError("LINE start and end must differ (zero-length line is disallowed)")
        return self


class DiagramArrow(MotilyModel):
    type: Literal[DiagramElementType.ARROW] = DiagramElementType.ARROW
    element_id: str | None = None
    start: DiagramPoint
    end: DiagramPoint
    style: DiagramStyle = Field(default_factory=DiagramStyle)

    @model_validator(mode="after")
    def _check_invariants(self) -> "DiagramArrow":
        if self.start == self.end:
            raise ValueError("ARROW start and end must differ (zero-length arrow is disallowed)")
        return self


class DiagramRectangle(MotilyModel):
    type: Literal[DiagramElementType.RECTANGLE] = DiagramElementType.RECTANGLE
    element_id: str | None = None
    top_left: DiagramPoint
    bottom_right: DiagramPoint
    style: DiagramStyle = Field(default_factory=DiagramStyle)

    @model_validator(mode="after")
    def _check_invariants(self) -> "DiagramRectangle":
        if self.bottom_right.x <= self.top_left.x or self.bottom_right.y <= self.top_left.y:
            raise ValueError(
                "RECTANGLE bottom_right must be strictly greater than top_left in both axes "
                "(zero-area rectangle is disallowed)"
            )
        return self


class DiagramEllipse(MotilyModel):
    type: Literal[DiagramElementType.ELLIPSE] = DiagramElementType.ELLIPSE
    element_id: str | None = None
    center: DiagramPoint
    radius_x: float
    radius_y: float
    style: DiagramStyle = Field(default_factory=DiagramStyle)

    @model_validator(mode="after")
    def _check_invariants(self) -> "DiagramEllipse":
        _check_relative_size(self.radius_x, "radius_x")
        _check_relative_size(self.radius_y, "radius_y")
        return self


class DiagramPolyline(MotilyModel):
    type: Literal[DiagramElementType.POLYLINE] = DiagramElementType.POLYLINE
    element_id: str | None = None
    points: list[DiagramPoint] = Field(min_length=2)
    closed: bool = False
    style: DiagramStyle = Field(default_factory=DiagramStyle)


class DiagramTextLabel(MotilyModel):
    type: Literal[DiagramElementType.TEXT_LABEL] = DiagramElementType.TEXT_LABEL
    element_id: str | None = None
    position: DiagramPoint
    text: str
    align: TextAlign = TextAlign.LEFT
    style: DiagramStyle = Field(default_factory=DiagramStyle)

    @model_validator(mode="after")
    def _check_invariants(self) -> "DiagramTextLabel":
        non_blank(self.text, "text")
        return self


class DiagramArc(MotilyModel):
    """An elliptical arc -- needed for angle/relationship diagrams (Phase
    25 requirement). Angles are degrees, measured the same way Pillow's
    own ImageDraw.arc/pieslice measure them (0 degrees at 3 o'clock,
    increasing clockwise in image space), swept from start to end."""

    type: Literal[DiagramElementType.ARC] = DiagramElementType.ARC
    element_id: str | None = None
    center: DiagramPoint
    radius_x: float
    radius_y: float
    start_angle_degrees: float
    end_angle_degrees: float
    style: DiagramStyle = Field(default_factory=DiagramStyle)

    @model_validator(mode="after")
    def _check_invariants(self) -> "DiagramArc":
        _check_relative_size(self.radius_x, "radius_x")
        _check_relative_size(self.radius_y, "radius_y")
        sweep = self.end_angle_degrees - self.start_angle_degrees
        if sweep == 0:
            raise ValueError("ARC start_angle_degrees and end_angle_degrees must differ")
        if abs(sweep) > 360:
            raise ValueError(
                f"ARC sweep must not exceed 360 degrees; got {sweep} "
                f"(start={self.start_angle_degrees}, end={self.end_angle_degrees})"
            )
        return self


class DiagramDot(MotilyModel):
    """A small filled circle -- optional per Phase 25's suggestion, kept
    because it materially simplifies physics diagrams needing a point mass
    or pivot marker without authoring a full ELLIPSE."""

    type: Literal[DiagramElementType.DOT] = DiagramElementType.DOT
    element_id: str | None = None
    center: DiagramPoint
    radius: float = 0.01
    style: DiagramStyle = Field(default_factory=DiagramStyle)

    @model_validator(mode="after")
    def _check_invariants(self) -> "DiagramDot":
        _check_relative_size(self.radius, "radius")
        return self


DiagramElement = Annotated[
    Union[
        DiagramLine,
        DiagramArrow,
        DiagramRectangle,
        DiagramEllipse,
        DiagramPolyline,
        DiagramTextLabel,
        DiagramArc,
        DiagramDot,
    ],
    Field(discriminator="type"),
]
"""A discriminated union keyed on `type` -- pydantic itself rejects any
`type` value outside DiagramElementType's members (Phase 25's "unsupported
element subtype" validation rule) and picks the matching concrete model
for every other field, with no manual dispatch needed at parse time."""


class DiagramCanvas(MotilyModel):
    """Explicit pixel canvas size and background policy (Phase 25
    requirement #5). background_color=None means a transparent PNG
    (RGBA, alpha=0 before any element is drawn); any string value paints
    that color as a fully opaque background first. See
    app/diagram_renderer/renderer.py's module docstring for exactly how
    DiagramRenderer applies this -- there is no third option."""

    width: int
    height: int
    background_color: str | None = "#FFFFFF"

    @model_validator(mode="after")
    def _check_invariants(self) -> "DiagramCanvas":
        if self.width <= 0 or self.height <= 0:
            raise ValueError(f"DiagramCanvas width/height must be > 0; got {self.width}x{self.height}")
        return self


class DiagramSpec(MotilyModel):
    """A complete, self-contained deterministic diagram: one canvas, one
    ordered element list (drawn in list order -- later elements paint over
    earlier ones, exactly like Pillow's own draw order). This is the type
    VisualBeat.diagram_spec (app/models/visual.py) carries for a DIAGRAM
    beat."""

    canvas: DiagramCanvas
    elements: list[DiagramElement] = Field(min_length=1)

    @model_validator(mode="after")
    def _check_invariants(self) -> "DiagramSpec":
        element_ids = [el.element_id for el in self.elements if el.element_id is not None]
        seen: set[str] = set()
        duplicates: set[str] = set()
        for element_id in element_ids:
            if element_id in seen:
                duplicates.add(element_id)
            seen.add(element_id)
        if duplicates:
            raise ValueError(f"DiagramSpec has duplicate element_id values: {sorted(duplicates)}")
        return self
