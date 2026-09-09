"""Typed contracts for Phase 33's deterministic production orchestration
layer.

ProductionRun/NodeRunRecord/ApprovalDecision are plain, dependency-free
data contracts -- no subprocess, no LLM, no provider. This module (and
every other file directly under app/orchestration/) is confirmed free of
app.llm/provider imports by tests/test_orchestration_purity.py
(requirement #30) -- adapters that need to invoke an LLM-based engine
live in app/orchestration/adapters.py, which is exempt from that source-
purity test (see that module's own docstring for why), but no such
adapter exists in this phase: every adapter Phase 33 actually wires
(app/orchestration/adapters.py) delegates to a renderer/builder already
confirmed LLM-free.

ProductionRun is a cross-module WORKFLOW record -- distinct from
ModuleRun (app/models/module_run.py), which remains exactly what it
always was: one module's own single execution record. A ProductionRun's
own `node_states` values reference a `module_run_id` where the
underlying adapter produced one, but ProductionRun never replaces or
duplicates ModuleRun's own persistence.
"""

from __future__ import annotations

from datetime import datetime
from enum import Enum
from uuid import UUID, uuid4

from pydantic import Field, model_validator

from app.models.common import MotilyModel, non_blank


class ProductionNodeStatus(str, Enum):
    """One node's own execution status within one ProductionRun.
    Deliberately NOT derived from ModuleRunStatus alone (requirement #6)
    -- a node can be SUCCEEDED via a fresh, REUSED artifact without any
    ModuleRun executing this run at all, and a node whose module executed
    successfully can still stop the run at WAITING_APPROVAL."""

    PENDING = "PENDING"
    """Not yet evaluated in this run."""
    BLOCKED = "BLOCKED"
    """Cannot run: a dependency is unsatisfied, a gate was rejected, QC
    is not ready, or an upstream artifact is stale. See `reason`."""
    READY = "READY"
    """Dependencies and gates are satisfied; about to execute or reuse."""
    RUNNING = "RUNNING"
    """The adapter's own execute() is in progress (transient; a
    NodeRunRecord read back after run_until() returns is never left in
    this state -- it always resolves to SUCCEEDED/FAILED)."""
    SUCCEEDED = "SUCCEEDED"
    """The node's artifact is current -- either freshly executed this
    run (`executed_this_run=True`) or reused from a prior run
    (`reused_existing_artifact=True`)."""
    FAILED = "FAILED"
    """The adapter's own execute() raised -- the module itself failed."""
    WAITING_APPROVAL = "WAITING_APPROVAL"
    """The node's artifact exists and its own gate_after check passed
    (e.g. QC permits review), but no APPROVED ApprovalDecision exists yet
    for this exact artifact -- production stops here until a human
    decides."""
    SKIPPED = "SKIPPED"
    """Reserved for a future optional-branch node; unused by any node
    Phase 33 registers today (every registered node is on the single
    required path to its own possible targets)."""


class BlockedReason(str, Enum):
    """Stable, machine-readable diagnostic codes (requirement #29) --
    never only a free-text message."""

    BLOCKED_DEPENDENCY = "BLOCKED_DEPENDENCY"
    WAITING_GATE = "WAITING_GATE"
    GATE_REJECTED = "GATE_REJECTED"
    QC_NOT_READY = "QC_NOT_READY"
    STALE_UPSTREAM = "STALE_UPSTREAM"
    NODE_FAILED = "NODE_FAILED"
    NODE_NOT_WIRED = "NODE_NOT_WIRED"
    FEASIBILITY_NOT_PASSED = "FEASIBILITY_NOT_PASSED"
    """Phase 34: FeasibilityReport.status is REFRAME/REJECT -- the real
    human decide_feasibility() gate is never even offered until the
    project's idea/research is reworked through the existing engine +
    app/review/service.py flow (mirrors QC_NOT_READY's own "block before
    offering the gate" semantics)."""
    SCRIPT_VERIFICATION_NOT_PASSED = "SCRIPT_VERIFICATION_NOT_PASSED"
    """Phase 34: ScriptVerificationReport.status is not PASS -- VOICE_PLAN/
    VISUAL_PLAN and the rest of the downstream chain must never proceed on
    an unverified script."""
    PACKAGING_RISK_TOO_HIGH = "PACKAGING_RISK_TOO_HIGH"
    """Phase 34: PackagingPrototype.risk_of_misleading is HIGH -- the real
    approve_packaging_p0() itself refuses a HIGH-risk prototype
    (PackagingRiskTooHighError), so the gate is never offered."""


