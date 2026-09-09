"""Phase 26 focused tests: VisualLayerCompositor
(app/layer_compositor/compositor.py).

Exercises the actual Pillow-based composition backend directly -- no
VisualRenderer, no VisualProvider, no LLM, nothing network-related.
"""

from __future__ import annotations

from io import BytesIO
from pathlib import Path

import pytest
from PIL import Image

from app.layer_compositor.compositor import VisualLayerCompositor
from app.layer_compositor.errors import LayerCompositionBackgroundError, LayerCompositionBoundsError
from app.layer_compositor.models import (
    BackgroundLayer,
    LayerAnchor,
    LayerCompositionSpec,
    LayerScale,
    OverlayLayer,
)

BACKGROUND_SIZE = (400, 300)
BACKGROUND_COLOR = (10, 20, 200)


def _write_background(path: Path, size=BACKGROUND_SIZE, color=BACKGROUND_COLOR, fmt="PNG") -> Path:
    Image.new("RGB", size, color).save(path, format=fmt)
    return path


def _write_transparent_overlay(path: Path, size=(100, 50), opaque_color=(255, 0, 0, 255)) -> Path:
    """A fully transparent image with a smaller fully opaque core -- lets a
    test assert both that the opaque core covers the background and that
    the transparent border reveals the background color, untouched."""
    image = Image.new("RGBA", size, (0, 0, 0, 0))
    inset_x, inset_y = size[0] // 4, size[1] // 4
    core = Image.new("RGBA", (size[0] - 2 * inset_x, size[1] - 2 * inset_y), opaque_color)
    image.paste(core, (inset_x, inset_y))
    image.save(path, format="PNG")
    return path


def _write_opaque_jpeg_overlay(path: Path, size=(80, 80), color=(0, 200, 0)) -> Path:
    Image.new("RGB", size, color).save(path, format="JPEG")
    return path


# ---------------------------------------------------------------------------
# Background-only composition
# ---------------------------------------------------------------------------


def test_background_only_composition(tmp_path):
    background_path = _write_background(tmp_path / "bg.png")
    spec = LayerCompositionSpec(layers=[BackgroundLayer(id="bg", source_path=background_path)], output_path=tmp_path / "out.png")

    result = VisualLayerCompositor().compose(spec)

    assert result.canvas_width, result.canvas_height == BACKGROUND_SIZE
    assert (tmp_path / "out.png").is_file()
    assert len(result.layers) == 1
    assert result.layers[0].layer_id == "bg"
    assert result.layers[0].clamped is False


def test_output_dimensions_equal_background_dimensions(tmp_path):
    background_path = _write_background(tmp_path / "bg.png", size=(640, 480))
    spec = LayerCompositionSpec(layers=[BackgroundLayer(id="bg", source_path=background_path)], output_path=tmp_path / "out.png")

    result = VisualLayerCompositor().compose(spec)

    assert result.canvas_width == 640
    assert result.canvas_height == 480
    with Image.open(tmp_path / "out.png") as image:
        assert image.size == (640, 480)


# ---------------------------------------------------------------------------
# Single / multiple overlays, z-order, stable ties
# ---------------------------------------------------------------------------


def test_one_transparent_overlay_composes(tmp_path):
    background_path = _write_background(tmp_path / "bg.png")
    overlay_path = _write_transparent_overlay(tmp_path / "ov.png")
    spec = LayerCompositionSpec(
        layers=[
            BackgroundLayer(id="bg", source_path=background_path),
            OverlayLayer(id="ov", source_path=overlay_path, anchor=LayerAnchor.CENTER),
        ],
        output_path=tmp_path / "out.png",
    )

    result = VisualLayerCompositor().compose(spec)

    assert len(result.layers) == 2
    ov_geometry = next(g for g in result.layers if g.layer_id == "ov")
    assert ov_geometry.width == 100 and ov_geometry.height == 50


