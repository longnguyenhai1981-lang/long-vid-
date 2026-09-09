"""ProductionRunner (Phase 33 requirement #23) -- the synchronous engine
that walks a ProductionGraph's own ancestors_closure(target_node) in its
stable topological order, and for each node: checks its dependencies
already SUCCEEDED this run, reuses a fresh existing artifact or executes
the adapter, then (if the node declares a gate) checks the gate's own
precondition and any recorded human decision before allowing the run to
continue past it.

No background execution (requirement #24): this is one plain, blocking
Python call. No retry loop (requirement #27): a FAILED/BLOCKED node is
recorded once and the closure is not revisited within the same call --
a later call (a fresh run_until(), or run_until(..., run_id=...) again)
independently recomputes every node's status from scratch, so a fixed
upstream problem or a newly recorded approval naturally lets execution
proceed further next time, with no explicit retry bookkeeping needed
here.

Every node in the closure is always visited, even after an earlier node
stops progress -- an unmet dependency simply resolves to BLOCKED
(reason=BLOCKED_DEPENDENCY) rather than being skipped outright, so the
persisted trace (requirement #28) always shows the complete picture:
which nodes actually ran or reused, and which were blocked and why.
"""

from __future__ import annotations

from datetime import datetime, timezone
from uuid import UUID, uuid4

from sqlalchemy import Engine

from app.orchestration.errors import NodeNotWiredError
from app.orchestration.gates import gate_decision_for
from app.orchestration.graph import ProductionGraph
from app.orchestration.models import (
    ApprovalDecisionType,
    BlockedReason,
    NodeRunRecord,
    ProductionNodeStatus,
    ProductionRun,
    ProductionRunStatus,
)
from app.orchestration.registry import ExecutionContext
from app.storage.production_runs import get_production_run, save_production_run

_TERMINAL_STATUS_PRIORITY = [
    ProductionRunStatus.FAILED,
    ProductionRunStatus.BLOCKED,
    ProductionRunStatus.WAITING_APPROVAL,
]


