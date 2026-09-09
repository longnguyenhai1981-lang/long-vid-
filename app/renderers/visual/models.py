"""Visual Renderer input/output contracts.

VisualRenderManifest (app/models/visual_render.py) is the single approved
business output -- these are thin wrappers around it, not a competing
schema. TiStateRenderMode/TiStateSource (Phase 23) are the minimum typed
contract needed for the caller to make a TI_STATE beat's rendering mode
explicit -- see app/renderers/visual/renderer.py's module docstring for
how they're consumed.

Phase 24 extends TiStateSource's COMPOSITE mode with a second, mutually
exclusive background source: background_beat_id, which names another
VisualBeat in the same VisualPlan whose own RenderedVisualAsset (produced
earlier in the same render pass) becomes the background, instead of an
explicit background_path the caller already has on disk. Which beat
renders before which is resolved deterministically from these references
-- see app/renderers/visual/renderer.py's dependency-graph helpers.

Phase 26 adds CompositionSpec/CompositionLayerSource: the renderer-input,
pre-resolution counterpart to app/layer_compositor/models.py's
LayerCompositionSpec/VisualLayer. Where VisualLayer always carries a
concrete, already-resolved source_path, a CompositionLayerSource may
instead carry a source_beat_id -- another VisualBeat in the same
VisualPlan whose own render-pass output supplies the layer -- resolved to
a concrete path by the renderer before VisualLayerCompositor ever sees
it, using the same generalized dependency machinery Phase 24 introduced
for TiStateSource.background_beat_id.
"""

from __future__ import annotations

from enum import Enum
from pathlib import Path
from typing import Annotated, Literal, Union
from uuid import UUID

from pydantic import Field, model_validator

from app.layer_compositor.models import LayerAnchor, LayerScale, LayerSourceType
from app.models.common import MotilyModel, non_blank
from app.models.visual_render import VisualRenderManifest
from app.ti_compositor.models import TiPlacement

VISUAL_RENDER_MANIFEST_ARTIFACT_TYPE = "visual_render_manifest"


class TiStateRenderMode(str, Enum):
    """Which of the two deterministic TI_STATE modes (Phase 23) a beat
    uses. Never inferred from visual content -- always an explicit choice
    the caller makes via TiStateSource."""

    STANDALONE = "STANDALONE"
    COMPOSITE = "COMPOSITE"


class TiStateSource(MotilyModel):
    """Per-beat TI_STATE rendering instructions the caller supplies via
    VisualRendererInput.ti_state_sources, keyed by beat_id. A beat with no
    entry in that mapping defaults to STANDALONE -- the same "never invent
    a background" default as an explicit STANDALONE entry.

    background_path/background_beat_id/placement are only meaningful in
    COMPOSITE mode, which requires exactly one of the two background
    sources (Phase 24):

    - background_path (Phase 23): an existing background image the caller
      already has on disk -- this renderer never generates one.
    - background_beat_id (Phase 24): the beat_id of another VisualBeat in
      the same VisualPlan; that beat's own RenderedVisualAsset from the
      current render pass becomes the background. Never a fuzzy match,
      never the "nearest previous beat" -- an exact beat_id only, and the
      referenced beat renders first regardless of authored list order
      (see the renderer's dependency-graph helpers).

    Supplying both, or neither, in COMPOSITE mode is a validation failure.
    placement, if omitted, falls back to VisualRenderer's own default
    TiPlacement.
    """

    mode: TiStateRenderMode = TiStateRenderMode.STANDALONE
    background_path: Path | None = None
    background_beat_id: str | None = None
    placement: TiPlacement | None = None

    @model_validator(mode="after")
    def _check_invariants(self) -> "TiStateSource":
        if self.background_beat_id is not None:
            non_blank(self.background_beat_id, "background_beat_id")

        if self.mode is TiStateRenderMode.STANDALONE:
            if self.background_path is not None or self.background_beat_id is not None:
                raise ValueError(
                    "TiStateSource.mode is STANDALONE but a background source "
                    "(background_path or background_beat_id) is set -- use "
                    "mode=COMPOSITE to composite onto a background, or omit both "
                    "background fields for a standalone canonical asset reference"
                )
        else:  # COMPOSITE
            sources_supplied = sum(
                1 for value in (self.background_path, self.background_beat_id) if value is not None
            )
            if sources_supplied != 1:
                raise ValueError(
                    "TiStateSource.mode is COMPOSITE but must supply exactly one "
                    f"of background_path or background_beat_id (got {sources_supplied} "
                    f"supplied: background_path={self.background_path!r}, "
                    f"background_beat_id={self.background_beat_id!r})"
                )
        return self