def test_multiple_overlays_all_present_in_geometry(tmp_path):
    background_path = _write_background(tmp_path / "bg.png")
    overlay1 = _write_transparent_overlay(tmp_path / "ov1.png")
    overlay2 = _write_opaque_jpeg_overlay(tmp_path / "ov2.jpg")
    spec = LayerCompositionSpec(
        layers=[
            BackgroundLayer(id="bg", source_path=background_path),
            OverlayLayer(id="ov1", source_path=overlay1, anchor=LayerAnchor.TOP_LEFT),
            OverlayLayer(id="ov2", source_path=overlay2, anchor=LayerAnchor.BOTTOM_RIGHT),
        ],
        output_path=tmp_path / "out.png",
    )

    result = VisualLayerCompositor().compose(spec)

    layer_ids = {g.layer_id for g in result.layers}
    assert layer_ids == {"bg", "ov1", "ov2"}


def test_correct_z_order_higher_z_index_drawn_on_top(tmp_path):
    background_path = _write_background(tmp_path / "bg.png")
    # Two overlapping full-canvas-ish overlays at CENTER: the higher
    # z_index one should end up visually on top.
    red_path = tmp_path / "red.png"
    Image.new("RGBA", (100, 100), (255, 0, 0, 255)).save(red_path)
    blue_path = tmp_path / "blue.png"
    Image.new("RGBA", (100, 100), (0, 0, 255, 255)).save(blue_path)

    spec = LayerCompositionSpec(
        layers=[
            BackgroundLayer(id="bg", source_path=background_path),
            OverlayLayer(id="red", source_path=red_path, anchor=LayerAnchor.CENTER, z_index=1),
            OverlayLayer(id="blue", source_path=blue_path, anchor=LayerAnchor.CENTER, z_index=2),
        ],
        output_path=tmp_path / "out.png",
    )
    VisualLayerCompositor().compose(spec)

    with Image.open(tmp_path / "out.png") as image:
        center_pixel = image.convert("RGB").getpixel((200, 150))
    assert center_pixel == (0, 0, 255)  # blue (z_index=2) on top of red (z_index=1)


def test_stable_tie_behavior_authored_order_wins_on_equal_z_index(tmp_path):
    background_path = _write_background(tmp_path / "bg.png")
    red_path = tmp_path / "red.png"
    Image.new("RGBA", (100, 100), (255, 0, 0, 255)).save(red_path)
    blue_path = tmp_path / "blue.png"
    Image.new("RGBA", (100, 100), (0, 0, 255, 255)).save(blue_path)

    # Equal z_index -- blue authored AFTER red, so blue should still end up
    # on top (stable sort preserves authored order for ties).
    spec = LayerCompositionSpec(
        layers=[
            BackgroundLayer(id="bg", source_path=background_path),
            OverlayLayer(id="red", source_path=red_path, anchor=LayerAnchor.CENTER, z_index=0),
            OverlayLayer(id="blue", source_path=blue_path, anchor=LayerAnchor.CENTER, z_index=0),
        ],
        output_path=tmp_path / "out.png",
    )
    VisualLayerCompositor().compose(spec)

    with Image.open(tmp_path / "out.png") as image:
        center_pixel = image.convert("RGB").getpixel((200, 150))
    assert center_pixel == (0, 0, 255)


# ---------------------------------------------------------------------------
# All 9 anchors
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("anchor", list(LayerAnchor))
def test_all_nine_anchors_place_without_error(tmp_path, anchor):
    background_path = _write_background(tmp_path / "bg.png")
    overlay_path = _write_transparent_overlay(tmp_path / "ov.png", size=(40, 40))
    spec = LayerCompositionSpec(
        layers=[
            BackgroundLayer(id="bg", source_path=background_path),
            OverlayLayer(id="ov", source_path=overlay_path, anchor=anchor),
        ],
        output_path=tmp_path / f"out_{anchor.value}.png",
    )

    result = VisualLayerCompositor().compose(spec)
    geometry = result.layers[1]
    assert 0 <= geometry.x <= BACKGROUND_SIZE[0] - geometry.width
    assert 0 <= geometry.y <= BACKGROUND_SIZE[1] - geometry.height


def test_top_left_anchor_places_near_origin(tmp_path):
    background_path = _write_background(tmp_path / "bg.png")
    overlay_path = _write_transparent_overlay(tmp_path / "ov.png", size=(40, 40))
    spec = LayerCompositionSpec(
        layers=[
            BackgroundLayer(id="bg", source_path=background_path),
            OverlayLayer(id="ov", source_path=overlay_path, anchor=LayerAnchor.TOP_LEFT),
        ],
        output_path=tmp_path / "out.png",
    )
    result = VisualLayerCompositor().compose(spec)
    geometry = result.layers[1]
    assert geometry.x == 0 and geometry.y == 0


