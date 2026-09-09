"""Phase 33 focused tests: ProductionRunner against fake NodeAdapters --
happy path/idempotency, stop-at-gate/resume, rejection, stale-artifact
invalidation, execution failure, and QC gate-precondition semantics
(requirements #34-#39).

FakeAdapter is a minimal, deterministic, in-memory NodeAdapter: it
"produces" an artifact by incrementing its own counter and storing a
simple object with an `id`/`upstream_id` pair, letting is_fresh() do a
real (if trivial) freshness comparison exactly like a real adapter would
against its own upstream artifact ids -- never a hardcoded True/False.
"""

from __future__ import annotations

from types import SimpleNamespace
from uuid import UUID, uuid4

import pytest

from app.orchestration.gates import approve_gate, reject_gate
from app.orchestration.graph import ProductionGraph, ProductionNodeDefinition
from app.orchestration.models import (
    ApprovalGateType,
    BlockedReason,
    ProductionNodeStatus,
    ProductionRunStatus,
)
from app.orchestration.registry import ExecutionContext, NodeExecutionResult
from app.orchestration.runner import ProductionRunner


class FakeAdapter:
    """A single-artifact-store fake: `store` is a dict shared across
    every FakeAdapter in one graph, keyed by node_id, so an adapter can
    look up its own dependency's current artifact by name."""

    def __init__(self, node_id: str, upstream: str | None, store: dict, *, gate_ok: bool = True, fails: bool = False):
        self.node_id = node_id
        self.upstream = upstream
        self.store = store
        self.execute_count = 0
        self._gate_ok = gate_ok
        self._fails = fails

    def load_current(self, ctx: ExecutionContext):
        return self.store.get(self.node_id)

    def is_fresh(self, artifact, ctx: ExecutionContext) -> bool:
        if self.upstream is None:
            return True
        upstream_artifact = self.store.get(self.upstream)
        if upstream_artifact is None:
            return False
        return artifact.upstream_id == upstream_artifact.id

    def execute(self, ctx: ExecutionContext) -> NodeExecutionResult:
        self.execute_count += 1
        if self._fails:
            raise RuntimeError(f"{self.node_id} deliberately failed")
        upstream_artifact = self.store.get(self.upstream) if self.upstream else None
        artifact = SimpleNamespace(
            id=uuid4(), upstream_id=upstream_artifact.id if upstream_artifact else None
        )
        self.store[self.node_id] = artifact
        return NodeExecutionResult(artifact_id=artifact.id, module_run_id=None)

    def gate_ok(self, artifact) -> bool:
        return self._gate_ok


def _ctx(engine) -> ExecutionContext:
    return ExecutionContext(db_engine=engine, project_id=uuid4())


# ---------------------------------------------------------------------------
# Happy path / idempotency (requirement #34/#25)
# ---------------------------------------------------------------------------


def test_linear_chain_executes_once_each(tmp_path):
    from app.storage.database import init_database

    engine = init_database(tmp_path / "db.sqlite")
    store: dict = {}
    graph = ProductionGraph(
        [
            ProductionNodeDefinition(node_id="A"),
            ProductionNodeDefinition(node_id="B", dependencies=("A",)),
            ProductionNodeDefinition(node_id="C", dependencies=("B",)),
        ]
    )
    adapters = {
        "A": FakeAdapter("A", None, store),
        "B": FakeAdapter("B", "A", store),
        "C": FakeAdapter("C", "B", store),
    }
    runner = ProductionRunner(engine, graph, adapters)
    ctx = _ctx(engine)
    result = runner.run_until(ctx.project_id, "C", ctx=ctx)

    assert result.status is ProductionRunStatus.SUCCEEDED
    for node_id in ("A", "B", "C"):
        record = result.node_states[node_id]
        assert record.status is ProductionNodeStatus.SUCCEEDED
        assert record.executed_this_run is True
        assert record.reused_existing_artifact is False
    assert adapters["A"].execute_count == 1
    assert adapters["B"].execute_count == 1
    assert adapters["C"].execute_count == 1


