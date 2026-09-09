"""The approved Motily project-state transition graph.

This module validates topology only: whether a target state is a structurally
allowed destination from a given current state. It does not decide whether an
upstream artifact (e.g. a FeasibilityReport) actually earned that transition
-- that judgment belongs to a future orchestrator, not this graph.
"""

from __future__ import annotations

from app.models.common import ProjectState

TRANSITIONS: dict[ProjectState, frozenset[ProjectState]] = {
    ProjectState.NEW_PROJECT: frozenset({ProjectState.IDEA_DISCOVERY}),
    ProjectState.IDEA_DISCOVERY: frozenset({ProjectState.IDEA_REVIEW}),
    ProjectState.IDEA_REVIEW: frozenset(
        {ProjectState.R0_RESEARCH, ProjectState.IDEA_DISCOVERY}
    ),
    ProjectState.R0_RESEARCH: frozenset({ProjectState.FEASIBILITY}),
    ProjectState.FEASIBILITY: frozenset(
        {ProjectState.R1_RESEARCH, ProjectState.IDEA_DISCOVERY, ProjectState.ARCHIVED}
    ),
    ProjectState.R1_RESEARCH: frozenset({ProjectState.NARRATIVE}),
    ProjectState.NARRATIVE: frozenset(
        {ProjectState.NARRATIVE_REVIEW, ProjectState.R1_RESEARCH}
    ),
    ProjectState.NARRATIVE_REVIEW: frozenset(
        {
            ProjectState.PACKAGING_P0,
            ProjectState.NARRATIVE,
            ProjectState.R1_RESEARCH,
            ProjectState.ARCHIVED,
        }
    ),
    ProjectState.PACKAGING_P0: frozenset(
        {
            ProjectState.SCRIPT,
            ProjectState.NARRATIVE,
            ProjectState.R1_RESEARCH,
        }
    ),
    ProjectState.SCRIPT: frozenset(
        {ProjectState.SCRIPT_VERIFICATION, ProjectState.NARRATIVE, ProjectState.R1_RESEARCH}
    ),
    ProjectState.SCRIPT_VERIFICATION: frozenset(
        {
            ProjectState.SCRIPT_REVIEW,
            ProjectState.SCRIPT,
            ProjectState.NARRATIVE,
            ProjectState.R1_RESEARCH,
        }
    ),
    ProjectState.SCRIPT_REVIEW: frozenset(
        {
            ProjectState.MVP_COMPLETE,
            ProjectState.SCRIPT,
            ProjectState.NARRATIVE,
            ProjectState.ARCHIVED,
        }
    ),
    ProjectState.MVP_COMPLETE: frozenset(),
    ProjectState.ARCHIVED: frozenset(),
}