class NodeRunRecord(MotilyModel):
    """One node's own trace within one ProductionRun -- compact by
    design (requirement #28): enough to answer which nodes ran, which
    were reused, which were blocked and why, and which artifact/
    ModuleRun each node produced or reused."""

    node_id: str
    status: ProductionNodeStatus
    artifact_id: str | None = None
    module_run_id: UUID | None = None
    executed_this_run: bool = False
    reused_existing_artifact: bool = False
    reason: BlockedReason | None = None
    message: str | None = None

    @model_validator(mode="after")
    def _check_invariants(self) -> "NodeRunRecord":
        non_blank(self.node_id, "node_id")
        if self.executed_this_run and self.reused_existing_artifact:
            raise ValueError("a node cannot be both executed_this_run and reused_existing_artifact")
        return self


class ProductionRunStatus(str, Enum):
    RUNNING = "RUNNING"
    """Transient; never the final status read back from storage after
    run_until() returns -- it always resolves to one of the four below."""
    WAITING_APPROVAL = "WAITING_APPROVAL"
    SUCCEEDED = "SUCCEEDED"
    FAILED = "FAILED"
    BLOCKED = "BLOCKED"


class ProductionRun(MotilyModel):
    """A cross-module orchestration workflow -- see this module's own
    docstring for how this relates to ModuleRun. Holds only orchestration
    metadata and references; no artifact payload is ever duplicated
    here."""

    id: UUID = Field(default_factory=uuid4)
    project_id: UUID
    target_node: str
    status: ProductionRunStatus
    node_states: dict[str, NodeRunRecord] = Field(default_factory=dict)
    created_at: datetime
    updated_at: datetime
    started_at: datetime | None = None
    completed_at: datetime | None = None
    stop_reason: str | None = None
    waiting_gate: str | None = None

    @model_validator(mode="after")
    def _check_invariants(self) -> "ProductionRun":
        non_blank(self.target_node, "target_node")
        if self.created_at.tzinfo is None:
            raise ValueError("created_at must be timezone-aware")
        if self.updated_at.tzinfo is None:
            raise ValueError("updated_at must be timezone-aware")
        return self


