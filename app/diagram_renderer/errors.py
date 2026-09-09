"""Diagram-renderer-layer domain errors (Phase 25).

Deliberately a single, narrow exception -- structural/shape validation of
a DiagramSpec itself is already exhaustively enforced by
app/models/diagram.py's own pydantic model validators (mirroring how
TiCompositeRequest/TiPlacement (Phase 22) validate themselves without a
dedicated wrapper exception). This module only covers what can still go
wrong once a spec is already known-valid: the drawing/encoding/file-write
step itself.
"""

from __future__ import annotations


class DiagramRenderError(Exception):
    """Raised when DiagramRenderer fails to draw or persist a diagram: an
    element type reaching the draw dispatch that no branch handles
    (unreachable in practice, since DiagramSpec.elements is a discriminated
    union over the exact same element types this renderer draws -- guarded
    defensively rather than assumed), or a filesystem failure while
    writing the output PNG. Never retried -- like TiCompositor, this is a
    deterministic local operation, not a network call."""
