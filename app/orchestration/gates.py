"""Human approval gate service (Phase 33 requirement #18; Phase 34
requirement #11/#15: legacy-review compatibility).

`FINAL_MEDIA_APPROVAL` is the only ApprovalGateType actually WRITABLE
through `approve_gate`/`reject_gate` -- it is the one genuinely new gate
this project introduces (no prior ProjectState/review-service concept
existed for "approve the finished, QC'd video"), so a new, durable,
artifact-id-bound `ApprovalDecision` row is the right, non-duplicating
representation for it.

Every other ApprovalGateType (IDEA_APPROVAL, RESEARCH_APPROVAL,
NARRATIVE_APPROVAL, PACKAGING_P0_APPROVAL, SCRIPT_APPROVAL) is backed by
an ALREADY-EXISTING app/review/service.py gate. `gate_decision_for`
reads these read-only, reconstructing an ApprovalDecision-shaped result
from the project's own current ProjectState plus its HumanApproval
history (app/storage/approvals.py) -- never a second decision store.
`approve_gate`/`reject_gate` refuse to write a decision for any of them
(LegacyGateNotWritableError): the real decision must be made by calling
app/review/service.py's own functions directly (that is what Phase 34's
concrete upstream adapters expect a human to have already done, exactly
like every wired renderer already expects `project.state == MVP_COMPLETE`
to have been reached through the real chain).

Reading app/review/service.py's own HumanApproval/ProjectState is safe
for app/orchestration/'s own source-purity rule: neither
app/storage/approvals.py nor app/models/approval.py imports app.llm or
any provider package (confirmed) -- this module stays exactly as
LLM/provider-free as every other file directly under app/orchestration/.

Legacy gate semantics (verified against app/review/service.py directly,
never assumed from names):

- Only TWO of app/review/service.py's sixteen functions ever record a
  true ApprovalStatus.REJECTED: `decide_feasibility(..., GateStatus.REJECT)`
  and `reject_final_script(...)`. Both transition to the terminal
  ProjectState.ARCHIVED. Every other "not approved" action
  (revise_idea, revise_narrative, send_narrative_back_to_research,
  revise_packaging_p0, send_packaging_back_to_research,
  send_script_for_rewrite, send_script_back_to_narrative,
  send_script_back_to_research, revise_final_script,
  send_final_script_back_to_narrative) records ApprovalStatus.REVISE and
  routes the project BACKWARD to an earlier working state -- "redo this
  stage," never a terminal rejection. IDEA_REVIEW, NARRATIVE_REVIEW,
  PACKAGING_P0, and SCRIPT_VERIFICATION therefore have NO representable
  REJECTED outcome at all in the current codebase; a caller invoking a
  redo/revise path on one of these sees the gate return to PENDING
  (`gate_decision_for` returns None) once the corresponding engine
  re-executes and produces a fresh artifact, exactly like an ordinary
  unmet gate.
- Because every engine that could invalidate an already-approved
  artifact only ever runs again after the project has cycled back
  through an EARLIER ProjectState (there is no way to silently swap in a
  new IdeaCandidate/FeasibilityReport/etc. while `project.state` still
  reads as "past" that gate), the project's own linear position in
  app/workflow/states.py's TRANSITIONS graph is sufficient, on its own,
  to answer "is the CURRENT artifact for this gate approved" -- no
  separate per-artifact staleness table is needed for these five gates
  (requirement #9's "use existing foreign-key/artifact-id relationships"
  is satisfied here by ProjectState + HumanApproval, the artifact-id
  equivalent this system already has for legacy gates).
"""

from __future__ import annotations

from datetime import datetime, timezone
from uuid import UUID

from sqlalchemy import Engine

from app.models.common import ApprovalStatus, ProjectState
from app.orchestration.errors import LegacyGateNotWritableError, UnknownApprovalGateError
from app.orchestration.graph import ProductionGraph
from app.orchestration.models import ApprovalDecision, ApprovalDecisionType, ApprovalGateType
from app.storage.approval_decisions import get_latest_approval_decision, save_approval_decision
from app.storage.approvals import get_latest_approval_for_stage
from app.storage.projects import get_project

