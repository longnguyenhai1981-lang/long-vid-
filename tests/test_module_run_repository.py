from __future__ import annotations

from datetime import datetime, timedelta, timezone
from uuid import uuid4

import pytest
from pydantic import ValidationError

from app.models.module_run import ModuleRun
from app.storage.errors import ModuleRunNotFoundError
from app.storage.module_runs import get_module_run, save_module_run


def test_running_record_round_trip(engine):
    started = datetime.now(timezone.utc)
    run = ModuleRun(
        project_id=uuid4(),
        module="idea_discovery",
        module_version="0.1",
        started_at=started,
        status="RUNNING",
    )
    save_module_run(engine, run)
    fetched = get_module_run(engine, run.run_id)
    assert fetched == run


def test_success_record_round_trip(engine):
    started = datetime.now(timezone.utc)
    run = ModuleRun(
        project_id=uuid4(),
        module="idea_discovery",
        module_version="0.1",
        started_at=started,
        completed_at=started + timedelta(seconds=5),
        input_ids=["C001", "C002"],
        output_id="idea-001",
        status="SUCCESS",
    )
    save_module_run(engine, run)
    fetched = get_module_run(engine, run.run_id)
    assert fetched == run


def test_failed_record_round_trip(engine):
    started = datetime.now(timezone.utc)
    run = ModuleRun(
        project_id=uuid4(),
        module="idea_discovery",
        module_version="0.1",
        started_at=started,
        completed_at=started + timedelta(seconds=2),
        status="FAILED",
        error_message="provider timeout",
    )
    save_module_run(engine, run)
    fetched = get_module_run(engine, run.run_id)
    assert fetched == run


def test_save_module_run_upserts_by_run_id(engine):
    started = datetime.now(timezone.utc)
    run = ModuleRun(
        project_id=uuid4(),
        module="idea_discovery",
        module_version="0.1",
        started_at=started,
        status="RUNNING",
    )
    save_module_run(engine, run)

    completed = ModuleRun(
        run_id=run.run_id,
        project_id=run.project_id,
        module=run.module,
        module_version=run.module_version,
        started_at=started,
        completed_at=started + timedelta(seconds=3),
        output_id="idea-001",
        status="SUCCESS",
    )
    save_module_run(engine, completed)

    fetched = get_module_run(engine, run.run_id)
    assert fetched.status.value == "SUCCESS"
    assert fetched.output_id == "idea-001"


def test_missing_module_run_raises_not_found(engine):
    with pytest.raises(ModuleRunNotFoundError):
        get_module_run(engine, uuid4())


def test_success_without_completed_at_rejected():
    with pytest.raises(ValidationError):
        ModuleRun(
            project_id=uuid4(),
            module="idea_discovery",
            module_version="0.1",
            started_at=datetime.now(timezone.utc),
            status="SUCCESS",
        )


def test_failed_without_error_message_rejected():
    started = datetime.now(timezone.utc)
    with pytest.raises(ValidationError):
        ModuleRun(
            project_id=uuid4(),
            module="idea_discovery",
            module_version="0.1",
            started_at=started,
            completed_at=started,
            status="FAILED",
        )


def test_completed_before_started_rejected():
    started = datetime.now(timezone.utc)
    with pytest.raises(ValidationError):
        ModuleRun(
            project_id=uuid4(),
            module="idea_discovery",
            module_version="0.1",
            started_at=started,
            completed_at=started - timedelta(seconds=1),
            status="SUCCESS",
        )
