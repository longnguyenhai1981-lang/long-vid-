from __future__ import annotations

from datetime import datetime, timedelta, timezone
from uuid import uuid4

from app.models.approval import HumanApproval
from app.models.common import ProjectState
from app.storage.approvals import (
    get_latest_approval_for_stage,
    list_approvals_for_project,
    save_approval,
)


def _approval(project_id, stage, status="APPROVED", created_at=None):
    return HumanApproval(
        project_id=project_id,
        stage=stage,
        status=status,
        created_at=created_at or datetime.now(timezone.utc),
    )


def test_save_and_list_approvals(engine):
    project_id = uuid4()
    a1 = _approval(project_id, ProjectState.IDEA_REVIEW)
    a2 = _approval(project_id, ProjectState.NARRATIVE_REVIEW)
    save_approval(engine, a1)
    save_approval(engine, a2)
    approvals = list_approvals_for_project(engine, project_id)
    assert {a.approval_id for a in approvals} == {a1.approval_id, a2.approval_id}


def test_latest_approval_by_stage(engine):
    project_id = uuid4()
    now = datetime.now(timezone.utc)
    older = _approval(project_id, ProjectState.IDEA_REVIEW, status="REVISE", created_at=now - timedelta(minutes=5))
    newer = _approval(project_id, ProjectState.IDEA_REVIEW, status="APPROVED", created_at=now)
    save_approval(engine, older)
    save_approval(engine, newer)
    latest = get_latest_approval_for_stage(engine, project_id, ProjectState.IDEA_REVIEW)
    assert latest.approval_id == newer.approval_id
    assert latest.status.value == "APPROVED"


def test_multiple_approval_stages(engine):
    project_id = uuid4()
    idea_approval = _approval(project_id, ProjectState.IDEA_REVIEW)
    script_approval = _approval(project_id, ProjectState.SCRIPT_REVIEW)
    save_approval(engine, idea_approval)
    save_approval(engine, script_approval)

    assert (
        get_latest_approval_for_stage(engine, project_id, ProjectState.IDEA_REVIEW).approval_id
        == idea_approval.approval_id
    )
    assert (
        get_latest_approval_for_stage(engine, project_id, ProjectState.SCRIPT_REVIEW).approval_id
        == script_approval.approval_id
    )


def test_no_approval_for_stage_returns_none(engine):
    project_id = uuid4()
    assert get_latest_approval_for_stage(engine, project_id, ProjectState.SCRIPT_REVIEW) is None


def test_approval_project_isolation(engine):
    project_a, project_b = uuid4(), uuid4()
    save_approval(engine, _approval(project_a, ProjectState.IDEA_REVIEW))
    save_approval(engine, _approval(project_b, ProjectState.IDEA_REVIEW))
    assert len(list_approvals_for_project(engine, project_a)) == 1
    assert len(list_approvals_for_project(engine, project_b)) == 1
