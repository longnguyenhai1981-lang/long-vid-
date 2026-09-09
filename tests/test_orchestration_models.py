"""Phase 33 focused tests: app/orchestration/models.py's own typed
contracts and invariants (requirement #31)."""

from __future__ import annotations

from datetime import datetime, timezone
from uuid import uuid4

import pytest
from pydantic import ValidationError

from app.orchestration.models import (
    ApprovalDecision,
    ApprovalDecisionType,
    ApprovalGateType,
    BlockedReason,
    NodeRunRecord,
    ProductionNodeStatus,
    ProductionRun,
    ProductionRunStatus,
)


def test_node_run_record_defaults():
    record = NodeRunRecord(node_id="VOICE_RENDER", status=ProductionNodeStatus.SUCCEEDED)
    assert record.artifact_id is None
    assert record.executed_this_run is False
    assert record.reused_existing_artifact is False


def test_node_run_record_rejects_blank_node_id():
    with pytest.raises(ValidationError):
        NodeRunRecord(node_id="   ", status=ProductionNodeStatus.PENDING)


def test_node_run_record_rejects_executed_and_reused_together():
    with pytest.raises(ValidationError):
        NodeRunRecord(
            node_id="VOICE_RENDER",
            status=ProductionNodeStatus.SUCCEEDED,
            executed_this_run=True,
            reused_existing_artifact=True,
        )


def test_node_run_record_accepts_reason_without_status_restriction():
    record = NodeRunRecord(
        node_id="MEDIA_QC", status=ProductionNodeStatus.BLOCKED, reason=BlockedReason.QC_NOT_READY
    )
    assert record.reason is BlockedReason.QC_NOT_READY


def test_production_run_requires_timezone_aware_timestamps():
    with pytest.raises(ValidationError):
        ProductionRun(
            project_id=uuid4(),
            target_node="MEDIA_QC",
            status=ProductionRunStatus.RUNNING,
            created_at=datetime.now(),  # naive
            updated_at=datetime.now(timezone.utc),
        )


def test_production_run_rejects_blank_target_node():
    with pytest.raises(ValidationError):
        ProductionRun(
            project_id=uuid4(),
            target_node="   ",
            status=ProductionRunStatus.RUNNING,
            created_at=datetime.now(timezone.utc),
            updated_at=datetime.now(timezone.utc),
        )


def test_approval_decision_requires_timezone_aware_decided_at():
    with pytest.raises(ValidationError):
        ApprovalDecision(
            project_id=uuid4(),
            gate_type=ApprovalGateType.FINAL_MEDIA_APPROVAL,
            subject_artifact_id=uuid4(),
            decision=ApprovalDecisionType.APPROVED,
            decided_at=datetime.now(),  # naive
        )


def test_approval_decision_round_trips():
    decision = ApprovalDecision(
        project_id=uuid4(),
        gate_type=ApprovalGateType.FINAL_MEDIA_APPROVAL,
        subject_artifact_id=uuid4(),
        decision=ApprovalDecisionType.REJECTED,
        decided_at=datetime.now(timezone.utc),
        note="not ready",
    )
    assert decision.decision is ApprovalDecisionType.REJECTED
    assert decision.note == "not ready"
