"""Shared engine-layer errors.

Not a BaseEngine -- just the error type every content engine needs for its
own state-precondition check before it may run.
"""

from __future__ import annotations


class EngineStateError(Exception):
    """Raised when an engine is run against a project in the wrong state."""
