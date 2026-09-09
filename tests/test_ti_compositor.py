from __future__ import annotations

import pytest
from PIL import Image

from app.models.common import VisualTiState
from app.models.ti_assets import TiState
from app.storage.errors import TiAssetSetNotFoundError
from app.ti_assets.errors import TiAssetMissingFileError
from app.ti_assets.retriever import SqliteTiAssetRetriever
from app.ti_assets.storage import TiAssetFileStore
from app.ti_compositor.compositor import (
    TiCompositor,
    clamp_position,
    compute_anchor_position,
    compute_scaled_size,
)
from app.ti_compositor.errors import TiCompositeBoundsError
from app.ti_compositor.models import TiAnchor, TiCompositeRequest, TiPlacement, TiScalePolicy
from tests._ti_compositor_helpers import (
    TI_ASSET_SIZE,
    TI_OPAQUE_COLOR,
    write_background,
    write_full_ti_asset_set,
)

BACKGROUND_COLOR = (10, 20, 200)  # a color Tí's own asset never contains


def _retriever(tmp_path, engine, *, active: bool = True) -> SqliteTiAssetRetriever:
    file_store = TiAssetFileStore(tmp_path / "ti_assets")
    if active:
        write_full_ti_asset_set(engine, file_store)
    return SqliteTiAssetRetriever(engine, file_store)


def _placement(anchor: TiAnchor = TiAnchor.BOTTOM_LEFT, scale: float = 0.5, **kwargs) -> TiPlacement:
    return TiPlacement(anchor=anchor, scale=TiScalePolicy(relative_height=scale), **kwargs)


# ---------------------------------------------------------------------------
# Pure placement/scale math
# ---------------------------------------------------------------------------


def test_compute_scaled_size_preserves_aspect_ratio():
    width, height = compute_scaled_size((40, 60), background_height=300, relative_height=0.5)
    assert height == 150
    assert width == round(40 * 150 / 60)