def test_second_run_reuses_all_fresh_outputs(tmp_path):
    store: dict = {}
    graph = ProductionGraph(
        [
            ProductionNodeDefinition(node_id="A"),
            ProductionNodeDefinition(node_id="B", dependencies=("A",)),
            ProductionNodeDefinition(node_id="C", dependencies=("B",)),
        ]
    )
    adapters = {
        "A": FakeAdapter("A", None, store),
        "B": FakeAdapter("B", "A", store),
        "C": FakeAdapter("C", "B", store),
    }
    from app.storage.database import init_database

    engine = init_database(tmp_path / "db.sqlite")
    runner = ProductionRunner(engine, graph, adapters)
    project_id = uuid4()

    runner.run_until(project_id, "C", ctx=ExecutionContext(db_engine=engine, project_id=project_id))
    second = runner.run_until(project_id, "C", ctx=ExecutionContext(db_engine=engine, project_id=project_id))

    assert second.status is ProductionRunStatus.SUCCEEDED
    for node_id in ("A", "B", "C"):
        record = second.node_states[node_id]
        assert record.reused_existing_artifact is True
        assert record.executed_this_run is False
    assert adapters["A"].execute_count == 1
    assert adapters["B"].execute_count == 1
    assert adapters["C"].execute_count == 1


def test_target_never_executes_beyond_itself(tmp_path):
    """run_until('B') on A->B->C must never touch C at all (requirement #8)."""
    store: dict = {}
    graph = ProductionGraph(
        [
            ProductionNodeDefinition(node_id="A"),
            ProductionNodeDefinition(node_id="B", dependencies=("A",)),
            ProductionNodeDefinition(node_id="C", dependencies=("B",)),
        ]
    )
    adapters = {
        "A": FakeAdapter("A", None, store),
        "B": FakeAdapter("B", "A", store),
        "C": FakeAdapter("C", "B", store),
    }
    from app.storage.database import init_database

    engine = init_database(tmp_path / "db.sqlite")
    runner = ProductionRunner(engine, graph, adapters)
    project_id = uuid4()

    result = runner.run_until(project_id, "B", ctx=ExecutionContext(db_engine=engine, project_id=project_id))

    assert result.status is ProductionRunStatus.SUCCEEDED
    assert "C" not in result.node_states
    assert adapters["C"].execute_count == 0


# ---------------------------------------------------------------------------
# Stop-at-gate / resume (requirement #35)
# ---------------------------------------------------------------------------


def _gated_chain(store: dict) -> tuple[ProductionGraph, dict]:
    graph = ProductionGraph(
        [
            ProductionNodeDefinition(node_id="A", gate_after=ApprovalGateType.FINAL_MEDIA_APPROVAL),
            ProductionNodeDefinition(node_id="B", dependencies=("A",)),
            ProductionNodeDefinition(node_id="C", dependencies=("B",)),
        ]
    )
    adapters = {
        "A": FakeAdapter("A", None, store),
        "B": FakeAdapter("B", "A", store),
        "C": FakeAdapter("C", "B", store),
    }
    return graph, adapters


def test_run_stops_at_waiting_approval_and_resume_continues(tmp_path):
    from app.storage.database import init_database

    engine = init_database(tmp_path / "db.sqlite")
    store: dict = {}
    graph, adapters = _gated_chain(store)
    runner = ProductionRunner(engine, graph, adapters)
    project_id = uuid4()
    ctx = ExecutionContext(db_engine=engine, project_id=project_id)

    first = runner.run_until(project_id, "C", ctx=ctx)

    assert first.status is ProductionRunStatus.WAITING_APPROVAL
    assert first.node_states["A"].status is ProductionNodeStatus.WAITING_APPROVAL
    assert first.node_states["B"].status is ProductionNodeStatus.BLOCKED
    assert first.node_states["B"].reason is BlockedReason.BLOCKED_DEPENDENCY
    assert first.node_states["C"].status is ProductionNodeStatus.BLOCKED
    assert adapters["B"].execute_count == 0
    assert adapters["C"].execute_count == 0

    artifact_a_id = UUID(first.node_states["A"].artifact_id)
    approve_gate(engine, graph, project_id, ApprovalGateType.FINAL_MEDIA_APPROVAL, artifact_a_id)

    resumed = runner.resume(first.id, ctx=ctx)

    assert resumed.status is ProductionRunStatus.SUCCEEDED
    assert resumed.node_states["A"].reused_existing_artifact is True
    assert resumed.node_states["B"].executed_this_run is True
    assert resumed.node_states["C"].executed_this_run is True
    assert adapters["A"].execute_count == 1  # not re-executed on resume
    assert resumed.id == first.id


