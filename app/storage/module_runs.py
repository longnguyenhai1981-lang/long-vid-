"""ModuleRun audit-log persistence. Metadata only -- no engine executes here."""

from __future__ import annotations

import json
from uuid import UUID

from sqlalchemy import Engine, select
from sqlalchemy.orm import Session

from app.models.common import ModuleRunStatus
from app.models.module_run import ModuleRun
from app.storage.errors import ModuleRunNotFoundError
from app.storage.orm import ModuleRunRow


def _to_domain(row: ModuleRunRow) -> ModuleRun:
    return ModuleRun(
        run_id=UUID(row.run_id),
        project_id=UUID(row.project_id),
        module=row.module,
        module_version=row.module_version,
        started_at=row.started_at,
        completed_at=row.completed_at,
        input_ids=json.loads(row.input_ids_json),
        output_id=row.output_id,
        status=ModuleRunStatus(row.status),
        error_message=row.error_message,
    )


def save_module_run(engine: Engine, run: ModuleRun) -> None:
    """Upsert by run_id, so a run's record can be updated as it completes."""
    with Session(engine) as session, session.begin():
        row = session.execute(
            select(ModuleRunRow).where(ModuleRunRow.run_id == str(run.run_id))
        ).scalar_one_or_none()
        if row is None:
            row = ModuleRunRow(run_id=str(run.run_id))
            session.add(row)
        row.project_id = str(run.project_id)
        row.module = run.module
        row.module_version = run.module_version
        row.started_at = run.started_at
        row.completed_at = run.completed_at
        row.input_ids_json = json.dumps(run.input_ids)
        row.output_id = run.output_id
        row.status = run.status.value
        row.error_message = run.error_message


def get_module_run(engine: Engine, run_id: UUID) -> ModuleRun:
    with Session(engine) as session:
        row = session.execute(
            select(ModuleRunRow).where(ModuleRunRow.run_id == str(run_id))
        ).scalar_one_or_none()
        if row is None:
            raise ModuleRunNotFoundError(f"Module run not found: {run_id}")
        return _to_domain(row)


def list_module_runs_for_project(engine: Engine, project_id: UUID) -> list[ModuleRun]:
    with Session(engine) as session:
        rows = (
            session.execute(
                select(ModuleRunRow)
                .where(ModuleRunRow.project_id == str(project_id))
                .order_by(ModuleRunRow.id)
            )
            .scalars()
            .all()
        )
        return [_to_domain(row) for row in rows]
