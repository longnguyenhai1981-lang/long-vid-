"""Typed contracts for the Phase 22 Tí compositor.

TiCompositeRequest/TiCompositeResult/TiPlacement/TiAnchor/TiScalePolicy are
plain, dependency-free data contracts -- no filesystem I/O, no Pillow, no
SQLite. app/ti_compositor/compositor.py is the only module that acts on
them.
"""

from __future__ import annotations

from enum import Enum
from pathlib import Path

from pydantic import model_validator

from app.models.common import MotilyModel, VisualTiState, non_blank
from app.models.ti_assets import TiState

_SUPPORTED_BACKGROUND_SUFFIXES = frozenset({".png", ".jpg", ".jpeg"})
_SUPPORTED_OUTPUT_SUFFIXES = frozenset({".png"})
"""Output is PNG-only (Phase 22 requirement #6: "PNG preferred for
deterministic lossless composition"), read here as a hard requirement
rather than a soft preference -- a lossy JPG re-encode of a just-composited
frame would make "deterministic output for identical inputs" (Phase 22
requirement #9) needlessly fragile across Pillow/libjpeg versions."""


class TiAnchor(str, Enum):
    """The fixed MVP placement vocabulary (Phase 22) -- no automatic
    computer-vision or saliency-based placement, and no other anchor."""

    BOTTOM_LEFT = "BOTTOM_LEFT"
    BOTTOM_CENTER = "BOTTOM_CENTER"
    BOTTOM_RIGHT = "BOTTOM_RIGHT"
    CENTER_LEFT = "CENTER_LEFT"
    CENTER = "CENTER"
    CENTER_RIGHT = "CENTER_RIGHT"


class TiScalePolicy(MotilyModel):
    """Tí's rendered height as an explicit fraction of the background's
    height, aspect ratio always preserved. 0.20 means Tí's rendered height
    is 20% of the background's height."""

    relative_height: float

    @model_validator(mode="after")
    def _check_bounds(self) -> "TiScalePolicy":
        if not (0 < self.relative_height <= 1):
            raise ValueError(f"relative_height must satisfy 0 < value <= 1; got {self.relative_height}")
        return self


class TiPlacement(MotilyModel):
    """Where and how large to render Tí on the background.

    margin is inward padding (pixels) applied on each edge-anchored axis of
    `anchor` (e.g. BOTTOM_LEFT's bottom and left edges); it has no effect on
    an axis `anchor` centers rather than pins to an edge (e.g. CENTER
    ignores margin entirely -- only offset_x/offset_y move it). offset_x/
    offset_y are an additional, unrestricted pixel nudge applied after
    anchor+margin, on top of either axis; a nudge that would push Tí
    out-of-frame is resolved by TiCompositor's documented clamp-or-fail
    policy (see compositor.py), never by silently clipping.
    """

    anchor: TiAnchor
    scale: TiScalePolicy
    margin: int = 0
    offset_x: int = 0
    offset_y: int = 0

    @model_validator(mode="after")
    def _check_margin(self) -> "TiPlacement":
        if self.margin < 0:
            raise ValueError(f"margin must be >= 0; got {self.margin}")
        return self


class TiCompositeRequest(MotilyModel):
    """One composition job: exactly one of `visual_ti_state` or `ti_state`
    selects which canonical Tí asset to composite -- never both, never
    neither, and never inferred. `visual_ti_state` is resolved through the
    existing deterministic `to_ti_state()` mapping (Phase 21.1) before
    TiAssetRetriever.get_asset() is ever called; `ti_state` is used as-is.
    """

    background_path: Path
    output_path: Path
    placement: TiPlacement
    visual_ti_state: VisualTiState | None = None
    ti_state: TiState | None = None

    @model_validator(mode="after")
    def _check_invariants(self) -> "TiCompositeRequest":
        non_blank(str(self.background_path), "background_path")
        non_blank(str(self.output_path), "output_path")

        if (self.visual_ti_state is None) == (self.ti_state is None):
            raise ValueError(
                "exactly one of visual_ti_state or ti_state must be provided "
                f"(got visual_ti_state={self.visual_ti_state!r}, ti_state={self.ti_state!r})"
            )

        background_suffix = self.background_path.suffix.lower()
        if background_suffix not in _SUPPORTED_BACKGROUND_SUFFIXES:
            raise ValueError(
                f"unsupported background_path format {background_suffix!r}; "
                f"expected one of {sorted(_SUPPORTED_BACKGROUND_SUFFIXES)}"
            )

        output_suffix = self.output_path.suffix.lower()
        if output_suffix not in _SUPPORTED_OUTPUT_SUFFIXES:
            raise ValueError(
                f"unsupported output_path format {output_suffix!r}; "
                f"expected one of {sorted(_SUPPORTED_OUTPUT_SUFFIXES)}"
            )

        return self


class TiCompositeResult(MotilyModel):
    """What TiCompositor actually did, in enough detail to assert on in
    tests without re-opening the output image: resolved state, final
    pixel geometry, and whether the requested position had to be clamped
    into bounds (see TiCompositor's out-of-bounds policy)."""

    output_path: Path
    resolved_ti_state: TiState
    source_visual_ti_state: VisualTiState | None
    background_width: int
    background_height: int
    ti_rendered_width: int
    ti_rendered_height: int
    requested_x: int
    requested_y: int
    placement_x: int
    placement_y: int
    clamped: bool

    @model_validator(mode="after")
    def _check_invariants(self) -> "TiCompositeResult":
        if self.background_width <= 0 or self.background_height <= 0:
            raise ValueError("background_width/background_height must be > 0")
        if self.ti_rendered_width <= 0 or self.ti_rendered_height <= 0:
            raise ValueError("ti_rendered_width/ti_rendered_height must be > 0")
        return self
