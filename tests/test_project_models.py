from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest
from pydantic import ValidationError

from app.models.project import Project


def _now():
    return datetime.now(timezone.utc)


def test_valid_project():
    created = _now()
    project = Project(
        title_internal="Ep01 - Vi khuẩn kháng thuốc",
        created_at=created,
        updated_at=created,
        state="NEW_PROJECT",
    )
    assert project.state.value == "NEW_PROJECT"
    assert project.template_versions.idea == "0.1"
    assert project.template_versions.research == "0.1"
    assert project.template_versions.feasibility == "0.1"
    assert project.template_versions.narrative == "0.1"
    assert project.template_versions.script == "0.1"


def test_updated_before_created_rejects():
    created = _now()
    with pytest.raises(ValidationError):
        Project(
            title_internal="Ep01",
            created_at=created,
            updated_at=created - timedelta(days=1),
            state="NEW_PROJECT",
        )


def test_invalid_state_rejects():
    created = _now()
    with pytest.raises(ValidationError):
        Project(
            title_internal="Ep01",
            created_at=created,
            updated_at=created,
            state="NOT_A_REAL_STATE",
        )


def test_naive_datetime_rejects():
    with pytest.raises(ValidationError):
        Project(
            title_internal="Ep01",
            created_at=datetime.now(),
            updated_at=datetime.now(),
            state="NEW_PROJECT",
        )