class BackgroundLayerSource(MotilyModel):
    """A CompositionSpec's BACKGROUND layer, pre-resolution. Exactly one of
    source_path/source_beat_id, exactly like TiStateSource's COMPOSITE
    mode -- no anchor/scale/margin/offset fields at all (meaningless for a
    background, same reasoning as VisualLayer's BackgroundLayer)."""

    source_type: Literal[LayerSourceType.BACKGROUND] = LayerSourceType.BACKGROUND
    id: str
    source_path: Path | None = None
    source_beat_id: str | None = None
    notes: str | None = None

    @model_validator(mode="after")
    def _check_invariants(self) -> "BackgroundLayerSource":
        non_blank(self.id, "id")
        if self.source_beat_id is not None:
            non_blank(self.source_beat_id, "source_beat_id")
        sources_supplied = sum(1 for value in (self.source_path, self.source_beat_id) if value is not None)
        if sources_supplied != 1:
            raise ValueError(
                "BackgroundLayerSource must supply exactly one of source_path or "
                f"source_beat_id (got {sources_supplied} supplied: "
                f"source_path={self.source_path!r}, source_beat_id={self.source_beat_id!r})"
            )
        return self


class OverlayLayerSource(MotilyModel):
    """One of a CompositionSpec's OVERLAY layers, pre-resolution. Exactly
    one of source_path/source_beat_id. anchor/scale/margin/offset_x/
    offset_y carry the exact same semantics as app/layer_compositor/
    models.py's OverlayLayer -- copied through unchanged once the source
    is resolved to a concrete path (see
    app/renderers/visual/renderer.py's _render_composition_beat)."""

    source_type: Literal[LayerSourceType.OVERLAY] = LayerSourceType.OVERLAY
    id: str
    source_path: Path | None = None
    source_beat_id: str | None = None
    z_index: int = 0
    anchor: LayerAnchor = LayerAnchor.CENTER
    scale: LayerScale = Field(default_factory=LayerScale)
    margin: int = 0
    offset_x: int = 0
    offset_y: int = 0
    notes: str | None = None

    @model_validator(mode="after")
    def _check_invariants(self) -> "OverlayLayerSource":
        non_blank(self.id, "id")
        if self.source_beat_id is not None:
            non_blank(self.source_beat_id, "source_beat_id")
        sources_supplied = sum(1 for value in (self.source_path, self.source_beat_id) if value is not None)
        if sources_supplied != 1:
            raise ValueError(
                "OverlayLayerSource must supply exactly one of source_path or "
                f"source_beat_id (got {sources_supplied} supplied: "
                f"source_path={self.source_path!r}, source_beat_id={self.source_beat_id!r})"
            )
        if self.margin < 0:
            raise ValueError(f"margin must be >= 0; got {self.margin}")
        return self


CompositionLayerSource = Annotated[
    Union[BackgroundLayerSource, OverlayLayerSource], Field(discriminator="source_type")
]


class CompositionSpec(MotilyModel):
    """Per-beat COMPOSITION instructions the caller supplies via
    VisualRendererInput.composition_specs, keyed by beat_id (Phase 26).
    Unlike TiStateSource, there is no default -- a COMPOSITION beat with
    no entry here is a hard failure (MissingCompositionSpecError), since
    there is no sensible "compose nothing" default. No output_path field:
    the renderer computes that itself, exactly like every other rendered
    beat's {project_id}/{visual_plan_id}/{render_job_id}.png path."""

    layers: list[CompositionLayerSource] = Field(min_length=1)

    @model_validator(mode="after")
    def _check_invariants(self) -> "CompositionSpec":
        backgrounds = [layer for layer in self.layers if layer.source_type is LayerSourceType.BACKGROUND]
        if len(backgrounds) != 1:
            raise ValueError(f"CompositionSpec requires exactly one BACKGROUND layer; got {len(backgrounds)}")

        ids = [layer.id for layer in self.layers]
        seen: set[str] = set()
        duplicates: set[str] = set()
        for layer_id in ids:
            if layer_id in seen:
                duplicates.add(layer_id)
            seen.add(layer_id)
        if duplicates:
            raise ValueError(f"CompositionSpec has duplicate layer ids: {sorted(duplicates)}")
        return self


class VisualRendererInput(MotilyModel):
    project_id: UUID
    ti_state_sources: dict[str, TiStateSource] = Field(default_factory=dict)
    """Optional per-beat TI_STATE mode/background overrides, keyed by
    VisualBeat.beat_id. A TI_STATE beat with no entry here renders in
    STANDALONE mode (Phase 23's default, non-destructive behavior)."""
    composition_specs: dict[str, CompositionSpec] = Field(default_factory=dict)
    """Required per-beat COMPOSITION layer instructions, keyed by
    VisualBeat.beat_id (Phase 26). A COMPOSITION beat with no entry here
    fails explicitly (MissingCompositionSpecError) -- there is no default."""


class VisualRendererResult(MotilyModel):
    manifest: VisualRenderManifest
    module_run_id: UUID
    provider_call_count: int
    rendered_asset_count: int
    external_requirement_count: int
    reuse_requirement_count: int
    canonical_asset_ready_count: int = 0
    """Count of TI_STATE beats resolved to a standalone canonical asset
    reference (VisualRequirementStatus.CANONICAL_ASSET_READY), Phase 23."""
