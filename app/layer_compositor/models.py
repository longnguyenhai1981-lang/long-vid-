"""Typed contracts for the Phase 26 deterministic layer compositor.

VisualLayer/LayerCompositionSpec/LayerCompositionResult/LayerAnchor/
LayerScale/LayerSourceType are plain, dependency-free data contracts --
no filesystem I/O, no Pillow, no database. app/layer_compositor/
compositor.py is the only module that acts on them.

Every layer here carries a concrete, already-resolved `source_path` --
this package never queries a database, a VisualRenderManifest, a
VisualPlan, or a provider to figure out what file a layer means. Turning
a beat_id reference into a concrete path is the caller's job (see
app/renderers/visual/renderer.py's Phase 26 integration, and
app/renderers/visual/models.py's CompositionSpec for the renderer-input
contract that still allows a beat_id reference, resolved before this
package ever sees it).
"""

from __future__ import annotations

from enum import Enum
from pathlib import Path
from typing import Annotated, Literal, Union

from pydantic import Field, model_validator

from app.models.common import MotilyModel, non_blank

_SUPPORTED_OUTPUT_SUFFIXES = frozenset({".png"})


class LayerSourceType(str, Enum):
    """The two MVP layer kinds (Phase 26) -- no semantic "Tí layer" or
    "diagram layer" concept here; the compositor treats every layer as a
    plain raster image regardless of what produced it."""

    BACKGROUND = "BACKGROUND"
    OVERLAY = "OVERLAY"


class LayerAnchor(str, Enum):
    """The fixed 9-position placement vocabulary (Phase 26) -- no
    automatic computer-vision or saliency-based placement, and no other
    anchor. A superset of app/ti_compositor/models.py's 6-member TiAnchor
    (adds the three TOP_* members); deliberately a separate enum rather
    than reusing TiAnchor, since this package has no dependency on
    app/ti_compositor/ and the two vocabularies are free to diverge."""

    TOP_LEFT = "TOP_LEFT"
    TOP_CENTER = "TOP_CENTER"
    TOP_RIGHT = "TOP_RIGHT"
    CENTER_LEFT = "CENTER_LEFT"
    CENTER = "CENTER"
    CENTER_RIGHT = "CENTER_RIGHT"
    BOTTOM_LEFT = "BOTTOM_LEFT"
    BOTTOM_CENTER = "BOTTOM_CENTER"
    BOTTOM_RIGHT = "BOTTOM_RIGHT"


class LayerScale(MotilyModel):
    """An overlay's rendered size as an explicit fraction of the canvas,
    aspect ratio always preserved -- never both axes at once (that would
    let the caller distort aspect ratio, which this package never does).
    Both None means the overlay's own native pixel dimensions are used
    unscaled."""

    relative_height: float | None = None
    relative_width: float | None = None

    @model_validator(mode="after")
    def _check_invariants(self) -> "LayerScale":
        if self.relative_height is not None and self.relative_width is not None:
            raise ValueError(
                "LayerScale must not supply both relative_height and relative_width "
                "-- exactly one, or neither for native size"
            )
        for name, value in (("relative_height", self.relative_height), ("relative_width", self.relative_width)):
            if value is not None and not (0 < value <= 1):
                raise ValueError(f"{name} must satisfy 0 < value <= 1; got {value}")
        return self


class BackgroundLayer(MotilyModel):
    """Fills the final canvas -- exactly one required per
    LayerCompositionSpec (enforced there). Has no anchor/scale/margin/
    offset fields at all: those concepts are meaningless for the layer
    that defines the canvas itself, so they are structurally absent
    rather than accepted-and-ignored."""

    source_type: Literal[LayerSourceType.BACKGROUND] = LayerSourceType.BACKGROUND
    id: str
    source_path: Path
    notes: str | None = None

    @model_validator(mode="after")
    def _check_invariants(self) -> "BackgroundLayer":
        non_blank(self.id, "id")
        non_blank(str(self.source_path), "source_path")
        return self


