"""Phase 35 requirement #27: ProductionService -- the one façade every
command handler in app/cli/main.py calls. It coordinates
ProductionRunner, the legacy/generic approval dispatch, and persistence
reads; it never implements engine business logic itself (every actual
decision still lives in ProductionRunner/the adapters/
app/review/service.py).

Also home to the two explicit alias tables (requirements #5, #21) --
the CLI's only translation from human-typed names to the real
ProductionNodeId/ApprovalGateType values. No fuzzy matching anywhere:
an unrecognized alias is always a hard error (UnknownTargetAliasError/
UnknownGateAliasError), never a best-effort guess.
"""

from __future__ import annotations

from dataclasses import dataclass
from uuid import UUID

from sqlalchemy import Engine

from app.engines.idea.models import DiscoveryMode, IdeaEngineInput
from app.models.common import ProjectState
from app.models.project import Project
from app.orchestration.gates import approve_gate, gate_decision_for, reject_gate
from app.orchestration.graph import ProductionGraph
from app.orchestration.models import (
    ApprovalDecisionType,
    ApprovalGateType,
    ProductionNodeStatus,
    ProductionRun,
    ProductionRunStatus,
)
from app.orchestration.registry import ExecutionContext
from app.orchestration.runner import ProductionRunner
from app.services.production_review import (
    GateNotPendingError,
    GateRejectionNotSupportedError,
    approve_legacy_gate,
    is_legacy_gate,
    reject_legacy_gate,
)
from app.storage.production_runs import get_production_run, list_production_runs_for_project
from app.storage.projects import create_project as _create_project_row
from app.storage.projects import get_project, update_project_state

# ---------------------------------------------------------------------------
# Requirement #5: --target aliases
# ---------------------------------------------------------------------------

TARGET_ALIASES: dict[str, str] = {
    "idea": "IDEA",
    "research": "FEASIBILITY",
    "script": "SCRIPT_VERIFY",
    "plans": "ASSEMBLY_PLAN",
    "video": "VIDEO_RENDER",
    "qc": "MEDIA_QC",
    "final": "MEDIA_QC",
}
DEFAULT_TARGET_ALIAS = "final"

# ---------------------------------------------------------------------------
# Requirement #21: gate aliases
# ---------------------------------------------------------------------------

GATE_ALIASES: dict[str, ApprovalGateType] = {
    "idea": ApprovalGateType.IDEA_APPROVAL,
    "research": ApprovalGateType.RESEARCH_APPROVAL,
    "narrative": ApprovalGateType.NARRATIVE_APPROVAL,
    "packaging": ApprovalGateType.PACKAGING_P0_APPROVAL,
    "script": ApprovalGateType.SCRIPT_APPROVAL,
    "final": ApprovalGateType.FINAL_MEDIA_APPROVAL,
}
_GATE_TYPE_TO_ALIAS = {value: key for key, value in GATE_ALIASES.items()}


class UnknownTargetAliasError(Exception):
    pass


class UnknownGateAliasError(Exception):
    pass


class ProjectNotFoundForCliError(Exception):
    pass


class NoGateCurrentlyPendingError(Exception):
    pass


class GateMismatchError(Exception):
    """Raised when --gate was given but does not match the gate actually
    pending on this run -- requirement #18's disambiguation check."""


def resolve_target_alias(alias: str) -> str:
    try:
        return TARGET_ALIASES[alias]
    except KeyError:
        raise UnknownTargetAliasError(
            f"Unknown --target {alias!r}. Valid targets: {', '.join(sorted(TARGET_ALIASES))}"
        ) from None


def resolve_gate_alias(alias: str) -> ApprovalGateType:
    try:
        return GATE_ALIASES[alias]
    except KeyError:
        raise UnknownGateAliasError(
            f"Unknown --gate {alias!r}. Valid gates: {', '.join(sorted(GATE_ALIASES))}"
        ) from None


def gate_alias_for(gate_type: ApprovalGateType) -> str:
    return _GATE_TYPE_TO_ALIAS[gate_type]


@dataclass(frozen=True)
class ApprovalOutcome:
    gate_type: ApprovalGateType
    decision: ApprovalDecisionType
    subject_artifact_id: str | None


def _find_waiting_node(run: ProductionRun) -> tuple[str, str | None] | None:
    for node_id, record in run.node_states.items():
        if record.status is ProductionNodeStatus.WAITING_APPROVAL:
            return node_id, record.artifact_id
    return None