# ---------------------------------------------------------------------------
# Legacy gate reconstruction (Phase 34)
# ---------------------------------------------------------------------------

_MAINLINE_ORDER = [
    ProjectState.NEW_PROJECT,
    ProjectState.IDEA_DISCOVERY,
    ProjectState.IDEA_REVIEW,
    ProjectState.R0_RESEARCH,
    ProjectState.FEASIBILITY,
    ProjectState.R1_RESEARCH,
    ProjectState.NARRATIVE,
    ProjectState.NARRATIVE_REVIEW,
    ProjectState.PACKAGING_P0,
    ProjectState.SCRIPT,
    ProjectState.SCRIPT_VERIFICATION,
    ProjectState.SCRIPT_REVIEW,
    ProjectState.MVP_COMPLETE,
]
_RANK = {state: index for index, state in enumerate(_MAINLINE_ORDER)}
_ARCHIVED_RANK = len(_MAINLINE_ORDER)  # sentinel: "further along than everything" when not the rejector

# Every state a human could currently be looking at for this gate --
# PENDING while project.state is any of these.
_LEGACY_GATE_AWAITING_STATES: dict[ApprovalGateType, tuple[ProjectState, ...]] = {
    ApprovalGateType.IDEA_APPROVAL: (ProjectState.IDEA_REVIEW,),
    ApprovalGateType.RESEARCH_APPROVAL: (ProjectState.FEASIBILITY,),
    ApprovalGateType.NARRATIVE_APPROVAL: (ProjectState.NARRATIVE_REVIEW,),
    ApprovalGateType.PACKAGING_P0_APPROVAL: (ProjectState.PACKAGING_P0,),
    ApprovalGateType.SCRIPT_APPROVAL: (ProjectState.SCRIPT_VERIFICATION, ProjectState.SCRIPT_REVIEW),
}
# The rank a project must exceed for this gate to be considered cleared.
_LEGACY_GATE_CLEARED_RANK: dict[ApprovalGateType, int] = {
    ApprovalGateType.IDEA_APPROVAL: _RANK[ProjectState.IDEA_REVIEW],
    ApprovalGateType.RESEARCH_APPROVAL: _RANK[ProjectState.FEASIBILITY],
    ApprovalGateType.NARRATIVE_APPROVAL: _RANK[ProjectState.NARRATIVE_REVIEW],
    ApprovalGateType.PACKAGING_P0_APPROVAL: _RANK[ProjectState.PACKAGING_P0],
    ApprovalGateType.SCRIPT_APPROVAL: _RANK[ProjectState.SCRIPT_REVIEW],
}
# The ONE stage whose HumanApproval row could ever show REJECTED for this
# gate -- absent for the four gates with no reject path at all.
_LEGACY_GATE_REJECT_STAGE: dict[ApprovalGateType, ProjectState] = {
    ApprovalGateType.RESEARCH_APPROVAL: ProjectState.FEASIBILITY,
    ApprovalGateType.SCRIPT_APPROVAL: ProjectState.SCRIPT_REVIEW,
}

_LEGACY_GATE_TYPES = frozenset(_LEGACY_GATE_AWAITING_STATES)


def _read_legacy_gate_decision(
    engine: Engine, project_id: UUID, gate_type: ApprovalGateType, subject_artifact_id: UUID | None
) -> ApprovalDecision | None:
    project = get_project(engine, project_id)

    # ScriptVerificationReport (SCRIPT_APPROVAL's own node artifact) has no
    # `id` field at all (a locked contract, confirmed in Phase 32/33) --
    # the runner therefore always calls this with subject_artifact_id=None
    # for that one gate. The real subject under review is the ScriptPlan
    # being verified/approved, so fall back to the project's own current
    # script_plan_id rather than a placeholder.
    if subject_artifact_id is None and gate_type is ApprovalGateType.SCRIPT_APPROVAL:
        subject_artifact_id = project.script_plan_id

    reject_stage = _LEGACY_GATE_REJECT_STAGE.get(gate_type)
    if reject_stage is not None:
        reject_row = get_latest_approval_for_stage(engine, project_id, reject_stage)
        if reject_row is not None and reject_row.status is ApprovalStatus.REJECTED:
            return _synthesize_decision(
                project_id, gate_type, subject_artifact_id, ApprovalDecisionType.REJECTED, reject_row
            )

    awaiting_states = _LEGACY_GATE_AWAITING_STATES[gate_type]
    if project.state in awaiting_states:
        return None  # PENDING -- gate is open, no decision yet

    current_rank = _RANK.get(project.state, _ARCHIVED_RANK)
    if current_rank <= _LEGACY_GATE_CLEARED_RANK[gate_type]:
        return None  # cycled back to an earlier stage -- this gate must be redone, treat as PENDING again

    approved_row = get_latest_approval_for_stage(engine, project_id, awaiting_states[-1])
    return _synthesize_decision(
        project_id, gate_type, subject_artifact_id, ApprovalDecisionType.APPROVED, approved_row
    )


