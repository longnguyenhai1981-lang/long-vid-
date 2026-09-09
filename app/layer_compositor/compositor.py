"""VisualLayerCompositor: deterministic multi-layer static image composition
(Phase 26).

Sits ABOVE the three existing deterministic/AI visual-generation systems
(GENERATED_STILL's VisualProvider, TI_STATE's TiCompositor, DIAGRAM's
DiagramRenderer) without replacing or depending on any of them -- this
package composes already-rendered raster files it is handed by a caller,
via LayerCompositionSpec.layers[*].source_path. It never generates new
visual content, never queries a database/manifest/VisualPlan/provider,
and never performs computer-vision or LLM-based placement -- every
position and scale in the output comes directly from the caller-authored
spec.

Pillow is used here exactly as app/ti_compositor/compositor.py (Phase 22)
and app/diagram_renderer/renderer.py (Phase 25) already use it: resizing
raster images, alpha-compositing them, and decoding PNG/JPEG bytes.

Out-of-bounds policy (Phase 26 requirement #9 -- pick one, document it):
this compositor CLAMPS, identically to TiCompositor's Phase 22 policy.
LayerScale already guarantees an overlay's rendered height or width never
exceeds the canvas's own height or width (0 < relative_height|
relative_width <= 1); given that, the requested anchor+margin+offset
position is clamped so the full scaled overlay always stays within the
canvas frame, and LayerGeometry.clamped reports whether clamping actually
changed the position. The one case this compositor refuses outright, via
LayerCompositionBoundsError, is a scaled overlay whose width OR height
exceeds the canvas's own width or height (possible when an overlay's
native aspect ratio is extreme relative to the canvas) -- there, no
position, clamped or not, could ever fit the full overlay in-frame, so
failing loudly is the only option that isn't a silent crop.

Z-order policy (Phase 26 requirement #7): overlays are drawn in ascending
z_index order; ties are broken by authored `LayerCompositionSpec.layers`
list order (Python's own stable sort preserves this for free -- no
duplicate-z_index rejection). The BACKGROUND layer is always drawn first,
beneath every overlay, regardless of any z_index a caller might have
supplied for it (BackgroundLayer has no z_index field at all -- see
models.py).
"""

from __future__ import annotations

from pathlib import Path

from PIL import Image, UnidentifiedImageError

from app.layer_compositor.errors import (
    LayerCompositionBackgroundError,
    LayerCompositionBoundsError,
    LayerCompositionOverlayError,
    LayerCompositionWriteError,
)
from app.layer_compositor.models import (
    LayerAnchor,
    LayerCompositionResult,
    LayerCompositionSpec,
    LayerGeometry,
    LayerScale,
    LayerSourceType,
    OverlayLayer,
)

_SUPPORTED_SOURCE_SUFFIXES = frozenset({".png", ".jpg", ".jpeg"})


class VisualLayerCompositor:
    """Composites an ordered stack of already-existing raster layers into
    one final PNG frame. Stateless and dependency-free -- unlike
    TiCompositor (which depends on a TiAssetRetriever), this class takes
    no constructor argument at all; every input a call needs is fully
    self-contained in the LayerCompositionSpec passed to compose()."""

    def compose(self, spec: LayerCompositionSpec) -> LayerCompositionResult:
        background_layer = next(layer for layer in spec.layers if layer.source_type is LayerSourceType.BACKGROUND)
        overlay_layers = [layer for layer in spec.layers if layer.source_type is LayerSourceType.OVERLAY]
        # Stable sort: ties keep their original position in spec.layers,
        # satisfying the documented tie-break policy for free.
        overlay_layers_sorted = sorted(overlay_layers, key=lambda layer: layer.z_index)

        background_image = self._load_image(
            background_layer.source_path, LayerCompositionBackgroundError, "background"
        ).convert("RGB")
        canvas_size = background_image.size
        composed = background_image.copy()

        geometry: list[LayerGeometry] = [
            LayerGeometry(
                layer_id=background_layer.id, x=0, y=0, width=canvas_size[0], height=canvas_size[1], clamped=False
            )
        ]

        for layer in overlay_layers_sorted:
            layer_geometry = self._composite_overlay(composed, layer, canvas_size)
            geometry.append(layer_geometry)

        self._write_output(composed, spec.output_path)

        return LayerCompositionResult(
            output_path=spec.output_path,
            canvas_width=canvas_size[0],
            canvas_height=canvas_size[1],
            layers=geometry,
        )

    def _composite_overlay(
        self, composed: Image.Image, layer: OverlayLayer, canvas_size: tuple[int, int]
    ) -> LayerGeometry:
        overlay_image = self._load_image(layer.source_path, LayerCompositionOverlayError, "overlay").convert("RGBA")

        scaled_size = compute_scaled_size(overlay_image.size, canvas_size, layer.scale)
        overlay_resized = overlay_image.resize(scaled_size, Image.LANCZOS)

        requested_x, requested_y = compute_anchor_position(
            canvas_size, scaled_size, layer.anchor, layer.margin, layer.offset_x, layer.offset_y
        )
        (final_x, final_y), clamped = clamp_position(canvas_size, scaled_size, (requested_x, requested_y))

        composed.paste(overlay_resized, (final_x, final_y), overlay_resized)

        return LayerGeometry(
            layer_id=layer.id, x=final_x, y=final_y, width=scaled_size[0], height=scaled_size[1], clamped=clamped
        )

    @staticmethod
    def _load_image(path: Path, error_cls: type[Exception], role: str) -> Image.Image:
        if not path.is_file():
            raise error_cls(f"{role.capitalize()} image not found: {path}")

        suffix = path.suffix.lower()
        if suffix not in _SUPPORTED_SOURCE_SUFFIXES:
            raise error_cls(
                f"Unsupported {role} format {suffix!r}; expected one of {sorted(_SUPPORTED_SOURCE_SUFFIXES)}"
            )

        try:
            image = Image.open(path)
            image.load()
        except (UnidentifiedImageError, OSError) as exc:
            raise error_cls(f"Failed to read {role} image at {path}: {exc}") from exc
        return image

    @staticmethod
    def _write_output(image: Image.Image, output_path: Path) -> None:
        try:
            output_path.parent.mkdir(parents=True, exist_ok=True)
            image.save(output_path, format="PNG")
        except OSError as exc:
            raise LayerCompositionWriteError(f"Failed to write composited output to {output_path}: {exc}") from exc


