"""Phase 35 requirement #20: the narrow command-layer approval dispatcher
for the five legacy, review-service-backed gates.

Phase 34 deliberately made `app/orchestration/gates.py::approve_gate`/
`reject_gate` refuse these five (`LegacyGateNotWritableError`) --  the
real decision must be made through `app/review/service.py`'s own
functions. This module is that mapping, living outside
`app/orchestration/` (it imports nothing from `app.llm`, but conceptually
belongs with the CLI/service layer, not the generic orchestration core).

Every function here does the MINIMUM necessary: verify the project is
actually sitting at the ProjectState this gate is offered from, then
call the one exact `app/review/service.py` function that represents
"approve"/"reject" for that gate. No creative logic, no re-implemented
validation -- the review service itself still performs every one of its
own preconditions (freshness checks, PASS-status requirements, etc.).
"""

from __future__ import annotations

from uuid import UUID

from sqlalchemy import Engine

from app.models.common import GateStatus, ProjectState
from app.orchestration.models import ApprovalGateType
from app.review.service import (
    accept_script_verification,
    approve_final_script,
    approve_idea,
    approve_narrative,
    approve_packaging_p0,
    decide_feasibility,
    reject_final_script,
)
from app.storage.projects import get_project


class GateNotPendingError(Exception):
    """Raised when the project's current ProjectState does not match
    what this gate expects to be offered from -- e.g. approving
    NARRATIVE_APPROVAL when the project has already moved on, or never
    reached NARRATIVE_REVIEW in the first place."""


class GateRejectionNotSupportedError(Exception):
    """Raised for a gate that has no reject path in
    app/review/service.py at all (IDEA_APPROVAL, NARRATIVE_APPROVAL,
    PACKAGING_P0_APPROVAL), or where reject is only reachable via a
    business-block state that never reaches WAITING_APPROVAL in the
    first place (RESEARCH_APPROVAL -- decide_feasibility(REJECT) would
    raise FeasibilityDecisionMismatchError against a PASSing report, and
    a project only ever reaches RESEARCH_APPROVAL's own WAITING_APPROVAL
    when the report IS PASS, since FeasibilityAdapter.gate_ok blocks the
    gate from being offered at all otherwise)."""


_LEGACY_GATE_TYPES = frozenset(
    {
        ApprovalGateType.IDEA_APPROVAL,
        ApprovalGateType.RESEARCH_APPROVAL,
        ApprovalGateType.NARRATIVE_APPROVAL,
        ApprovalGateType.PACKAGING_P0_APPROVAL,
        ApprovalGateType.SCRIPT_APPROVAL,
    }
)


def is_legacy_gate(gate_type: ApprovalGateType) -> bool:
    return gate_type in _LEGACY_GATE_TYPES


def approve_legacy_gate(
    engine: Engine, project_id: UUID, gate_type: ApprovalGateType, *, note: str | None = None
) -> None:
    if gate_type is ApprovalGateType.IDEA_APPROVAL:
        _require_state(engine, project_id, ProjectState.IDEA_REVIEW, gate_type)
        approve_idea(engine, project_id, feedback=note)
        return

    if gate_type is ApprovalGateType.RESEARCH_APPROVAL:
        _require_state(engine, project_id, ProjectState.FEASIBILITY, gate_type)
        decide_feasibility(engine, project_id, GateStatus.PASS, feedback=note)
        return

    if gate_type is ApprovalGateType.NARRATIVE_APPROVAL:
        _require_state(engine, project_id, ProjectState.NARRATIVE_REVIEW, gate_type)
        approve_narrative(engine, project_id, feedback=note)
        return

    if gate_type is ApprovalGateType.PACKAGING_P0_APPROVAL:
        _require_state(engine, project_id, ProjectState.PACKAGING_P0, gate_type)
        approve_packaging_p0(engine, project_id, feedback=note)
        return

    if gate_type is ApprovalGateType.SCRIPT_APPROVAL:
        # SCRIPT_APPROVAL spans two real sequential states with no engine
        # work between them (requirement #17/#20 note in
        # app/orchestration/models.py). Each CLI "approve" call performs
        # EXACTLY ONE real app/review/service.py mutation -- one explicit,
        # auditable HumanApproval row per call (requirement #29) -- never
        # silently chaining both steps from a single command. The gate
        # therefore still reads WAITING_APPROVAL after the first approve
        # (project has merely moved from SCRIPT_VERIFICATION to
        # SCRIPT_REVIEW); a second approve+resume cycle is required to
        # fully clear it.
        project = get_project(engine, project_id)
        if project.state is ProjectState.SCRIPT_VERIFICATION:
            accept_script_verification(engine, project_id, feedback=note)
            return
        if project.state is ProjectState.SCRIPT_REVIEW:
            approve_final_script(engine, project_id, feedback=note)
            return
        raise GateNotPendingError(
            f"SCRIPT_APPROVAL is not currently pending for project {project_id} "
            f"(state={project.state.value})."
        )

    raise ValueError(
        f"{gate_type.value} is not a legacy gate -- use app.orchestration.gates.approve_gate "
        f"for FINAL_MEDIA_APPROVAL instead."
    )


def reject_legacy_gate(
    engine: Engine, project_id: UUID, gate_type: ApprovalGateType, *, note: str | None = None
) -> None:
    if gate_type is ApprovalGateType.SCRIPT_APPROVAL:
        project = get_project(engine, project_id)
        if project.state is ProjectState.SCRIPT_REVIEW:
            reject_final_script(engine, project_id, feedback=note)
            return
        if project.state is ProjectState.SCRIPT_VERIFICATION:
            raise GateRejectionNotSupportedError(
                "SCRIPT_APPROVAL can only be rejected once script verification has been "
                "accepted (project state SCRIPT_REVIEW) -- approve first to advance past "
                "verification, then reject if the final script itself should not proceed."
            )
        raise GateNotPendingError(
            f"SCRIPT_APPROVAL is not currently pending for project {project_id} "
            f"(state={project.state.value})."
        )

    if gate_type is ApprovalGateType.RESEARCH_APPROVAL:
        raise GateRejectionNotSupportedError(
            "RESEARCH_APPROVAL cannot be rejected through this command: by the time this "
            "gate is WAITING_APPROVAL, FeasibilityReport.status is already PASS (a REFRAME/"
            "REJECT report blocks the gate from ever being offered, as FEASIBILITY_NOT_PASSED) "
            "-- decide_feasibility(REJECT) would be rejected by the review service itself as "
            "a status mismatch."
        )

    if gate_type in (ApprovalGateType.IDEA_APPROVAL, ApprovalGateType.NARRATIVE_APPROVAL, ApprovalGateType.PACKAGING_P0_APPROVAL):
        raise GateRejectionNotSupportedError(
            f"{gate_type.value} has no reject path in app/review/service.py -- only "
            f"revise/send-back functions exist for this stage, which redo the stage rather "
            f"than terminate the project."
        )

    raise ValueError(
        f"{gate_type.value} is not a legacy gate -- use app.orchestration.gates.reject_gate "
        f"for FINAL_MEDIA_APPROVAL instead."
    )


def _require_state(engine: Engine, project_id: UUID, expected: ProjectState, gate_type: ApprovalGateType) -> None:
    project = get_project(engine, project_id)
    if project.state is not expected:
        raise GateNotPendingError(
            f"{gate_type.value} is not currently pending for project {project_id} "
            f"(expected state {expected.value}, got {project.state.value})."
        )
