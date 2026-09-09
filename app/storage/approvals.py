"""HumanApproval persistence. Approvals never mutate project state in Phase 2."""

from __future__ import annotations

from uuid import UUID

from sqlalchemy import Engine, select
from sqlalchemy.orm import Session

from app.models.approval import HumanApproval
from app.models.common import ApprovalStatus, ProjectState
from app.storage.orm import ApprovalRow


def _to_row(approval: HumanApproval) -> ApprovalRow:
    return ApprovalRow(
        approval_id=str(approval.approval_id),
        project_id=str(approval.project_id),
        stage=approval.stage.value,
        status=approval.status.value,
        user_feedback=approval.user_feedback,
        created_at=approval.created_at,
    )


def _to_domain(row: ApprovalRow) -> HumanApproval:
    return HumanApproval(
        approval_id=UUID(row.approval_id),
        project_id=UUID(row.project_id),
        stage=ProjectState(row.stage),
        status=ApprovalStatus(row.status),
        user_feedback=row.user_feedback,
        created_at=row.created_at,
    )


def save_approval(engine: Engine, approval: HumanApproval) -> None:
    with Session(engine) as session, session.begin():
        session.add(_to_row(approval))


def list_approvals_for_project(engine: Engine, project_id: UUID) -> list[HumanApproval]:
    with Session(engine) as session:
        rows = (
            session.execute(
                select(ApprovalRow)
                .where(ApprovalRow.project_id == str(project_id))
                .order_by(ApprovalRow.id)
            )
            .scalars()
            .all()
        )
        return [_to_domain(row) for row in rows]


def get_latest_approval_for_stage(
    engine: Engine, project_id: UUID, stage: ProjectState
) -> HumanApproval | None:
    with Session(engine) as session:
        row = (
            session.execute(
                select(ApprovalRow)
                .where(
                    ApprovalRow.project_id == str(project_id),
                    ApprovalRow.stage == stage.value,
                )
                .order_by(ApprovalRow.id.desc())
                .limit(1)
            )
            .scalars()
            .first()
        )
        return _to_domain(row) if row is not None else None