class ProductionService:
    """One façade per (db_engine, graph, adapters) triple -- built once
    by the composition root (app/bootstrap.py) and reused across every
    CLI command in a single process invocation."""

    def __init__(self, db_engine: Engine, graph: ProductionGraph, adapters: dict[str, object]):
        self.db_engine = db_engine
        self.graph = graph
        self.adapters = adapters
        self._runner = ProductionRunner(db_engine, graph, adapters)

    # -- start / resume ----------------------------------------------------

    def start(
        self,
        project_id: UUID,
        target_alias: str,
        ctx: ExecutionContext,
    ) -> ProductionRun:
        """Creates exactly one new ProductionRun (requirement #28 -- no
        deduplication of user-initiated production requests)."""
        target_node = resolve_target_alias(target_alias)
        return self.start_at_node(project_id, target_node, ctx)

    def start_at_node(self, project_id: UUID, target_node: str, ctx: ExecutionContext) -> ProductionRun:
        """The same as `start`, but takes a raw ProductionNodeId instead
        of a published `--target` alias. Not exposed as a CLI flag (the
        CLI's own `--target` stays limited to the documented alias list,
        requirement #5's "no fuzzy matching" -- an unpublished raw node
        id is not a name a human should need to type). Exists for
        internal/advanced callers that need finer-grained control than
        the seven aliases offer -- e.g. VOICE_PLAN/VISUAL_PLAN/
        ASSEMBLY_PLAN each need to run as their own step, since each
        one's own structured-output schema references a real upstream id
        that only becomes known once the previous step has actually
        executed (no `--target` alias maps to any of the three
        individually, only to their shared endpoint via "plans")."""
        self._ensure_bootstrapped(project_id)
        return self._runner.run_until(project_id, target_node, ctx=ctx)

    def resume(self, run_id: UUID, ctx: ExecutionContext) -> ProductionRun:
        return self._runner.resume(run_id, ctx=ctx)

    def _ensure_bootstrapped(self, project_id: UUID) -> None:
        """NEW_PROJECT -> IDEA_DISCOVERY is a trivial, review-free
        transition every engine test already performs as pure setup (no
        app/review/service.py function exists for it) -- the CLI
        performs the identical bootstrap for a freshly created project,
        never a creative decision."""
        project = get_project(self.db_engine, project_id)
        if project.state is ProjectState.NEW_PROJECT:
            update_project_state(self.db_engine, project_id, ProjectState.IDEA_DISCOVERY)

    def create_project(self, title: str) -> UUID:
        from datetime import datetime, timezone

        created = datetime.now(timezone.utc)
        project = Project(title_internal=title, created_at=created, updated_at=created, state=ProjectState.NEW_PROJECT)
        _create_project_row(self.db_engine, project)
        return project.project_id

    @staticmethod
    def build_initial_idea_input(
        project_id: UUID,
        *,
        brief: str | None,
        domain: str = "physics",
        discovery_mode: str = "open",
        additional_context: str | None = None,
    ) -> IdeaEngineInput:
        mode = DiscoveryMode.OPEN if discovery_mode == "open" else DiscoveryMode.EXPAND
        return IdeaEngineInput(
            project_id=project_id, seed=brief, domain=domain, discovery_mode=mode,
            additional_context=additional_context,
        )

    # -- read-only ----------------------------------------------------------

    def status(self, run_id: UUID) -> ProductionRun:
        return get_production_run(self.db_engine, run_id)

    def list_runs(self, project_id: UUID, limit: int = 20) -> list[ProductionRun]:
        runs = list_production_runs_for_project(self.db_engine, project_id)
        return list(reversed(runs))[:limit]

    # -- approval / rejection -------------------------------------------------

    def approve_pending(self, run_id: UUID, gate_alias: str | None = None, note: str | None = None) -> ApprovalOutcome:
        gate_type, subject_artifact_id, project_id = self._resolve_pending_gate(run_id, gate_alias)
        if is_legacy_gate(gate_type):
            approve_legacy_gate(self.db_engine, project_id, gate_type, note=note)
        else:
            approve_gate(self.db_engine, self.graph, project_id, gate_type, UUID(subject_artifact_id), note=note)
        return ApprovalOutcome(gate_type=gate_type, decision=ApprovalDecisionType.APPROVED, subject_artifact_id=subject_artifact_id)

    def reject_pending(self, run_id: UUID, gate_alias: str | None = None, note: str | None = None) -> ApprovalOutcome:
        gate_type, subject_artifact_id, project_id = self._resolve_pending_gate(run_id, gate_alias)
        if is_legacy_gate(gate_type):
            reject_legacy_gate(self.db_engine, project_id, gate_type, note=note)
        else:
            reject_gate(self.db_engine, self.graph, project_id, gate_type, UUID(subject_artifact_id), note=note)
        return ApprovalOutcome(gate_type=gate_type, decision=ApprovalDecisionType.REJECTED, subject_artifact_id=subject_artifact_id)

    def _resolve_pending_gate(
        self, run_id: UUID, gate_alias: str | None
    ) -> tuple[ApprovalGateType, str | None, UUID]:
        run = self.status(run_id)
        if run.status is not ProductionRunStatus.WAITING_APPROVAL or run.waiting_gate is None:
            raise NoGateCurrentlyPendingError(f"Production run {run_id} has no gate currently pending.")
        gate_type = ApprovalGateType(run.waiting_gate)
        if gate_alias is not None:
            requested = resolve_gate_alias(gate_alias)
            if requested is not gate_type:
                raise GateMismatchError(
                    f"--gate {gate_alias!r} resolves to {requested.value}, but the gate actually "
                    f"pending on this run is {gate_type.value}."
                )
        waiting = _find_waiting_node(run)
        subject_artifact_id = waiting[1] if waiting else None
        return gate_type, subject_artifact_id, run.project_id
