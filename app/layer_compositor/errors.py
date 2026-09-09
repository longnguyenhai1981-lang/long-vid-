"""Layer-compositor-layer domain errors (Phase 26).

Deliberately narrow, mirroring app/ti_compositor/errors.py's shape
exactly: structural/shape validation of a LayerCompositionSpec itself is
already exhaustively enforced by app/layer_compositor/models.py's own
pydantic model validators (zero backgrounds, multiple backgrounds, blank/
duplicate layer ids, invalid scale, both relative_width/relative_height
supplied, invalid output extension) -- these classes only cover what can
still go wrong once a spec is already known-valid: loading/decoding a
source image, fitting a scaled overlay into the canvas, and writing the
final PNG.
"""

from __future__ import annotations


class LayerCompositionError(Exception):
    """Base class for all layer-compositor-layer errors."""


class LayerCompositionBackgroundError(LayerCompositionError):
    """Raised when the BACKGROUND layer's source image cannot be used:
    missing file, unsupported format, or unreadable/corrupt image bytes."""


class LayerCompositionOverlayError(LayerCompositionError):
    """Raised when an OVERLAY layer's source image cannot be used: missing
    file, unsupported format, or unreadable/corrupt image bytes."""


class LayerCompositionBoundsError(LayerCompositionError):
    """Raised when an overlay's scaled size exceeds the canvas in some
    dimension, so no position -- clamped or not -- could ever place it
    fully in-frame. Never silently crops an oversized overlay; see
    compositor.py's module docstring for the full out-of-bounds policy."""


class LayerCompositionWriteError(LayerCompositionError):
    """Raised when writing the final composited output image to disk
    fails."""