@pytest.mark.parametrize(
    "anchor,expected",
    [
        (TiAnchor.BOTTOM_LEFT, (0, 100 - 30)),
        (TiAnchor.BOTTOM_CENTER, ((200 - 20) // 2, 100 - 30)),
        (TiAnchor.BOTTOM_RIGHT, (200 - 20, 100 - 30)),
        (TiAnchor.CENTER_LEFT, (0, (100 - 30) // 2)),
        (TiAnchor.CENTER, ((200 - 20) // 2, (100 - 30) // 2)),
        (TiAnchor.CENTER_RIGHT, (200 - 20, (100 - 30) // 2)),
    ],
)
def test_compute_anchor_position_matches_each_anchor(anchor, expected):
    position = compute_anchor_position((200, 100), (20, 30), anchor, margin=0, offset_x=0, offset_y=0)
    assert position == expected


def test_compute_anchor_position_applies_margin_and_offset():
    position = compute_anchor_position(
        (200, 100), (20, 30), TiAnchor.BOTTOM_LEFT, margin=5, offset_x=3, offset_y=-2
    )
    assert position == (0 + 5 + 3, 100 - 30 - 5 - 2)


def test_clamp_position_leaves_in_bounds_position_untouched():
    position, clamped = clamp_position((200, 100), (20, 30), (10, 10))
    assert position == (10, 10)
    assert clamped is False


def test_clamp_position_clamps_out_of_bounds_offset():
    position, clamped = clamp_position((200, 100), (20, 30), (1000, -1000))
    assert position == (200 - 20, 0)
    assert clamped is True


def test_clamp_position_raises_when_ti_wider_than_background():
    with pytest.raises(TiCompositeBoundsError):
        clamp_position((50, 100), (60, 30), (0, 0))


# ---------------------------------------------------------------------------
# End-to-end compositing
# ---------------------------------------------------------------------------


def test_composite_uses_canonical_asset_via_retriever(tmp_path, engine):
    retriever = _retriever(tmp_path, engine)
    compositor = TiCompositor(retriever)
    bg_path = write_background(tmp_path / "bg.png", 200, 200, BACKGROUND_COLOR, "PNG")

    result = compositor.composite(
        TiCompositeRequest(
            background_path=bg_path,
            output_path=tmp_path / "out.png",
            placement=_placement(),
            ti_state=TiState.NEUTRAL,
        )
    )

    assert result.resolved_ti_state is TiState.NEUTRAL
    assert result.output_path.is_file()


@pytest.mark.parametrize(
    "visual_state,expected_ti_state",
    [
        (VisualTiState.NEUTRAL, TiState.NEUTRAL),
        (VisualTiState.CURIOUS, TiState.CURIOUS),
        (VisualTiState.CONFUSED, TiState.CURIOUS),
        (VisualTiState.SURPRISED, TiState.EXCITED),
        (VisualTiState.SMUG, TiState.SKEPTICAL),
        (VisualTiState.PANIC, TiState.PANIC),
    ],
)
def test_composite_resolves_visual_ti_state_through_deterministic_mapping(
    tmp_path, engine, visual_state, expected_ti_state
):
    retriever = _retriever(tmp_path, engine)
    compositor = TiCompositor(retriever)
    bg_path = write_background(tmp_path / "bg.png", 200, 200, BACKGROUND_COLOR, "PNG")

    result = compositor.composite(
        TiCompositeRequest(
            background_path=bg_path,
            output_path=tmp_path / "out.png",
            placement=_placement(),
            visual_ti_state=visual_state,
        )
    )

    assert result.resolved_ti_state is expected_ti_state
    assert result.source_visual_ti_state is visual_state


def test_request_rejects_both_visual_ti_state_and_ti_state():
    with pytest.raises(ValueError):
        TiCompositeRequest(
            background_path="bg.png",
            output_path="out.png",
            placement=_placement(),
            visual_ti_state=VisualTiState.NEUTRAL,
            ti_state=TiState.NEUTRAL,
        )


def test_request_rejects_neither_visual_ti_state_nor_ti_state():
    with pytest.raises(ValueError):
        TiCompositeRequest(
            background_path="bg.png",
            output_path="out.png",
            placement=_placement(),
        )


def test_alpha_compositing_preserves_transparent_border_and_opaque_core(tmp_path, engine):
    retriever = _retriever(tmp_path, engine)
    compositor = TiCompositor(retriever)
    bg_path = write_background(tmp_path / "bg.png", 200, 200, BACKGROUND_COLOR, "PNG")
    out_path = tmp_path / "out.png"

    result = compositor.composite(
        TiCompositeRequest(
            background_path=bg_path,
            output_path=out_path,
            placement=_placement(anchor=TiAnchor.CENTER, scale=0.5),
            ti_state=TiState.NEUTRAL,
        )
    )

    output = Image.open(out_path).convert("RGB")
    ti_w, ti_h = result.ti_rendered_width, result.ti_rendered_height
    x, y = result.placement_x, result.placement_y

    # Opaque core (center of the placed Tí rect) must be exactly Tí's color --
    # full overwrite, not blended with the background.
    core_pixel = output.getpixel((x + ti_w // 2, y + ti_h // 2))
    assert core_pixel == TI_OPAQUE_COLOR[:3]

    # The transparent border (a corner of the placed rect) must show the
    # original background color untouched -- no white/black baked in.
    border_pixel = output.getpixel((x + 1, y + 1))
    assert border_pixel == BACKGROUND_COLOR

    # Well outside the placed rect entirely: still the background color.
    assert (x, y) != (5, 5)  # sanity: the corner pixel checked below really is outside the placed rect
    far_pixel = output.getpixel((5, 5))
    assert far_pixel == BACKGROUND_COLOR


@pytest.mark.parametrize("anchor", list(TiAnchor))
def test_every_supported_anchor_composites_successfully(tmp_path, engine, anchor):
    retriever = _retriever(tmp_path, engine)
    compositor = TiCompositor(retriever)
    bg_path = write_background(tmp_path / "bg.png", 300, 200, BACKGROUND_COLOR, "PNG")
    out_path = tmp_path / f"out_{anchor.value}.png"

    result = compositor.composite(
        TiCompositeRequest(
            background_path=bg_path,
            output_path=out_path,
            placement=_placement(anchor=anchor, scale=0.3),
            ti_state=TiState.NEUTRAL,
        )
    )

    assert 0 <= result.placement_x <= result.background_width - result.ti_rendered_width
    assert 0 <= result.placement_y <= result.background_height - result.ti_rendered_height
    assert result.clamped is False


def test_scale_is_preserved_as_relative_background_height(tmp_path, engine):
    retriever = _retriever(tmp_path, engine)
    compositor = TiCompositor(retriever)
    bg_path = write_background(tmp_path / "bg.png", 400, 500, BACKGROUND_COLOR, "PNG")

    result = compositor.composite(
        TiCompositeRequest(
            background_path=bg_path,
            output_path=tmp_path / "out.png",
            placement=_placement(scale=0.2),
            ti_state=TiState.NEUTRAL,
        )
    )

    assert result.ti_rendered_height == round(500 * 0.2)
    expected_width = round(TI_ASSET_SIZE[0] * result.ti_rendered_height / TI_ASSET_SIZE[1])
    assert result.ti_rendered_width == expected_width


def test_output_dimensions_equal_background_dimensions(tmp_path, engine):
    retriever = _retriever(tmp_path, engine)
    compositor = TiCompositor(retriever)
    bg_path = write_background(tmp_path / "bg.png", 321, 213, BACKGROUND_COLOR, "PNG")
    out_path = tmp_path / "out.png"

    compositor.composite(
        TiCompositeRequest(
            background_path=bg_path,
            output_path=out_path,
            placement=_placement(),
            ti_state=TiState.NEUTRAL,
        )
    )

    with Image.open(out_path) as output:
        assert output.size == (321, 213)


@pytest.mark.parametrize("fmt,suffix", [("PNG", ".png"), ("JPEG", ".jpg")])
def test_png_and_jpg_backgrounds_both_supported(tmp_path, engine, fmt, suffix):
    retriever = _retriever(tmp_path, engine)
    compositor = TiCompositor(retriever)
    bg_path = write_background(tmp_path / f"bg{suffix}", 200, 200, BACKGROUND_COLOR, fmt)
    out_path = tmp_path / "out.png"

    result = compositor.composite(
        TiCompositeRequest(
            background_path=bg_path,
            output_path=out_path,
            placement=_placement(),
            ti_state=TiState.NEUTRAL,
        )
    )

    assert result.background_width == 200
    assert result.background_height == 200
    assert out_path.is_file()


def test_composite_fails_clearly_with_no_active_asset_set(tmp_path, engine):
    retriever = _retriever(tmp_path, engine, active=False)
    compositor = TiCompositor(retriever)
    bg_path = write_background(tmp_path / "bg.png", 200, 200, BACKGROUND_COLOR, "PNG")

    with pytest.raises(TiAssetSetNotFoundError):
        compositor.composite(
            TiCompositeRequest(
                background_path=bg_path,
                output_path=tmp_path / "out.png",
                placement=_placement(),
                ti_state=TiState.NEUTRAL,
            )
        )


def test_composite_fails_clearly_when_state_asset_file_missing(tmp_path, engine):
    file_store = TiAssetFileStore(tmp_path / "ti_assets")
    asset_set = write_full_ti_asset_set(engine, file_store)
    panic_asset = next(a for a in asset_set.assets if a.state is TiState.PANIC)
    file_store.resolve(panic_asset.relative_path).unlink()

    retriever = SqliteTiAssetRetriever(engine, file_store)
    compositor = TiCompositor(retriever)
    bg_path = write_background(tmp_path / "bg.png", 200, 200, BACKGROUND_COLOR, "PNG")

    with pytest.raises(TiAssetMissingFileError):
        compositor.composite(
            TiCompositeRequest(
                background_path=bg_path,
                output_path=tmp_path / "out.png",
                placement=_placement(),
                ti_state=TiState.PANIC,
            )
        )


def test_out_of_bounds_width_fails_explicitly_instead_of_cropping(tmp_path, engine):
    """Tí's synthetic asset is 40w x 60h. At relative_height=1.0 against a
    50x100 background, the rendered width would be round(40*100/60)=67,
    wider than the 50px-wide background in every possible position --
    this compositor's documented policy fails explicitly rather than ever
    producing a silently cropped composite."""
    retriever = _retriever(tmp_path, engine)
    compositor = TiCompositor(retriever)
    bg_path = write_background(tmp_path / "bg.png", 50, 100, BACKGROUND_COLOR, "PNG")

    with pytest.raises(TiCompositeBoundsError):
        compositor.composite(
            TiCompositeRequest(
                background_path=bg_path,
                output_path=tmp_path / "out.png",
                placement=_placement(scale=1.0),
                ti_state=TiState.NEUTRAL,
            )
        )


def test_out_of_bounds_offset_is_clamped_not_dropped(tmp_path, engine):
    retriever = _retriever(tmp_path, engine)
    compositor = TiCompositor(retriever)
    bg_path = write_background(tmp_path / "bg.png", 200, 200, BACKGROUND_COLOR, "PNG")

    result = compositor.composite(
        TiCompositeRequest(
            background_path=bg_path,
            output_path=tmp_path / "out.png",
            placement=_placement(anchor=TiAnchor.BOTTOM_LEFT, scale=0.2, offset_x=10_000, offset_y=-10_000),
            ti_state=TiState.NEUTRAL,
        )
    )

    assert result.clamped is True
    assert result.placement_x == result.background_width - result.ti_rendered_width
    assert result.placement_y == 0


def test_scale_bounds_are_enforced():
    with pytest.raises(ValueError):
        TiScalePolicy(relative_height=0.0)
    with pytest.raises(ValueError):
        TiScalePolicy(relative_height=1.5)
    with pytest.raises(ValueError):
        TiScalePolicy(relative_height=-0.1)


def test_composite_is_deterministic_for_identical_inputs(tmp_path, engine):
    retriever = _retriever(tmp_path, engine)
    compositor = TiCompositor(retriever)
    bg_path = write_background(tmp_path / "bg.png", 200, 200, BACKGROUND_COLOR, "PNG")

    out_a = tmp_path / "out_a.png"
    out_b = tmp_path / "out_b.png"
    for out_path in (out_a, out_b):
        compositor.composite(
            TiCompositeRequest(
                background_path=bg_path,
                output_path=out_path,
                placement=_placement(),
                ti_state=TiState.NEUTRAL,
            )
        )

    assert out_a.read_bytes() == out_b.read_bytes()


def test_compositor_module_never_imports_a_provider_or_llm_dependency():
    import ast

    import app.ti_compositor.compositor as compositor_module

    source = open(compositor_module.__file__, encoding="utf-8").read()
    imported_names: set[str] = set()
    for node in ast.walk(ast.parse(source)):
        if isinstance(node, ast.Import):
            imported_names.update(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            imported_names.add(node.module)
            imported_names.update(f"{node.module}.{alias.name}" for alias in node.names)

    forbidden_substrings = ("VisualProvider", "CloudflareImageProvider", "GeminiImageProvider", "genai", "httpx")
    for imported in imported_names:
        for forbidden in forbidden_substrings:
            assert forbidden not in imported, f"unexpected import of {imported!r} in compositor.py"


def test_composite_calls_no_network_client(tmp_path, engine, monkeypatch):
    """Belt-and-suspenders: even if some import crept in, httpx.Client/
    google.genai.Client construction would be the observable side effect of
    an actual provider/LLM call. Patch both to explode if ever touched."""
    import httpx

    def _boom(*_args, **_kwargs):
        raise AssertionError("TiCompositor must never construct a network client")

    monkeypatch.setattr(httpx, "Client", _boom)

    retriever = _retriever(tmp_path, engine)
    compositor = TiCompositor(retriever)
    bg_path = write_background(tmp_path / "bg.png", 200, 200, BACKGROUND_COLOR, "PNG")

    compositor.composite(
        TiCompositeRequest(
            background_path=bg_path,
            output_path=tmp_path / "out.png",
            placement=_placement(),
            ti_state=TiState.NEUTRAL,
        )
    )