def test_bottom_right_anchor_places_near_far_corner(tmp_path):
    background_path = _write_background(tmp_path / "bg.png")
    overlay_path = _write_transparent_overlay(tmp_path / "ov.png", size=(40, 40))
    spec = LayerCompositionSpec(
        layers=[
            BackgroundLayer(id="bg", source_path=background_path),
            OverlayLayer(id="ov", source_path=overlay_path, anchor=LayerAnchor.BOTTOM_RIGHT),
        ],
        output_path=tmp_path / "out.png",
    )
    result = VisualLayerCompositor().compose(spec)
    geometry = result.layers[1]
    assert geometry.x == BACKGROUND_SIZE[0] - 40
    assert geometry.y == BACKGROUND_SIZE[1] - 40


# ---------------------------------------------------------------------------
# Offsets / margins
# ---------------------------------------------------------------------------


def test_margin_applied_from_edge(tmp_path):
    background_path = _write_background(tmp_path / "bg.png")
    overlay_path = _write_transparent_overlay(tmp_path / "ov.png", size=(40, 40))
    spec = LayerCompositionSpec(
        layers=[
            BackgroundLayer(id="bg", source_path=background_path),
            OverlayLayer(id="ov", source_path=overlay_path, anchor=LayerAnchor.TOP_LEFT, margin=15),
        ],
        output_path=tmp_path / "out.png",
    )
    result = VisualLayerCompositor().compose(spec)
    geometry = result.layers[1]
    assert geometry.x == 15 and geometry.y == 15


def test_offset_applied_on_top_of_anchor(tmp_path):
    background_path = _write_background(tmp_path / "bg.png")
    overlay_path = _write_transparent_overlay(tmp_path / "ov.png", size=(40, 40))
    spec = LayerCompositionSpec(
        layers=[
            BackgroundLayer(id="bg", source_path=background_path),
            OverlayLayer(id="ov", source_path=overlay_path, anchor=LayerAnchor.TOP_LEFT, offset_x=5, offset_y=7),
        ],
        output_path=tmp_path / "out.png",
    )
    result = VisualLayerCompositor().compose(spec)
    geometry = result.layers[1]
    assert geometry.x == 5 and geometry.y == 7


# ---------------------------------------------------------------------------
# Scaling: native size, relative_height, relative_width, aspect ratio
# ---------------------------------------------------------------------------


def test_native_size_overlay_uses_source_dimensions(tmp_path):
    background_path = _write_background(tmp_path / "bg.png")
    overlay_path = _write_transparent_overlay(tmp_path / "ov.png", size=(60, 30))
    spec = LayerCompositionSpec(
        layers=[
            BackgroundLayer(id="bg", source_path=background_path),
            OverlayLayer(id="ov", source_path=overlay_path),
        ],
        output_path=tmp_path / "out.png",
    )
    result = VisualLayerCompositor().compose(spec)
    geometry = result.layers[1]
    assert (geometry.width, geometry.height) == (60, 30)


def test_relative_height_scale(tmp_path):
    background_path = _write_background(tmp_path / "bg.png")  # 400x300
    overlay_path = _write_transparent_overlay(tmp_path / "ov.png", size=(200, 100))  # 2:1 aspect
    spec = LayerCompositionSpec(
        layers=[
            BackgroundLayer(id="bg", source_path=background_path),
            OverlayLayer(id="ov", source_path=overlay_path, scale=LayerScale(relative_height=0.5)),
        ],
        output_path=tmp_path / "out.png",
    )
    result = VisualLayerCompositor().compose(spec)
    geometry = result.layers[1]
    assert geometry.height == 150  # 0.5 * 300
    assert geometry.width == 300  # aspect preserved: 200/100 * 150


