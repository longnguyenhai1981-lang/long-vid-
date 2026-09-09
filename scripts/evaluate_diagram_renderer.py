"""Manual visual-evaluation utility for the Phase 25 deterministic
DiagramRenderer.

Renders three small, illustrative physics/mechanism diagrams directly
through the real DiagramRenderer path (app/diagram_renderer/renderer.py)
-- no VisualRenderer, no VisualProvider, no AI image generation, no LLM
layout. Every coordinate below is authored by hand in normalized [0,1]
space; nothing here infers a layout automatically.

This is a manual viewing aid only, exactly like scripts/evaluate_ti_
compositor.py (Phase 22):

- No automated aesthetic/quality scoring, no LLM judge.
- No live image API call of any kind.
- These are evaluation artifacts, not production-pretty deliverables --
  they exist to prove deterministic rendering and readable labeling for
  line/arrow/text/arc/circle/rectangle elements together.

Usage:

    python scripts/evaluate_diagram_renderer.py
    python scripts/evaluate_diagram_renderer.py --out-dir data/diagram_renderer_evaluation_2
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

from app.diagram_renderer.errors import DiagramRenderError
from app.diagram_renderer.renderer import DiagramRenderer
from app.models.diagram import (
    DiagramArc,
    DiagramArrow,
    DiagramCanvas,
    DiagramDot,
    DiagramEllipse,
    DiagramLine,
    DiagramPoint,
    DiagramRectangle,
    DiagramSpec,
    DiagramStyle,
    DiagramTextLabel,
    LineStyle,
    TextAlign,
)

def _P(x: float, y: float) -> DiagramPoint:
    return DiagramPoint(x=x, y=y)


def _force_block_spec() -> DiagramSpec:
    """A block on a line with three labeled force arrows: gravity (down),
    the normal force (up), and an applied pull (right) -- the canonical
    free-body-diagram shape."""
    block_top_left = _P(x=0.35, y=0.40)
    block_bottom_right = _P(x=0.60, y=0.60)
    ground_y = 0.60

    return DiagramSpec(
        canvas=DiagramCanvas(width=760, height=480, background_color="#FFFFFF"),
        elements=[
            DiagramLine(element_id="ground", start=_P(0.05, ground_y), end=_P(0.95, ground_y)),
            DiagramRectangle(
                element_id="block",
                top_left=block_top_left,
                bottom_right=block_bottom_right,
                style=DiagramStyle(stroke_width=3, fill_color="#DDEBFF"),
            ),
            DiagramTextLabel(
                element_id="block_label",
                position=_P(0.475, 0.50),
                text="m",
                align=TextAlign.CENTER,
                style=DiagramStyle(font_size=20),
            ),
            # Gravity: straight down from the block's center.
            DiagramArrow(
                element_id="gravity_arrow",
                start=_P(0.475, 0.60),
                end=_P(0.475, 0.80),
                style=DiagramStyle(stroke_color="#CC0000", stroke_width=3, arrowhead_size=12),
            ),
            DiagramTextLabel(
                element_id="gravity_label",
                position=_P(0.50, 0.82),
                text="Fg (gravity)",
                style=DiagramStyle(text_color="#CC0000", font_size=16),
            ),
            # Normal force: straight up from the block's base.
            DiagramArrow(
                element_id="normal_arrow",
                start=_P(0.475, 0.40),
                end=_P(0.475, 0.20),
                style=DiagramStyle(stroke_color="#006600", stroke_width=3, arrowhead_size=12),
            ),
            DiagramTextLabel(
                element_id="normal_label",
                position=_P(0.50, 0.16),
                text="N (normal)",
                style=DiagramStyle(text_color="#006600", font_size=16),
            ),
            # Applied pull: horizontal, off the block's right edge.
            DiagramArrow(
                element_id="applied_arrow",
                start=_P(0.60, 0.50),
                end=_P(0.90, 0.50),
                style=DiagramStyle(stroke_color="#0044CC", stroke_width=3, arrowhead_size=12),
            ),
            DiagramTextLabel(
                element_id="applied_label",
                position=_P(0.97, 0.50),
                text="F (applied)",
                align=TextAlign.RIGHT,
                style=DiagramStyle(text_color="#0044CC", font_size=16),
            ),
            DiagramTextLabel(
                element_id="title",
                position=_P(0.05, 0.06),
                text="FORCE_BLOCK: free-body diagram",
                style=DiagramStyle(font_size=18),
            ),
        ],
    )


def _sun_stick_shadow_spec() -> DiagramSpec:
    """A simplified stick standing on the ground, a sun with a few rays in
    the upper corner, and the shadow the stick casts along the ground --
    the classic "shadow-length" mechanism diagram."""
    ground_y = 0.75
    stick_base = _P(0.45, ground_y)
    stick_top = _P(0.45, 0.35)
    sun_center = _P(0.80, 0.18)
    sun_radius = 0.06
    shadow_end = _P(0.70, ground_y)

    ray_specs = [
        (_P(0.80, 0.06), _P(0.80, 0.02)),
        (_P(0.90, 0.10), _P(0.94, 0.06)),
        (_P(0.94, 0.18), _P(0.98, 0.18)),
        (_P(0.90, 0.26), _P(0.94, 0.30)),
        (_P(0.70, 0.10), _P(0.66, 0.06)),
    ]
    rays = [
        DiagramLine(element_id=f"ray_{i}", start=start, end=end, style=DiagramStyle(stroke_color="#CC8800"))
        for i, (start, end) in enumerate(ray_specs)
    ]

    return DiagramSpec(
        canvas=DiagramCanvas(width=640, height=480, background_color="#FFFFFF"),
        elements=[
            DiagramLine(element_id="ground", start=_P(0.05, ground_y), end=_P(0.95, ground_y), style=DiagramStyle(stroke_width=3)),
            DiagramEllipse(
                element_id="sun",
                center=sun_center,
                radius_x=sun_radius,
                radius_y=sun_radius,
                style=DiagramStyle(stroke_color="#CC8800", fill_color="#FFDD55", stroke_width=2),
            ),
            *rays,
            DiagramLine(element_id="stick", start=stick_base, end=stick_top, style=DiagramStyle(stroke_width=4)),
            DiagramDot(element_id="stick_top_dot", center=stick_top, radius=0.012, style=DiagramStyle(stroke_color="#000000")),
            # The sun's rays travel toward the stick's top, casting a
            # shadow on the far side, away from the sun.
            DiagramLine(
                element_id="shadow",
                start=stick_base,
                end=shadow_end,
                style=DiagramStyle(stroke_color="#888888", stroke_width=6, line_style=LineStyle.DASHED),
            ),
            DiagramLine(
                element_id="sunray_to_tip",
                start=sun_center,
                end=stick_top,
                style=DiagramStyle(stroke_color="#CC8800", line_style=LineStyle.DASHED, stroke_width=1),
            ),
            DiagramTextLabel(element_id="stick_label", position=_P(0.47, 0.55), text="stick"),
            DiagramTextLabel(element_id="shadow_label", position=_P(0.55, 0.78), text="shadow"),
            DiagramTextLabel(
                element_id="title",
                position=_P(0.05, 0.06),
                text="SUN_STICK_SHADOW: shadow-length mechanism",
                style=DiagramStyle(font_size=18),
            ),
        ],
    )


def _circle_angle_relation_spec() -> DiagramSpec:
    """A circle with two radii from its center forming an angle, and an
    arc marking that angle -- the canonical angle/relationship diagram."""
    import math

    center = _P(0.5, 0.55)
    radius = 0.30
    angle_a_degrees, angle_b_degrees = -20.0, 55.0

    def _point_on_circle(angle_degrees: float) -> DiagramPoint:
        angle_radians = math.radians(angle_degrees)
        return _P(
            x=center.x + radius * math.cos(angle_radians),
            y=center.y + radius * math.sin(angle_radians),
        )

    point_a = _point_on_circle(angle_a_degrees)
    point_b = _point_on_circle(angle_b_degrees)
    arc_radius = radius * 0.35
    label_angle_degrees = (angle_a_degrees + angle_b_degrees) / 2
    label_point = _point_on_circle(label_angle_degrees)
    label_point = _P(
        x=center.x + (label_point.x - center.x) * 0.55,
        y=center.y + (label_point.y - center.y) * 0.55,
    )

    return DiagramSpec(
        canvas=DiagramCanvas(width=480, height=480, background_color="#FFFFFF"),
        elements=[
            DiagramEllipse(
                element_id="circle",
                center=center,
                radius_x=radius,
                radius_y=radius,
                style=DiagramStyle(stroke_width=3),
            ),
            DiagramDot(element_id="center_dot", center=center, radius=0.010),
            DiagramLine(element_id="radius_a", start=center, end=point_a, style=DiagramStyle(stroke_width=2)),
            DiagramLine(element_id="radius_b", start=center, end=point_b, style=DiagramStyle(stroke_width=2)),
            DiagramArc(
                element_id="angle_arc",
                center=center,
                radius_x=arc_radius,
                radius_y=arc_radius,
                start_angle_degrees=angle_a_degrees,
                end_angle_degrees=angle_b_degrees,
                style=DiagramStyle(stroke_color="#CC0000", stroke_width=3),
            ),
            DiagramTextLabel(
                element_id="angle_label",
                position=label_point,
                text="theta",
                align=TextAlign.CENTER,
                style=DiagramStyle(text_color="#CC0000", font_size=18),
            ),
            DiagramTextLabel(element_id="point_a_label", position=point_a, text="A"),
            DiagramTextLabel(element_id="point_b_label", position=point_b, text="B"),
            DiagramTextLabel(
                element_id="title",
                position=_P(0.05, 0.06),
                text="CIRCLE_ANGLE_RELATION: angle between two radii",
                style=DiagramStyle(font_size=18),
            ),
        ],
    )


EVALUATION_CASES: list[tuple[str, str]] = [
    ("FORCE_BLOCK", "force_block.png"),
    ("SUN_STICK_SHADOW", "sun_stick_shadow.png"),
    ("CIRCLE_ANGLE_RELATION", "circle_angle_relation.png"),
]

_SPEC_BUILDERS = {
    "FORCE_BLOCK": _force_block_spec,
    "SUN_STICK_SHADOW": _sun_stick_shadow_spec,
    "CIRCLE_ANGLE_RELATION": _circle_angle_relation_spec,
}


def _parse_args(argv: list[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument(
        "--out-dir",
        type=Path,
        default=Path("data/diagram_renderer_evaluation"),
        help="Directory to write the 3 evaluation PNGs into",
    )
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    for stream in (sys.stdout, sys.stderr):
        if hasattr(stream, "reconfigure"):
            stream.reconfigure(encoding="utf-8")

    args = _parse_args(argv if argv is not None else sys.argv[1:])
    args.out_dir.mkdir(parents=True, exist_ok=True)
    renderer = DiagramRenderer()

    print(f"Output directory: {args.out_dir.resolve()}")
    print()

    results = []
    for name, filename in EVALUATION_CASES:
        spec = _SPEC_BUILDERS[name]()
        output_path = args.out_dir / filename
        try:
            result = renderer.render(spec, output_path)
        except DiagramRenderError as exc:
            print(f"  {name}: FAILED ({exc})")
            continue

        # Render again to a scratch path and compare bytes -- proves
        # determinism inline, not just by convention.
        scratch_path = args.out_dir / f"_determinism_check_{filename}"
        renderer.render(spec, scratch_path)
        deterministic = scratch_path.read_bytes() == output_path.read_bytes()
        scratch_path.unlink()

        print(
            f"  {name}: {result.output_path} ({result.width}x{result.height}, "
            f"deterministic={deterministic}, {len(spec.elements)} elements, "
            f"0 provider/API calls)"
        )
        results.append(result)

    print()
    print(f"Rendered {len(results)}/{len(EVALUATION_CASES)} evaluation diagram(s).")
    print("Look and decide manually -- this script does not rank or score visual quality.")
    return 0 if len(results) == len(EVALUATION_CASES) else 1


if __name__ == "__main__":
    raise SystemExit(main())