class ApprovalGateType(str, Enum):
    """Requirement #12 (Phase 33) / precise remapping (Phase 34): every
    member below except FINAL_MEDIA_APPROVAL corresponds to one exact,
    already-established app/review/service.py gate -- verified against
    that file directly rather than assumed from names. Phase 33 declared
    only a 3-way IDEA/RESEARCH/SCRIPT vocabulary and left it unenforced
    (project.state == MVP_COMPLETE stood in as proof the whole chain
    passed); Phase 34 enforces each one individually so the runner can
    stop and resume at the REAL point a human decision is pending,
    instead of skipping straight through to MVP_COMPLETE. These gates are
    read-only from the orchestrator's perspective: `gate_decision_for`
    (app/orchestration/gates.py) reconstructs APPROVED/REJECTED/pending
    from the EXISTING ProjectState + HumanApproval rows -- `approve_gate`/
    `reject_gate` refuse to accept a decision for any of these (only
    FINAL_MEDIA_APPROVAL is writable through this module), since the
    real decision must be made through app/review/service.py's own
    functions, never duplicated here.

    - IDEA_APPROVAL -> ProjectState.IDEA_REVIEW (approve_idea/revise_idea)
    - RESEARCH_APPROVAL -> ProjectState.FEASIBILITY (decide_feasibility)
      -- the real human research-continuation decision; there is no
      separate "approve R0/R1 research" gate in the existing system, R1
      research runs automatically once feasibility passes
    - NARRATIVE_APPROVAL -> ProjectState.NARRATIVE_REVIEW (approve_narrative/
      revise_narrative/send_narrative_back_to_research)
    - PACKAGING_P0_APPROVAL -> ProjectState.PACKAGING_P0 (approve_packaging_p0/
      revise_packaging_p0/send_packaging_back_to_research)
    - SCRIPT_APPROVAL -> spans TWO sequential real states with no engine
      work in between, collapsed into one gate on the SCRIPT_VERIFY node:
      ProjectState.SCRIPT_VERIFICATION (accept_script_verification/
      send_script_for_rewrite/send_script_back_to_*) THEN
      ProjectState.SCRIPT_REVIEW (approve_final_script/revise_final_script/
      send_final_script_back_to_narrative/reject_final_script) -- both
      must pass; reaching MVP_COMPLETE is conclusive proof they did
    - FINAL_MEDIA_APPROVAL -> Phase 33's own new gate (no prior
      ProjectState/review-service concept existed for "approve the
      finished, QC'd video"); unchanged, still the only APPROVED/REJECTED
      decision actually recorded via the ApprovalDecision table

    Only IDEA_REVIEW's revise_idea, PACKAGING_P0's two redo functions, and
    NARRATIVE_REVIEW's/SCRIPT_VERIFICATION's redo functions exist for
    their stages -- none of those four gates has a true reject path in
    the current codebase (only `decide_feasibility(REJECT)` and
    `reject_final_script` ever reach ProjectState.ARCHIVED). See
    app/orchestration/gates.py's own module docstring for exactly how
    that asymmetry is read back."""

    IDEA_APPROVAL = "IDEA_APPROVAL"
    RESEARCH_APPROVAL = "RESEARCH_APPROVAL"
    NARRATIVE_APPROVAL = "NARRATIVE_APPROVAL"
    PACKAGING_P0_APPROVAL = "PACKAGING_P0_APPROVAL"
    SCRIPT_APPROVAL = "SCRIPT_APPROVAL"
    FINAL_MEDIA_APPROVAL = "FINAL_MEDIA_APPROVAL"


class ApprovalDecisionType(str, Enum):
    APPROVED = "APPROVED"
    REJECTED = "REJECTED"


class ApprovalDecision(MotilyModel):
    """Binds one human APPROVED/REJECTED decision to an EXACT subject
    artifact id (requirement #13) -- distinct from the existing
    ApprovalRow/HumanApproval mechanism (app/review/service.py), which
    drives ProjectState transitions for the earlier creative-review gates
    and carries no artifact-id binding. Multiple decisions may accumulate
    for the same (project_id, gate_type, subject_artifact_id) over time
    (a human may reconsider) -- the MOST RECENT one (by insertion order)
    is authoritative; this is a deliberate, documented duplicate policy
    (requirement #33), never ambiguous. If the subject artifact is later
    replaced (a new id), any prior decision for the OLD id simply never
    matches the new one -- approval never carries forward implicitly
    across artifact replacement (requirement #14)."""

    id: UUID = Field(default_factory=uuid4)
    project_id: UUID
    gate_type: ApprovalGateType
    subject_artifact_id: UUID
    decision: ApprovalDecisionType
    decided_at: datetime
    note: str | None = None

    @model_validator(mode="after")
    def _check_invariants(self) -> "ApprovalDecision":
        if self.decided_at.tzinfo is None:
            raise ValueError("decided_at must be timezone-aware")
        return self
