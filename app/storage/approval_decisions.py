"""ApprovalDecision persistence (Phase 33).

Distinct from app/storage/approvals.py's own ApprovalRow/HumanApproval
mechanism (which drives ProjectState transitions for the earlier
creative-review gates and carries no artifact-id binding) -- see
app/orchestration/models.py's ApprovalDecision docstring for the full
rationale.

Append-only: `save_approval_decision` always inserts a new row, never
updates one in place. Multiple decisions may accumulate for the same
(project_id, gate_type, subject_artifact_id) over time (a human may
reconsider) -- `get_latest_approval_decision` always returns the most
recently inserted one (ordered by the row's own autoincrement id,
mirroring ModuleRunRow's own "insertion order is temporal order"
convention). This is the documented duplicate policy (requirement #33):
the latest explicit decision for the same gate+subject wins, never an
error, never ambiguous.
"""

from __future__ import annotations

from uuid import UUID

from sqlalchemy import Engine, select
from sqlalchemy.orm import Session

from app.orchestration.models import ApprovalDecision, ApprovalDecisionType, ApprovalGateType
from app.storage.orm import ApprovalDecisionRow


def _to_domain(row: ApprovalDecisionRow) -> ApprovalDecision:
    return ApprovalDecision(
        id=UUID(row.decision_id),
        project_id=UUID(row.project_id),
        gate_type=ApprovalGateType(row.gate_type),
        subject_artifact_id=UUID(row.subject_artifact_id),
        decision=ApprovalDecisionType(row.decision),
        decided_at=row.decided_at,
        note=row.note,
    )


def save_approval_decision(engine: Engine, decision: ApprovalDecision) -> None:
    """Always inserts -- never upserts. Each call records one more
    explicit human decision in the audit trail."""
    with Session(engine) as session, session.begin():
        row = ApprovalDecisionRow(
            decision_id=str(decision.id),
            project_id=str(decision.project_id),
            gate_type=decision.gate_type.value,
            subject_artifact_id=str(decision.subject_artifact_id),
            decision=decision.decision.value,
            decided_at=decision.decided_at,
            note=decision.note,
        )
        session.add(row)


def get_latest_approval_decision(
    engine: Engine, project_id: UUID, gate_type: ApprovalGateType, subject_artifact_id: UUID
) -> ApprovalDecision | None:
    """Returns the most recent decision for this exact
    (project_id, gate_type, subject_artifact_id), or None if no decision
    has ever been recorded for it. A decision recorded for a DIFFERENT
    subject_artifact_id (e.g. the artifact was replaced) is never
    returned here -- approval never carries forward across artifact
    replacement (requirement #14)."""
    with Session(engine) as session:
        row = (
            session.execute(
                select(ApprovalDecisionRow)
                .where(
                    ApprovalDecisionRow.project_id == str(project_id),
                    ApprovalDecisionRow.gate_type == gate_type.value,
                    ApprovalDecisionRow.subject_artifact_id == str(subject_artifact_id),
                )
                .order_by(ApprovalDecisionRow.id.desc())
                .limit(1)
            )
            .scalars()
            .first()
        )
        return _to_domain(row) if row is not None else None


def list_approval_decisions_for_project(engine: Engine, project_id: UUID) -> list[ApprovalDecision]:
    with Session(engine) as session:
        rows = (
            session.execute(
                select(ApprovalDecisionRow)
                .where(ApprovalDecisionRow.project_id == str(project_id))
                .order_by(ApprovalDecisionRow.id)
            )
            .scalars()
            .all()
        )
        return [_to_domain(row) for row in rows]