def test_relative_width_scale(tmp_path):
    background_path = _write_background(tmp_path / "bg.png")  # 400x300
    overlay_path = _write_transparent_overlay(tmp_path / "ov.png", size=(200, 100))  # 2:1 aspect
    spec = LayerCompositionSpec(
        layers=[
            BackgroundLayer(id="bg", source_path=background_path),
            OverlayLayer(id="ov", source_path=overlay_path, scale=LayerScale(relative_width=0.5)),
        ],
        output_path=tmp_path / "out.png",
    )
    result = VisualLayerCompositor().compose(spec)
    geometry = result.layers[1]
    assert geometry.width == 200  # 0.5 * 400
    assert geometry.height == 100  # aspect preserved: 100/200 * 200


def test_aspect_ratio_preserved_never_distorted(tmp_path):
    background_path = _write_background(tmp_path / "bg.png")
    overlay_path = _write_transparent_overlay(tmp_path / "ov.png", size=(150, 50))  # 3:1
    spec = LayerCompositionSpec(
        layers=[
            BackgroundLayer(id="bg", source_path=background_path),
            OverlayLayer(id="ov", source_path=overlay_path, scale=LayerScale(relative_height=0.2)),
        ],
        output_path=tmp_path / "out.png",
    )
    result = VisualLayerCompositor().compose(spec)
    geometry = result.layers[1]
    assert geometry.width / geometry.height == pytest.approx(150 / 50, rel=0.05)


# ---------------------------------------------------------------------------
# Alpha correctness
# ---------------------------------------------------------------------------


def test_alpha_correctness_transparent_border_shows_background(tmp_path):
    background_path = _write_background(tmp_path / "bg.png")
    overlay_path = _write_transparent_overlay(tmp_path / "ov.png", size=(100, 100), opaque_color=(255, 0, 0, 255))
    spec = LayerCompositionSpec(
        layers=[
            BackgroundLayer(id="bg", source_path=background_path),
            OverlayLayer(id="ov", source_path=overlay_path, anchor=LayerAnchor.TOP_LEFT),
        ],
        output_path=tmp_path / "out.png",
    )
    VisualLayerCompositor().compose(spec)

    with Image.open(tmp_path / "out.png") as image:
        rgb = image.convert("RGB")
        # Corner of the overlay's bounding box is in the transparent border
        # -- background color must show through untouched.
        corner_pixel = rgb.getpixel((2, 2))
        # Center of the overlay is the opaque red core.
        center_pixel = rgb.getpixel((50, 50))

    assert corner_pixel == BACKGROUND_COLOR
    assert center_pixel == (255, 0, 0)


def test_opaque_jpeg_overlay_fully_covers_background(tmp_path):
    background_path = _write_background(tmp_path / "bg.png")
    overlay_path = _write_opaque_jpeg_overlay(tmp_path / "ov.jpg", size=(50, 50), color=(0, 200, 0))
    spec = LayerCompositionSpec(
        layers=[
            BackgroundLayer(id="bg", source_path=background_path),
            OverlayLayer(id="ov", source_path=overlay_path, anchor=LayerAnchor.TOP_LEFT),
        ],
        output_path=tmp_path / "out.png",
    )
    VisualLayerCompositor().compose(spec)

    with Image.open(tmp_path / "out.png") as image:
        pixel = image.convert("RGB").getpixel((25, 25))
    assert pixel == (0, 200, 0)


# ---------------------------------------------------------------------------
# Bounds / clamping
# ---------------------------------------------------------------------------


def test_oversized_overlay_fails_explicitly(tmp_path):
    background_path = _write_background(tmp_path / "bg.png", size=(100, 100))
    overlay_path = _write_transparent_overlay(tmp_path / "ov.png", size=(200, 50))
    spec = LayerCompositionSpec(
        layers=[
            BackgroundLayer(id="bg", source_path=background_path),
            # relative_height=1.0 on a 200x50 (4:1) overlay against a
            # 100x100 canvas -> scaled width 400 > canvas width 100.
            OverlayLayer(id="ov", source_path=overlay_path, scale=LayerScale(relative_height=1.0)),
        ],
        output_path=tmp_path / "out.png",
    )

    with pytest.raises(LayerCompositionBoundsError):
        VisualLayerCompositor().compose(spec)


