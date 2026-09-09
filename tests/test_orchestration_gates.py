"""Phase 33 focused tests: approval-decision persistence
(app/storage/approval_decisions.py) and the gate service
(app/orchestration/gates.py) -- requirements #13/#14/#18/#33."""

from __future__ import annotations

from uuid import uuid4

import pytest

from app.orchestration.errors import UnknownApprovalGateError
from app.orchestration.gates import approve_gate, gate_decision_for, reject_gate
from app.orchestration.graph import ProductionGraph, ProductionNodeDefinition
from app.orchestration.models import ApprovalDecisionType, ApprovalGateType
from app.storage.approval_decisions import (
    get_latest_approval_decision,
    list_approval_decisions_for_project,
    save_approval_decision,
)


def _graph_with_gate() -> ProductionGraph:
    return ProductionGraph(
        [
            ProductionNodeDefinition(node_id="A", gate_after=ApprovalGateType.FINAL_MEDIA_APPROVAL),
            ProductionNodeDefinition(node_id="B", dependencies=("A",)),
        ]
    )


def test_approve_gate_persists_decision(engine):
    graph = _graph_with_gate()
    project_id = uuid4()
    artifact_id = uuid4()

    decision = approve_gate(engine, graph, project_id, ApprovalGateType.FINAL_MEDIA_APPROVAL, artifact_id)

    assert decision.decision is ApprovalDecisionType.APPROVED
    stored = get_latest_approval_decision(engine, project_id, ApprovalGateType.FINAL_MEDIA_APPROVAL, artifact_id)
    assert stored is not None
    assert stored.id == decision.id
    assert stored.subject_artifact_id == artifact_id


def test_reject_gate_persists_decision_with_note(engine):
    graph = _graph_with_gate()
    project_id = uuid4()
    artifact_id = uuid4()

    decision = reject_gate(
        engine, graph, project_id, ApprovalGateType.FINAL_MEDIA_APPROVAL, artifact_id, note="not ready yet"
    )

    assert decision.decision is ApprovalDecisionType.REJECTED
    assert decision.note == "not ready yet"


def test_unknown_gate_type_rejected(engine):
    """A graph with no node declaring gate_after=X must refuse a decision
    against X -- prevents silently recording approvals nothing ever
    consumes."""
    graph = ProductionGraph([ProductionNodeDefinition(node_id="A")])
    with pytest.raises(UnknownApprovalGateError):
        approve_gate(engine, graph, uuid4(), ApprovalGateType.FINAL_MEDIA_APPROVAL, uuid4())


def test_no_decision_returns_none(engine):
    assert gate_decision_for(engine, uuid4(), ApprovalGateType.FINAL_MEDIA_APPROVAL, uuid4()) is None


def test_decision_scoped_to_exact_subject_artifact_id(engine):
    """An approval for artifact A must never apply to a different
    artifact B, even under the same project/gate (requirement #14)."""
    graph = _graph_with_gate()
    project_id = uuid4()
    artifact_a = uuid4()
    artifact_b = uuid4()

    approve_gate(engine, graph, project_id, ApprovalGateType.FINAL_MEDIA_APPROVAL, artifact_a)

    assert gate_decision_for(engine, project_id, ApprovalGateType.FINAL_MEDIA_APPROVAL, artifact_a) is not None
    assert gate_decision_for(engine, project_id, ApprovalGateType.FINAL_MEDIA_APPROVAL, artifact_b) is None


def test_duplicate_decision_most_recent_wins(engine):
    """Requirement #33's documented duplicate policy: append-only, most
    recent decision (by insertion order) is authoritative."""
    graph = _graph_with_gate()
    project_id = uuid4()
    artifact_id = uuid4()

    reject_gate(engine, graph, project_id, ApprovalGateType.FINAL_MEDIA_APPROVAL, artifact_id)
    approve_gate(engine, graph, project_id, ApprovalGateType.FINAL_MEDIA_APPROVAL, artifact_id)

    latest = gate_decision_for(engine, project_id, ApprovalGateType.FINAL_MEDIA_APPROVAL, artifact_id)
    assert latest.decision is ApprovalDecisionType.APPROVED

    all_decisions = list_approval_decisions_for_project(engine, project_id)
    assert len(all_decisions) == 2


def test_decisions_persist_across_storage_reload(engine):
    graph = _graph_with_gate()
    project_id = uuid4()
    artifact_id = uuid4()
    approve_gate(engine, graph, project_id, ApprovalGateType.FINAL_MEDIA_APPROVAL, artifact_id)

    # A brand-new query against the same engine simulates a fresh process
    # reading back persisted state -- no in-memory cache is involved.
    reloaded = get_latest_approval_decision(engine, project_id, ApprovalGateType.FINAL_MEDIA_APPROVAL, artifact_id)
    assert reloaded is not None
    assert reloaded.decision is ApprovalDecisionType.APPROVED


def test_save_approval_decision_never_upserts(engine):
    from app.orchestration.models import ApprovalDecision
    from datetime import datetime, timezone

    project_id = uuid4()
    artifact_id = uuid4()
    for _ in range(3):
        save_approval_decision(
            engine,
            ApprovalDecision(
                project_id=project_id,
                gate_type=ApprovalGateType.FINAL_MEDIA_APPROVAL,
                subject_artifact_id=artifact_id,
                decision=ApprovalDecisionType.APPROVED,
                decided_at=datetime.now(timezone.utc),
            ),
        )
    assert len(list_approval_decisions_for_project(engine, project_id)) == 3
