"""Tí-compositor-layer domain errors.

Deliberately its own hierarchy, not a subclass of TiAssetError or
VisualError: compositing a background+Tí image is a different kind of
problem from canonical-asset retrieval (app/ti_assets/errors.py) or
provider rendering (app/visual/errors.py), even though TiCompositor
depends on TiAssetRetriever. A missing active asset set or missing state
asset still raises the existing app/ti_assets/errors.py exceptions
unchanged -- TiCompositor never wraps or swallows those -- these classes
only cover problems specific to the compositing step itself.
"""

from __future__ import annotations


class TiCompositorError(Exception):
    """Base class for all Tí-compositor-layer errors."""


class TiCompositeBackgroundError(TiCompositorError):
    """Raised when a request's background image cannot be used: missing
    file, unsupported format, or unreadable/corrupt image bytes."""


class TiCompositeAssetError(TiCompositorError):
    """Raised when the canonical Tí asset file resolved via TiAssetRetriever
    exists on disk (retriever's own existence check already passed) but its
    bytes cannot be decoded as an image."""


class TiCompositeBoundsError(TiCompositorError):
    """Raised when the requested scale makes the rendered Tí asset larger
    than the background in some dimension, so no position -- clamped or
    not -- could ever place it fully in-frame. Never silently produces a
    cropped/clipped composite; see compositor.py's module docstring for
    the full out-of-bounds policy."""


class TiCompositeWriteError(TiCompositorError):
    """Raised when writing the composited output image to disk fails."""
