"""Visual-layer domain errors.

Two deliberately separate hierarchies, mirroring app/audio/errors.py: a
visual provider call failing is a different kind of problem from a
filesystem write failing, and callers must be able to tell them apart
(e.g. retry a provider error, but never retry a write error).
VisualError and VisualStorageError share no common base for this reason.
"""

from __future__ import annotations


class VisualError(Exception):
    """Base class for all visual-provider-layer errors."""


class VisualProviderError(VisualError):
    """The provider invocation itself failed (network/API/provider-side
    error). Retryable -- the renderer may retry a request that fails with
    this error, up to its configured bound."""


class VisualOutputError(VisualError):
    """The provider returned a response that failed to satisfy the
    VisualRenderResponse contract (e.g. malformed/empty asset). Not
    retried."""


class VisualProviderUnavailableError(VisualError):
    """Raised when no concrete VisualProvider is configured/available for
    a VisualRenderRequest's media_type (Phase 20's production routing
    policy -- e.g. no real diagram provider exists yet). Deliberately NOT
    a VisualProviderError subclass: this is a configuration/routing
    decision, not a transient provider failure, so VisualRenderer's retry
    loop (which only retries VisualProviderError) never wastes attempts
    on it."""


class VisualStorageError(Exception):
    """Base class for all visual-filesystem-layer errors. Deliberately NOT
    a VisualError subclass -- a storage failure is never a provider
    failure."""


class VisualPathError(VisualStorageError):
    """Raised when a requested relative path is blank, absolute, or
    escapes the visual-output root (e.g. via '..')."""


class VisualWriteError(VisualStorageError):
    """Raised when writing visual asset bytes to disk fails."""