class ProductionRunner:
    """Coordinates existing module adapters through a ProductionGraph.
    Holds no per-call state of its own -- `graph`/`adapters` are the
    fixed topology+adapter set (typically app.orchestration.adapters'
    own build_default_graph()/build_default_adapters()), and every
    run_until() call is an independent, self-contained pass."""

    def __init__(self, db_engine: Engine, graph: ProductionGraph, adapters: dict[str, object]):
        self._db_engine = db_engine
        self._graph = graph
        self._adapters = adapters

    def run_until(
        self, project_id: UUID, target_node: str, *, run_id: UUID | None = None, ctx: ExecutionContext | None = None
    ) -> ProductionRun:
        now = datetime.now(timezone.utc)
        if run_id is not None:
            existing = get_production_run(self._db_engine, run_id)
            created_at = existing.created_at
            run_pk = existing.id
        else:
            created_at = now
            run_pk = None

        if ctx is None:
            ctx = ExecutionContext(db_engine=self._db_engine, project_id=project_id)

        node_states: dict[str, NodeRunRecord] = {}
        closure = self._graph.ancestors_closure(target_node)
        for node_id in closure:
            node_states[node_id] = self._evaluate_node(node_id, node_states, ctx, project_id)

        status, stop_reason, waiting_gate = self._compute_run_outcome(closure, node_states)

        run = ProductionRun(
            id=run_pk if run_pk is not None else uuid4(),
            project_id=project_id,
            target_node=target_node,
            status=status,
            node_states=node_states,
            created_at=created_at,
            updated_at=now,
            started_at=created_at,
            completed_at=now if status != ProductionRunStatus.RUNNING else None,
            stop_reason=stop_reason,
            waiting_gate=waiting_gate,
        )
        save_production_run(self._db_engine, run)
        return run

    def resume(self, run_id: UUID, *, ctx: ExecutionContext | None = None) -> ProductionRun:
        existing = get_production_run(self._db_engine, run_id)
        return self.run_until(existing.project_id, existing.target_node, run_id=run_id, ctx=ctx)

    def _evaluate_node(
        self,
        node_id: str,
        node_states: dict[str, NodeRunRecord],
        ctx: ExecutionContext,
        project_id: UUID,
    ) -> NodeRunRecord:
        definition = self._graph.get(node_id)
        adapter = self._adapters[node_id]

        for dependency in definition.dependencies:
            if node_states[dependency].status != ProductionNodeStatus.SUCCEEDED:
                return NodeRunRecord(
                    node_id=node_id,
                    status=ProductionNodeStatus.BLOCKED,
                    reason=BlockedReason.BLOCKED_DEPENDENCY,
                    message=f"Dependency {dependency!r} did not succeed in this run.",
                )

        current_artifact = adapter.load_current(ctx)
        if current_artifact is not None and adapter.is_fresh(current_artifact, ctx):
            artifact = current_artifact
            executed_this_run = False
            reused_existing_artifact = True
            module_run_id = None
        else:
            try:
                result = adapter.execute(ctx)
            except NodeNotWiredError as exc:
                return NodeRunRecord(
                    node_id=node_id,
                    status=ProductionNodeStatus.BLOCKED,
                    reason=BlockedReason.NODE_NOT_WIRED,
                    message=str(exc),
                )
            except Exception as exc:  # noqa: BLE001 -- the module's own failure, recorded not swallowed
                return NodeRunRecord(
                    node_id=node_id,
                    status=ProductionNodeStatus.FAILED,
                    reason=BlockedReason.NODE_FAILED,
                    message=f"{type(exc).__name__}: {exc}",
                )
            artifact = adapter.load_current(ctx)
            executed_this_run = True
            reused_existing_artifact = False
            module_run_id = result.module_run_id

        # Not every domain model carries an `id` (e.g. ScriptVerificationReport)
        # -- such a node can still be reused/executed and reported SUCCEEDED,
        # it simply has no artifact identity to record or gate against.
        raw_artifact_id = getattr(artifact, "id", None)
        artifact_id_str = str(raw_artifact_id) if raw_artifact_id is not None else None

        if definition.gate_after is None:
            return NodeRunRecord(
                node_id=node_id,
                status=ProductionNodeStatus.SUCCEEDED,
                artifact_id=artifact_id_str,
                module_run_id=module_run_id,
                executed_this_run=executed_this_run,
                reused_existing_artifact=reused_existing_artifact,
            )

        if not adapter.gate_ok(artifact):
            not_ready_reason = getattr(adapter, "gate_not_ready_reason", BlockedReason.QC_NOT_READY)
            return NodeRunRecord(
                node_id=node_id,
                status=ProductionNodeStatus.BLOCKED,
                artifact_id=artifact_id_str,
                module_run_id=module_run_id,
                executed_this_run=executed_this_run,
                reused_existing_artifact=reused_existing_artifact,
                reason=not_ready_reason,
                message="This node's own quality/business gate precondition was not met.",
            )

        decision = gate_decision_for(self._db_engine, project_id, definition.gate_after, raw_artifact_id)
        if decision is None:
            return NodeRunRecord(
                node_id=node_id,
                status=ProductionNodeStatus.WAITING_APPROVAL,
                artifact_id=artifact_id_str,
                module_run_id=module_run_id,
                executed_this_run=executed_this_run,
                reused_existing_artifact=reused_existing_artifact,
                reason=BlockedReason.WAITING_GATE,
                message=f"Waiting for a human {definition.gate_after.value} decision on this artifact.",
            )
        if decision.decision == ApprovalDecisionType.REJECTED:
            return NodeRunRecord(
                node_id=node_id,
                status=ProductionNodeStatus.BLOCKED,
                artifact_id=artifact_id_str,
                module_run_id=module_run_id,
                executed_this_run=executed_this_run,
                reused_existing_artifact=reused_existing_artifact,
                reason=BlockedReason.GATE_REJECTED,
                message=decision.note or f"{definition.gate_after.value} was rejected for this artifact.",
            )

        return NodeRunRecord(
            node_id=node_id,
            status=ProductionNodeStatus.SUCCEEDED,
            artifact_id=artifact_id_str,
            module_run_id=module_run_id,
            executed_this_run=executed_this_run,
            reused_existing_artifact=reused_existing_artifact,
        )

    _NODE_TO_RUN_STATUS = {
        ProductionNodeStatus.FAILED: ProductionRunStatus.FAILED,
        ProductionNodeStatus.BLOCKED: ProductionRunStatus.BLOCKED,
        ProductionNodeStatus.WAITING_APPROVAL: ProductionRunStatus.WAITING_APPROVAL,
    }

    def _compute_run_outcome(
        self, closure: list[str], node_states: dict[str, NodeRunRecord]
    ) -> tuple[ProductionRunStatus, str | None, str | None]:
        """The FIRST non-SUCCEEDED node in topological order determines
        both the run's own terminal status and its stop reason -- a
        node BLOCKED only as a cascading consequence of an earlier
        WAITING_APPROVAL/FAILED node (via BLOCKED_DEPENDENCY) must never
        override that earlier, actual cause (requirement #28/#29)."""
        for node_id in closure:
            record = node_states[node_id]
            if record.status == ProductionNodeStatus.SUCCEEDED:
                continue
            reason = record.reason.value if record.reason is not None else record.status.value
            waiting_gate = None
            if record.status == ProductionNodeStatus.WAITING_APPROVAL:
                waiting_gate = self._graph.get(node_id).gate_after.value
            run_status = self._NODE_TO_RUN_STATUS[record.status]
            return run_status, f"{node_id}:{reason}", waiting_gate
        return ProductionRunStatus.SUCCEEDED, None, None