def _synthesize_decision(
    project_id: UUID,
    gate_type: ApprovalGateType,
    subject_artifact_id: UUID | None,
    decision_type: ApprovalDecisionType,
    approval_row,
) -> ApprovalDecision:
    return ApprovalDecision(
        project_id=project_id,
        gate_type=gate_type,
        subject_artifact_id=subject_artifact_id if subject_artifact_id is not None else project_id,
        decision=decision_type,
        decided_at=approval_row.created_at if approval_row is not None else datetime.now(timezone.utc),
        note=approval_row.user_feedback if approval_row is not None else None,
    )


# ---------------------------------------------------------------------------
# Public gate API (unchanged call sites for app/orchestration/runner.py)
# ---------------------------------------------------------------------------


def approve_gate(
    engine: Engine,
    graph: ProductionGraph,
    project_id: UUID,
    gate_type: ApprovalGateType,
    subject_artifact_id: UUID,
    *,
    note: str | None = None,
) -> ApprovalDecision:
    _reject_legacy_write(gate_type)
    return _record_decision(engine, graph, project_id, gate_type, subject_artifact_id, ApprovalDecisionType.APPROVED, note)


def reject_gate(
    engine: Engine,
    graph: ProductionGraph,
    project_id: UUID,
    gate_type: ApprovalGateType,
    subject_artifact_id: UUID,
    *,
    note: str | None = None,
) -> ApprovalDecision:
    _reject_legacy_write(gate_type)
    return _record_decision(engine, graph, project_id, gate_type, subject_artifact_id, ApprovalDecisionType.REJECTED, note)


def gate_decision_for(
    engine: Engine, project_id: UUID, gate_type: ApprovalGateType, subject_artifact_id: UUID | None
) -> ApprovalDecision | None:
    """The most recent decision for this exact subject, or None if no
    decision has ever been recorded for it (Phase 33 gates) / the gate is
    still open (Phase 34 legacy gates)."""
    if gate_type in _LEGACY_GATE_TYPES:
        return _read_legacy_gate_decision(engine, project_id, gate_type, subject_artifact_id)
    return get_latest_approval_decision(engine, project_id, gate_type, subject_artifact_id)


def _reject_legacy_write(gate_type: ApprovalGateType) -> None:
    if gate_type in _LEGACY_GATE_TYPES:
        raise LegacyGateNotWritableError(
            f"{gate_type.value} is backed by the existing app/review/service.py flow -- "
            f"decide it through that service's own functions directly, not approve_gate/reject_gate."
        )


def _record_decision(
    engine: Engine,
    graph: ProductionGraph,
    project_id: UUID,
    gate_type: ApprovalGateType,
    subject_artifact_id: UUID,
    decision_type: ApprovalDecisionType,
    note: str | None,
) -> ApprovalDecision:
    _verify_gate_is_registered(graph, gate_type)
    decision = ApprovalDecision(
        project_id=project_id,
        gate_type=gate_type,
        subject_artifact_id=subject_artifact_id,
        decision=decision_type,
        decided_at=datetime.now(timezone.utc),
        note=note,
    )
    save_approval_decision(engine, decision)
    return decision


def _verify_gate_is_registered(graph: ProductionGraph, gate_type: ApprovalGateType) -> None:
    for node_id in graph.node_ids():
        if graph.get(node_id).gate_after is gate_type:
            return
    raise UnknownApprovalGateError(
        f"No node in the production graph declares gate_after={gate_type.value!r}"
    )