# ---------------------------------------------------------------------------
# Rejection (requirement #36)
# ---------------------------------------------------------------------------


def test_rejected_gate_blocks_downstream_without_regeneration(tmp_path):
    from app.storage.database import init_database

    engine = init_database(tmp_path / "db.sqlite")
    store: dict = {}
    graph, adapters = _gated_chain(store)
    runner = ProductionRunner(engine, graph, adapters)
    project_id = uuid4()
    ctx = ExecutionContext(db_engine=engine, project_id=project_id)

    first = runner.run_until(project_id, "C", ctx=ctx)
    artifact_a_id = UUID(first.node_states["A"].artifact_id)
    reject_gate(engine, graph, project_id, ApprovalGateType.FINAL_MEDIA_APPROVAL, artifact_a_id)

    second = runner.run_until(project_id, "C", ctx=ctx)

    assert second.status is ProductionRunStatus.BLOCKED
    assert second.node_states["A"].status is ProductionNodeStatus.BLOCKED
    assert second.node_states["A"].reason is BlockedReason.GATE_REJECTED
    assert second.node_states["A"].artifact_id == str(artifact_a_id)  # artifact never deleted
    assert second.node_states["B"].status is ProductionNodeStatus.BLOCKED
    assert adapters["A"].execute_count == 1  # never auto-regenerated
    assert adapters["B"].execute_count == 0


# ---------------------------------------------------------------------------
# Stale artifact invalidation (requirement #37)
# ---------------------------------------------------------------------------


def test_stale_upstream_replacement_invalidates_prior_approval(tmp_path):
    from app.storage.database import init_database

    engine = init_database(tmp_path / "db.sqlite")
    store: dict = {}
    graph, adapters = _gated_chain(store)
    runner = ProductionRunner(engine, graph, adapters)
    project_id = uuid4()
    ctx = ExecutionContext(db_engine=engine, project_id=project_id)

    first = runner.run_until(project_id, "C", ctx=ctx)
    old_artifact_a_id = UUID(first.node_states["A"].artifact_id)
    approve_gate(engine, graph, project_id, ApprovalGateType.FINAL_MEDIA_APPROVAL, old_artifact_a_id)
    resumed = runner.resume(first.id, ctx=ctx)
    assert resumed.status is ProductionRunStatus.SUCCEEDED

    # Replace A's artifact (e.g. an upstream input changed and A was
    # re-executed by hand) with a brand-new id -- the OLD approval must
    # not silently cover the NEW artifact.
    store["A"] = SimpleNamespace(id=uuid4(), upstream_id=None)

    third = runner.run_until(project_id, "C", ctx=ctx)

    assert third.status is ProductionRunStatus.WAITING_APPROVAL
    assert third.node_states["A"].status is ProductionNodeStatus.WAITING_APPROVAL
    new_artifact_a_id = UUID(third.node_states["A"].artifact_id)
    assert new_artifact_a_id != old_artifact_a_id
    assert third.node_states["B"].status is ProductionNodeStatus.BLOCKED


# ---------------------------------------------------------------------------
# Execution failure (requirement #38)
# ---------------------------------------------------------------------------


