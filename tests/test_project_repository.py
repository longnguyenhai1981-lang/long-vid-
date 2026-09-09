from __future__ import annotations

from datetime import datetime, timedelta, timezone
from uuid import uuid4

import pytest

from app.models.common import ProjectState
from app.models.project import Project
from app.storage.errors import ProjectNotFoundError
from app.storage.projects import (
    create_project,
    get_project,
    list_projects,
    update_artifact_reference,
    update_project_state,
)
from app.workflow.errors import InvalidStateTransitionError


def _project(**overrides):
    created = datetime.now(timezone.utc)
    kwargs = dict(
        title_internal="Ep01 - Vi khuẩn kháng thuốc",
        created_at=created,
        updated_at=created,
        state=ProjectState.NEW_PROJECT,
    )
    kwargs.update(overrides)
    return Project(**kwargs)


def test_create_and_get_project(engine):
    project = _project()
    create_project(engine, project)
    fetched = get_project(engine, project.project_id)
    assert fetched == project


def test_create_project_requires_new_project_state(engine):
    project = _project(state=ProjectState.IDEA_DISCOVERY)
    with pytest.raises(ValueError):
        create_project(engine, project)


def test_list_projects(engine):
    p1 = _project(title_internal="Ep01")
    p2 = _project(title_internal="Ep02")
    create_project(engine, p1)
    create_project(engine, p2)
    projects = list_projects(engine)
    assert {p.project_id for p in projects} == {p1.project_id, p2.project_id}


def test_valid_state_update(engine):
    project = _project()
    create_project(engine, project)
    updated = update_project_state(engine, project.project_id, ProjectState.IDEA_DISCOVERY)
    assert updated.state == ProjectState.IDEA_DISCOVERY
    assert updated.updated_at >= project.updated_at


def test_invalid_state_update_rejected(engine):
    project = _project()
    create_project(engine, project)
    with pytest.raises(InvalidStateTransitionError):
        update_project_state(engine, project.project_id, ProjectState.SCRIPT)


def test_state_unchanged_after_rejected_transition(engine):
    project = _project()
    create_project(engine, project)
    with pytest.raises(InvalidStateTransitionError):
        update_project_state(engine, project.project_id, ProjectState.SCRIPT)
    fetched = get_project(engine, project.project_id)
    assert fetched.state == ProjectState.NEW_PROJECT


def test_artifact_reference_update(engine):
    project = _project()
    create_project(engine, project)
    idea_id = uuid4()
    updated = update_artifact_reference(engine, project.project_id, "idea_candidate_id", idea_id)
    assert updated.idea_candidate_id == idea_id
    fetched = get_project(engine, project.project_id)
    assert fetched.idea_candidate_id == idea_id
    assert fetched.research_r0_id is None


def test_unknown_artifact_reference_field_rejected(engine):
    project = _project()
    create_project(engine, project)
    with pytest.raises(ValueError):
        update_artifact_reference(engine, project.project_id, "not_a_real_field", uuid4())


def test_timezone_aware_timestamps_survive_round_trip(engine):
    created = datetime.now(timezone.utc) - timedelta(days=1)
    project = _project(created_at=created, updated_at=created)
    create_project(engine, project)
    fetched = get_project(engine, project.project_id)
    assert fetched.created_at == created
    assert fetched.created_at.tzinfo is not None


def test_missing_project_raises_not_found(engine):
    with pytest.raises(ProjectNotFoundError):
        get_project(engine, uuid4())


def test_missing_project_state_update_raises_not_found(engine):
    with pytest.raises(ProjectNotFoundError):
        update_project_state(engine, uuid4(), ProjectState.IDEA_DISCOVERY)
