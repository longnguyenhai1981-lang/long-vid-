from __future__ import annotations

import pytest

from app.models.common import ProjectState
from app.workflow.errors import InvalidStateTransitionError
from app.workflow.states import TRANSITIONS
from app.workflow.transitions import can_transition, validate_transition

# Phase 9 contract migration: NARRATIVE_REVIEW's forward edge now goes to the
# new PACKAGING_P0 state (not directly to SCRIPT), and PACKAGING_P0 -> SCRIPT
# is the new final forward edge before scripting. See docs/TECHNICAL_SPEC_v0.1.md.
FORWARD_TRANSITIONS = [
    (ProjectState.NEW_PROJECT, ProjectState.IDEA_DISCOVERY),
    (ProjectState.IDEA_DISCOVERY, ProjectState.IDEA_REVIEW),
    (ProjectState.IDEA_REVIEW, ProjectState.R0_RESEARCH),
    (ProjectState.R0_RESEARCH, ProjectState.FEASIBILITY),
    (ProjectState.FEASIBILITY, ProjectState.R1_RESEARCH),
    (ProjectState.R1_RESEARCH, ProjectState.NARRATIVE),
    (ProjectState.NARRATIVE, ProjectState.NARRATIVE_REVIEW),
    (ProjectState.NARRATIVE_REVIEW, ProjectState.PACKAGING_P0),
    (ProjectState.PACKAGING_P0, ProjectState.SCRIPT),
    (ProjectState.SCRIPT, ProjectState.SCRIPT_VERIFICATION),
    (ProjectState.SCRIPT_VERIFICATION, ProjectState.SCRIPT_REVIEW),
    (ProjectState.SCRIPT_REVIEW, ProjectState.MVP_COMPLETE),
]

RECOVERY_TRANSITIONS = [
    (ProjectState.IDEA_REVIEW, ProjectState.IDEA_DISCOVERY),
    (ProjectState.FEASIBILITY, ProjectState.IDEA_DISCOVERY),
    (ProjectState.FEASIBILITY, ProjectState.ARCHIVED),
    (ProjectState.NARRATIVE, ProjectState.R1_RESEARCH),
    (ProjectState.NARRATIVE_REVIEW, ProjectState.NARRATIVE),
    (ProjectState.NARRATIVE_REVIEW, ProjectState.R1_RESEARCH),
    (ProjectState.NARRATIVE_REVIEW, ProjectState.ARCHIVED),
    (ProjectState.PACKAGING_P0, ProjectState.NARRATIVE),
    (ProjectState.PACKAGING_P0, ProjectState.R1_RESEARCH),
    (ProjectState.SCRIPT, ProjectState.NARRATIVE),
    (ProjectState.SCRIPT, ProjectState.R1_RESEARCH),
    (ProjectState.SCRIPT_VERIFICATION, ProjectState.SCRIPT),
    (ProjectState.SCRIPT_VERIFICATION, ProjectState.NARRATIVE),
    (ProjectState.SCRIPT_VERIFICATION, ProjectState.R1_RESEARCH),
    (ProjectState.SCRIPT_REVIEW, ProjectState.SCRIPT),
    (ProjectState.SCRIPT_REVIEW, ProjectState.NARRATIVE),
    (ProjectState.SCRIPT_REVIEW, ProjectState.ARCHIVED),
]

INVALID_TRANSITIONS = [
    (ProjectState.NEW_PROJECT, ProjectState.SCRIPT),
    (ProjectState.IDEA_DISCOVERY, ProjectState.R1_RESEARCH),
    (ProjectState.R0_RESEARCH, ProjectState.SCRIPT),
    # The old direct forward edge, removed by the Phase 9 migration: approving
    # a narrative must route through PACKAGING_P0, never straight to SCRIPT.
    (ProjectState.NARRATIVE_REVIEW, ProjectState.SCRIPT),
    # PACKAGING_P0 intentionally has no route to IDEA_DISCOVERY or ARCHIVED --
    # recovery from packaging stays local (NARRATIVE or R1_RESEARCH only).
    (ProjectState.PACKAGING_P0, ProjectState.IDEA_DISCOVERY),
    (ProjectState.PACKAGING_P0, ProjectState.ARCHIVED),
    (ProjectState.MVP_COMPLETE, ProjectState.NEW_PROJECT),
    (ProjectState.MVP_COMPLETE, ProjectState.ARCHIVED),
    (ProjectState.MVP_COMPLETE, ProjectState.SCRIPT_REVIEW),
    (ProjectState.ARCHIVED, ProjectState.NEW_PROJECT),
    (ProjectState.ARCHIVED, ProjectState.MVP_COMPLETE),
    (ProjectState.ARCHIVED, ProjectState.IDEA_DISCOVERY),
]


@pytest.mark.parametrize("current,target", FORWARD_TRANSITIONS)
def test_forward_transitions_allowed(current, target):
    assert can_transition(current, target) is True
    validate_transition(current, target)


@pytest.mark.parametrize("current,target", RECOVERY_TRANSITIONS)
def test_recovery_transitions_allowed(current, target):
    assert can_transition(current, target) is True
    validate_transition(current, target)


@pytest.mark.parametrize("current,target", INVALID_TRANSITIONS)
def test_invalid_transitions_rejected(current, target):
    assert can_transition(current, target) is False
    with pytest.raises(InvalidStateTransitionError):
        validate_transition(current, target)


def test_terminal_states_have_no_outgoing_transitions():
    assert TRANSITIONS[ProjectState.MVP_COMPLETE] == frozenset()
    assert TRANSITIONS[ProjectState.ARCHIVED] == frozenset()


def test_invalid_transition_error_carries_states():
    with pytest.raises(InvalidStateTransitionError) as exc_info:
        validate_transition(ProjectState.NEW_PROJECT, ProjectState.MVP_COMPLETE)
    assert exc_info.value.current is ProjectState.NEW_PROJECT
    assert exc_info.value.target is ProjectState.MVP_COMPLETE


def test_project_state_includes_packaging_p0():
    assert ProjectState("PACKAGING_P0") is ProjectState.PACKAGING_P0


def test_narrative_review_no_longer_has_direct_script_edge():
    assert ProjectState.SCRIPT not in TRANSITIONS[ProjectState.NARRATIVE_REVIEW]
    assert ProjectState.PACKAGING_P0 in TRANSITIONS[ProjectState.NARRATIVE_REVIEW]


def test_packaging_p0_outgoing_edges_exactly():
    assert TRANSITIONS[ProjectState.PACKAGING_P0] == frozenset(
        {ProjectState.SCRIPT, ProjectState.NARRATIVE, ProjectState.R1_RESEARCH}
    )
