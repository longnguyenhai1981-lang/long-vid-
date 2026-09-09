"""Deterministic static multi-layer image composition (Phase 26).

Combines already-existing raster assets (a GENERATED_STILL background, a
DIAGRAM overlay, a canonical Tí overlay, another composed frame, ...)
into one final PNG frame using fixed, rule-based placement/scale -- no
computer-vision placement, no saliency model, no LLM layout, and no new
visual-content generation of any kind. A new, separate architecture
boundary alongside app/ti_compositor/ (Phase 22) and
app/diagram_renderer/ (Phase 25): VisualLayerCompositor depends on
nothing but the LayerCompositionSpec it is given -- never on
VisualProvider, TiCompositor, DiagramRenderer, a database, a manifest, or
app/renderers/visual/renderer.py. As of Phase 26 it IS consumed by
app/renderers/visual/renderer.py for every VisualMediaType.COMPOSITION
beat, through this same public surface.
"""

from __future__ import annotations
