"""ProductionRun persistence (Phase 33).

Mirrors app/storage/module_runs.py's own upsert-by-id shape exactly, but
keyed by the ProductionRun's own `id` (a project may accumulate many
ProductionRun rows over time, across resume cycles -- there is no
one-row-per-project-per-type constraint here, unlike
app/storage/artifacts.py's own ArtifactRow convention).
"""

from __future__ import annotations

import json
from uuid import UUID

from sqlalchemy import Engine, select
from sqlalchemy.orm import Session

from app.orchestration.models import NodeRunRecord, ProductionRun, ProductionRunStatus
from app.storage.errors import ProductionRunNotFoundError
from app.storage.orm import ProductionRunRow


def _to_domain(row: ProductionRunRow) -> ProductionRun:
    raw_node_states = json.loads(row.node_states_json)
    return ProductionRun(
        id=UUID(row.run_id),
        project_id=UUID(row.project_id),
        target_node=row.target_node,
        status=ProductionRunStatus(row.status),
        node_states={key: NodeRunRecord.model_validate(value) for key, value in raw_node_states.items()},
        created_at=row.created_at,
        updated_at=row.updated_at,
        started_at=row.started_at,
        completed_at=row.completed_at,
        stop_reason=row.stop_reason,
        waiting_gate=row.waiting_gate,
    )


def save_production_run(engine: Engine, run: ProductionRun) -> None:
    """Upsert by id, so a run's own record can be updated as it
    progresses across multiple run_until()/resume() calls."""
    with Session(engine) as session, session.begin():
        row = session.execute(
            select(ProductionRunRow).where(ProductionRunRow.run_id == str(run.id))
        ).scalar_one_or_none()
        if row is None:
            row = ProductionRunRow(run_id=str(run.id))
            session.add(row)
        row.project_id = str(run.project_id)
        row.target_node = run.target_node
        row.status = run.status.value
        row.node_states_json = json.dumps(
            {key: value.model_dump(mode="json") for key, value in run.node_states.items()}
        )
        row.created_at = run.created_at
        row.updated_at = run.updated_at
        row.started_at = run.started_at
        row.completed_at = run.completed_at
        row.stop_reason = run.stop_reason
        row.waiting_gate = run.waiting_gate


def get_production_run(engine: Engine, run_id: UUID) -> ProductionRun:
    with Session(engine) as session:
        row = session.execute(
            select(ProductionRunRow).where(ProductionRunRow.run_id == str(run_id))
        ).scalar_one_or_none()
        if row is None:
            raise ProductionRunNotFoundError(f"ProductionRun not found: {run_id}")
        return _to_domain(row)


def list_production_runs_for_project(engine: Engine, project_id: UUID) -> list[ProductionRun]:
    with Session(engine) as session:
        rows = (
            session.execute(
                select(ProductionRunRow)
                .where(ProductionRunRow.project_id == str(project_id))
                .order_by(ProductionRunRow.id)
            )
            .scalars()
            .all()
        )
        return [_to_domain(row) for row in rows]