def compute_scaled_size(
    native_size: tuple[int, int], canvas_size: tuple[int, int], scale: LayerScale
) -> tuple[int, int]:
    """Resize target for an overlay, aspect ratio always preserved:
    - relative_height set: rendered height = round(canvas_height * relative_height);
      width follows from the overlay's own aspect ratio.
    - relative_width set: rendered width = round(canvas_width * relative_width);
      height follows from the overlay's own aspect ratio.
    - neither set: the overlay's native pixel dimensions, unscaled.
    Both dimensions are floored at 1px so a pathologically small scale
    can never produce a zero-size (i.e. invisible/degenerate) image."""
    native_width, native_height = native_size
    canvas_width, canvas_height = canvas_size

    if scale.relative_height is not None:
        target_height = max(1, round(canvas_height * scale.relative_height))
        target_width = max(1, round(native_width * target_height / native_height))
        return target_width, target_height

    if scale.relative_width is not None:
        target_width = max(1, round(canvas_width * scale.relative_width))
        target_height = max(1, round(native_height * target_width / native_width))
        return target_width, target_height

    return native_width, native_height


def compute_anchor_position(
    canvas_size: tuple[int, int],
    layer_size: tuple[int, int],
    anchor: LayerAnchor,
    margin: int,
    offset_x: int,
    offset_y: int,
) -> tuple[int, int]:
    """The requested (pre-clamp) top-left pixel position for layer_size on
    canvas_size, per the 9-anchor vocabulary's documented anchor/margin/
    offset semantics. May return a position that puts the overlay
    partially or fully out-of-frame -- clamp_position() resolves that."""
    canvas_width, canvas_height = canvas_size
    layer_width, layer_height = layer_size

    if anchor is LayerAnchor.TOP_LEFT:
        x, y = margin, margin
    elif anchor is LayerAnchor.TOP_CENTER:
        x, y = (canvas_width - layer_width) // 2, margin
    elif anchor is LayerAnchor.TOP_RIGHT:
        x, y = canvas_width - layer_width - margin, margin
    elif anchor is LayerAnchor.CENTER_LEFT:
        x, y = margin, (canvas_height - layer_height) // 2
    elif anchor is LayerAnchor.CENTER:
        x, y = (canvas_width - layer_width) // 2, (canvas_height - layer_height) // 2
    elif anchor is LayerAnchor.CENTER_RIGHT:
        x, y = canvas_width - layer_width - margin, (canvas_height - layer_height) // 2
    elif anchor is LayerAnchor.BOTTOM_LEFT:
        x, y = margin, canvas_height - layer_height - margin
    elif anchor is LayerAnchor.BOTTOM_CENTER:
        x, y = (canvas_width - layer_width) // 2, canvas_height - layer_height - margin
    elif anchor is LayerAnchor.BOTTOM_RIGHT:
        x, y = canvas_width - layer_width - margin, canvas_height - layer_height - margin
    else:  # pragma: no cover -- unreachable for a real LayerAnchor member
        raise AssertionError(f"unhandled LayerAnchor: {anchor!r}")

    return x + offset_x, y + offset_y


def clamp_position(
    canvas_size: tuple[int, int], layer_size: tuple[int, int], position: tuple[int, int]
) -> tuple[tuple[int, int], bool]:
    """Apply this compositor's clamp-first out-of-bounds policy (see this
    module's docstring). Raises LayerCompositionBoundsError only when
    layer_size cannot possibly fit inside canvas_size regardless of
    position. Returns ((x, y), clamped) where clamped is True iff position
    had to move to land in-bounds."""
    canvas_width, canvas_height = canvas_size
    layer_width, layer_height = layer_size
    x, y = position

    if layer_width > canvas_width or layer_height > canvas_height:
        raise LayerCompositionBoundsError(
            f"scaled overlay size {layer_width}x{layer_height} exceeds canvas size "
            f"{canvas_width}x{canvas_height}; reduce scale.relative_height/relative_width"
        )

    clamped_x = min(max(x, 0), canvas_width - layer_width)
    clamped_y = min(max(y, 0), canvas_height - layer_height)
    clamped = (clamped_x, clamped_y) != (x, y)
    return (clamped_x, clamped_y), clamped
