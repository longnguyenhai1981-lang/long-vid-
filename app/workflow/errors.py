"""Domain errors for the project state machine."""

from __future__ import annotations

from app.models.common import ProjectState


class InvalidStateTransitionError(Exception):
    """Raised when a requested project state transition is not in the approved graph."""

    def __init__(self, current: ProjectState, target: ProjectState):
        self.current = current
        self.target = target
        super().__init__(
            f"Cannot transition project from {current.value} to {target.value}"
        )