def test_execution_failure_marks_node_failed_and_blocks_downstream_without_calling_it(tmp_path):
    from app.storage.database import init_database

    engine = init_database(tmp_path / "db.sqlite")
    store: dict = {}
    graph = ProductionGraph(
        [
            ProductionNodeDefinition(node_id="A"),
            ProductionNodeDefinition(node_id="B", dependencies=("A",)),
            ProductionNodeDefinition(node_id="C", dependencies=("B",)),
        ]
    )
    adapters = {
        "A": FakeAdapter("A", None, store),
        "B": FakeAdapter("B", "A", store, fails=True),
        "C": FakeAdapter("C", "B", store),
    }
    runner = ProductionRunner(engine, graph, adapters)
    project_id = uuid4()
    ctx = ExecutionContext(db_engine=engine, project_id=project_id)

    result = runner.run_until(project_id, "C", ctx=ctx)

    assert result.status is ProductionRunStatus.FAILED
    assert result.node_states["B"].status is ProductionNodeStatus.FAILED
    assert result.node_states["B"].reason is BlockedReason.NODE_FAILED
    assert result.node_states["C"].status is ProductionNodeStatus.BLOCKED
    assert adapters["C"].execute_count == 0

    # Explicit rerun with the bug "fixed" can retry B -- no hidden retry
    # loop happened automatically, but nothing prevents a fresh call.
    adapters["B"]._fails = False
    retried = runner.run_until(project_id, "C", ctx=ctx)
    assert retried.status is ProductionRunStatus.SUCCEEDED
    assert adapters["B"].execute_count == 2


# ---------------------------------------------------------------------------
# QC gate-precondition semantics (requirement #39)
# ---------------------------------------------------------------------------


def test_gate_ok_true_makes_gate_eligible_for_approval_request(tmp_path):
    from app.storage.database import init_database

    engine = init_database(tmp_path / "db.sqlite")
    store: dict = {}
    graph = ProductionGraph(
        [ProductionNodeDefinition(node_id="QC", gate_after=ApprovalGateType.FINAL_MEDIA_APPROVAL)]
    )
    adapters = {"QC": FakeAdapter("QC", None, store, gate_ok=True)}
    runner = ProductionRunner(engine, graph, adapters)
    project_id = uuid4()

    result = runner.run_until(project_id, "QC", ctx=ExecutionContext(db_engine=engine, project_id=project_id))

    assert result.status is ProductionRunStatus.WAITING_APPROVAL
    assert result.node_states["QC"].reason is BlockedReason.WAITING_GATE


def test_gate_ok_false_blocks_without_ever_requesting_approval(tmp_path):
    from app.storage.database import init_database

    engine = init_database(tmp_path / "db.sqlite")
    store: dict = {}
    graph = ProductionGraph(
        [ProductionNodeDefinition(node_id="QC", gate_after=ApprovalGateType.FINAL_MEDIA_APPROVAL)]
    )
    adapters = {"QC": FakeAdapter("QC", None, store, gate_ok=False)}
    runner = ProductionRunner(engine, graph, adapters)
    project_id = uuid4()

    result = runner.run_until(project_id, "QC", ctx=ExecutionContext(db_engine=engine, project_id=project_id))

    assert result.status is ProductionRunStatus.BLOCKED
    assert result.node_states["QC"].status is ProductionNodeStatus.BLOCKED
    assert result.node_states["QC"].reason is BlockedReason.QC_NOT_READY
    # No decision was ever recorded -- the gate was never even offered.
    from app.storage.approval_decisions import list_approval_decisions_for_project

    assert list_approval_decisions_for_project(engine, project_id) == []


# ---------------------------------------------------------------------------
# Node-not-wired (UnwiredNodeAdapter integration with the runner)
# ---------------------------------------------------------------------------


def test_unwired_node_blocks_without_raising(tmp_path):
    from app.storage.database import init_database
    from app.orchestration.registry import UnwiredNodeAdapter
    from app.models.idea import IdeaCandidate

    engine = init_database(tmp_path / "db.sqlite")
    graph = ProductionGraph([ProductionNodeDefinition(node_id="IDEA")])
    adapters = {"IDEA": UnwiredNodeAdapter("IDEA", "idea_candidate", IdeaCandidate)}
    runner = ProductionRunner(engine, graph, adapters)
    project_id = uuid4()

    result = runner.run_until(project_id, "IDEA", ctx=ExecutionContext(db_engine=engine, project_id=project_id))

    assert result.status is ProductionRunStatus.BLOCKED
    assert result.node_states["IDEA"].reason is BlockedReason.NODE_NOT_WIRED