class OverlayLayer(MotilyModel):
    """Placed over the background (and over any lower-z_index overlay).
    Transparent PNG or opaque raster; alpha compositing only, no blend
    modes."""

    source_type: Literal[LayerSourceType.OVERLAY] = LayerSourceType.OVERLAY
    id: str
    source_path: Path
    z_index: int = 0
    anchor: LayerAnchor = LayerAnchor.CENTER
    scale: LayerScale = Field(default_factory=LayerScale)
    margin: int = 0
    offset_x: int = 0
    offset_y: int = 0
    notes: str | None = None

    @model_validator(mode="after")
    def _check_invariants(self) -> "OverlayLayer":
        non_blank(self.id, "id")
        non_blank(str(self.source_path), "source_path")
        if self.margin < 0:
            raise ValueError(f"margin must be >= 0; got {self.margin}")
        return self


VisualLayer = Annotated[Union[BackgroundLayer, OverlayLayer], Field(discriminator="source_type")]
"""A discriminated union keyed on `source_type` -- pydantic itself rejects
any `source_type` value outside LayerSourceType's two members."""


class LayerCompositionSpec(MotilyModel):
    """One composition job: an ordered, non-empty list of layers (exactly
    one BACKGROUND, any number of OVERLAY) and where to write the result.
    Every layer's `source_path` must already be a concrete, existing file
    -- this spec never resolves a beat_id, a manifest entry, or a
    canonical-asset reference itself."""

    layers: list[VisualLayer] = Field(min_length=1)
    output_path: Path

    @model_validator(mode="after")
    def _check_invariants(self) -> "LayerCompositionSpec":
        backgrounds = [layer for layer in self.layers if layer.source_type is LayerSourceType.BACKGROUND]
        if len(backgrounds) != 1:
            raise ValueError(
                f"LayerCompositionSpec requires exactly one BACKGROUND layer; got {len(backgrounds)}"
            )

        ids = [layer.id for layer in self.layers]
        seen: set[str] = set()
        duplicates: set[str] = set()
        for layer_id in ids:
            if layer_id in seen:
                duplicates.add(layer_id)
            seen.add(layer_id)
        if duplicates:
            raise ValueError(f"LayerCompositionSpec has duplicate layer ids: {sorted(duplicates)}")

        non_blank(str(self.output_path), "output_path")
        output_suffix = self.output_path.suffix.lower()
        if output_suffix not in _SUPPORTED_OUTPUT_SUFFIXES:
            raise ValueError(
                f"unsupported output_path format {output_suffix!r}; "
                f"expected one of {sorted(_SUPPORTED_OUTPUT_SUFFIXES)}"
            )
        return self


class LayerGeometry(MotilyModel):
    """Where one layer actually ended up, in enough detail to assert on in
    tests without re-opening the output image."""

    layer_id: str
    x: int
    y: int
    width: int
    height: int
    clamped: bool

    @model_validator(mode="after")
    def _check_invariants(self) -> "LayerGeometry":
        if self.width <= 0 or self.height <= 0:
            raise ValueError("width/height must be > 0")
        return self


class LayerCompositionResult(MotilyModel):
    """What VisualLayerCompositor actually did. `layers` includes the
    BACKGROUND entry too (always `x=0, y=0, clamped=False`, matching the
    canvas exactly) so a caller has one complete geometry record per
    input layer, not just the overlays."""

    output_path: Path
    canvas_width: int
    canvas_height: int
    layers: list[LayerGeometry]

    @model_validator(mode="after")
    def _check_invariants(self) -> "LayerCompositionResult":
        if self.canvas_width <= 0 or self.canvas_height <= 0:
            raise ValueError("canvas_width/canvas_height must be > 0")
        return self

    @property
    def clamped_layer_ids(self) -> list[str]:
        return [layer.layer_id for layer in self.layers if layer.clamped]
