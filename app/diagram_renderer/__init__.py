"""Deterministic local diagram rendering (Phase 25).

Draws a DiagramSpec (app/models/diagram.py) to a PNG using Pillow's
ImageDraw -- no AI generation, no LLM layout, no computer-vision
placement. A new, separate architecture boundary alongside
app/ti_compositor/ (Phase 22): DiagramRenderer depends only on the
DiagramSpec it is given, never on VisualProvider, a concrete provider, or
app/renderers/visual/renderer.py. As of Phase 25 it IS consumed by
app/renderers/visual/renderer.py for every VisualMediaType.DIAGRAM beat,
through this same public surface.
"""

from __future__ import annotations