def test_clamp_behavior_reports_clamped_true(tmp_path):
    background_path = _write_background(tmp_path / "bg.png", size=(100, 100))
    overlay_path = _write_transparent_overlay(tmp_path / "ov.png", size=(40, 40))
    spec = LayerCompositionSpec(
        layers=[
            BackgroundLayer(id="bg", source_path=background_path),
            # margin pushes the requested position out-of-frame; clamp
            # should pull it back in and report clamped=True.
            OverlayLayer(id="ov", source_path=overlay_path, anchor=LayerAnchor.TOP_LEFT, offset_x=-100, offset_y=-100),
        ],
        output_path=tmp_path / "out.png",
    )
    result = VisualLayerCompositor().compose(spec)
    geometry = result.layers[1]
    assert geometry.clamped is True
    assert geometry.x == 0 and geometry.y == 0
    assert "ov" in result.clamped_layer_ids


def test_no_clamp_when_position_already_in_bounds(tmp_path):
    background_path = _write_background(tmp_path / "bg.png")
    overlay_path = _write_transparent_overlay(tmp_path / "ov.png", size=(40, 40))
    spec = LayerCompositionSpec(
        layers=[
            BackgroundLayer(id="bg", source_path=background_path),
            OverlayLayer(id="ov", source_path=overlay_path, anchor=LayerAnchor.CENTER),
        ],
        output_path=tmp_path / "out.png",
    )
    result = VisualLayerCompositor().compose(spec)
    geometry = result.layers[1]
    assert geometry.clamped is False
    assert result.clamped_layer_ids == []


# ---------------------------------------------------------------------------
# PNG/JPG backgrounds, missing/corrupt sources
# ---------------------------------------------------------------------------


def test_jpg_background_accepted(tmp_path):
    background_path = _write_background(tmp_path / "bg.jpg", fmt="JPEG")
    spec = LayerCompositionSpec(layers=[BackgroundLayer(id="bg", source_path=background_path)], output_path=tmp_path / "out.png")
    result = VisualLayerCompositor().compose(spec)
    assert result.canvas_width == BACKGROUND_SIZE[0]


def test_png_background_accepted(tmp_path):
    background_path = _write_background(tmp_path / "bg.png", fmt="PNG")
    spec = LayerCompositionSpec(layers=[BackgroundLayer(id="bg", source_path=background_path)], output_path=tmp_path / "out.png")
    result = VisualLayerCompositor().compose(spec)
    assert result.canvas_width == BACKGROUND_SIZE[0]


def test_missing_background_file_fails_explicitly(tmp_path):
    spec = LayerCompositionSpec(
        layers=[BackgroundLayer(id="bg", source_path=tmp_path / "missing.png")], output_path=tmp_path / "out.png"
    )
    with pytest.raises(LayerCompositionBackgroundError):
        VisualLayerCompositor().compose(spec)


def test_corrupt_background_file_fails_explicitly(tmp_path):
    corrupt_path = tmp_path / "corrupt.png"
    corrupt_path.write_bytes(b"not a real png")
    spec = LayerCompositionSpec(layers=[BackgroundLayer(id="bg", source_path=corrupt_path)], output_path=tmp_path / "out.png")
    with pytest.raises(LayerCompositionBackgroundError):
        VisualLayerCompositor().compose(spec)


# ---------------------------------------------------------------------------
# Determinism
# ---------------------------------------------------------------------------


def test_deterministic_repeated_output(tmp_path):
    background_path = _write_background(tmp_path / "bg.png")
    overlay_path = _write_transparent_overlay(tmp_path / "ov.png")
    spec = LayerCompositionSpec(
        layers=[
            BackgroundLayer(id="bg", source_path=background_path),
            OverlayLayer(id="ov", source_path=overlay_path, anchor=LayerAnchor.BOTTOM_RIGHT, scale=LayerScale(relative_height=0.3)),
        ],
        output_path=tmp_path / "first.png",
    )
    compositor = VisualLayerCompositor()
    compositor.compose(spec)
    first_bytes = (tmp_path / "first.png").read_bytes()

    spec_second = spec.model_copy(update={"output_path": tmp_path / "second.png"})
    compositor.compose(spec_second)
    second_bytes = (tmp_path / "second.png").read_bytes()

    assert first_bytes == second_bytes
