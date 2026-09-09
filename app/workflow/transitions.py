"""Deterministic transition validation API for ProjectState."""

from __future__ import annotations

from app.models.common import ProjectState
from app.workflow.errors import InvalidStateTransitionError
from app.workflow.states import TRANSITIONS


def can_transition(current: ProjectState, target: ProjectState) -> bool:
    """Return whether target is a structurally allowed destination from current."""
    return target in TRANSITIONS.get(current, frozenset())


def validate_transition(current: ProjectState, target: ProjectState) -> None:
    """Return normally if the transition is allowed; raise otherwise."""
    if not can_transition(current, target):
        raise InvalidStateTransitionError(current, target)
