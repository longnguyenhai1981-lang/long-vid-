"""Project persistence. State updates always pass through the transition validator."""

from __future__ import annotations

from datetime import datetime, timezone
from uuid import UUID

from sqlalchemy import Engine, select
from sqlalchemy.orm import Session

from app.models.common import ProjectState
from app.models.project import Project, TemplateVersions
from app.storage.errors import ProjectNotFoundError
from app.storage.orm import ProjectRow
from app.workflow.transitions import validate_transition

_ARTIFACT_REFERENCE_FIELDS = {
    "idea_candidate_id",
    "research_r0_id",
    "feasibility_id",
    "research_r1_id",
    "narrative_plan_id",
    "packaging_prototype_id",
    "script_plan_id",
}


def _to_row(project: Project) -> ProjectRow:
    return ProjectRow(
        project_id=str(project.project_id),
        title_internal=project.title_internal,
        created_at=project.created_at,
        updated_at=project.updated_at,
        state=project.state.value,
        template_versions_json=project.template_versions.model_dump_json(),
        idea_candidate_id=_opt_str(project.idea_candidate_id),
        research_r0_id=_opt_str(project.research_r0_id),
        feasibility_id=_opt_str(project.feasibility_id),
        research_r1_id=_opt_str(project.research_r1_id),
        narrative_plan_id=_opt_str(project.narrative_plan_id),
        packaging_prototype_id=_opt_str(project.packaging_prototype_id),
        script_plan_id=_opt_str(project.script_plan_id),
    )


def _to_domain(row: ProjectRow) -> Project:
    return Project(
        project_id=UUID(row.project_id),
        title_internal=row.title_internal,
        created_at=row.created_at,
        updated_at=row.updated_at,
        state=ProjectState(row.state),
        template_versions=TemplateVersions.model_validate_json(row.template_versions_json),
        idea_candidate_id=_opt_uuid(row.idea_candidate_id),
        research_r0_id=_opt_uuid(row.research_r0_id),
        feasibility_id=_opt_uuid(row.feasibility_id),
        research_r1_id=_opt_uuid(row.research_r1_id),
        narrative_plan_id=_opt_uuid(row.narrative_plan_id),
        packaging_prototype_id=_opt_uuid(row.packaging_prototype_id),
        script_plan_id=_opt_uuid(row.script_plan_id),
    )


def _opt_str(value: UUID | None) -> str | None:
    return str(value) if value is not None else None


def _opt_uuid(value: str | None) -> UUID | None:
    return UUID(value) if value is not None else None


def create_project(engine: Engine, project: Project) -> None:
    """Insert a new project. Must start in NEW_PROJECT -- the graph's only entry state."""
    if project.state is not ProjectState.NEW_PROJECT:
        raise ValueError(
            f"New projects must start in NEW_PROJECT state, got {project.state.value}"
        )
    with Session(engine) as session, session.begin():
        session.add(_to_row(project))


def get_project(engine: Engine, project_id: UUID) -> Project:
    with Session(engine) as session:
        row = session.get(ProjectRow, str(project_id))
        if row is None:
            raise ProjectNotFoundError(f"Project not found: {project_id}")
        return _to_domain(row)


def list_projects(engine: Engine) -> list[Project]:
    with Session(engine) as session:
        rows = (
            session.execute(select(ProjectRow).order_by(ProjectRow.created_at)).scalars().all()
        )
        return [_to_domain(row) for row in rows]


def update_project_state(engine: Engine, project_id: UUID, new_state: ProjectState) -> Project:
    """Validate and apply a state transition. Raises InvalidStateTransitionError and
    leaves the stored state untouched if the transition is not in the approved graph.
    """
    with Session(engine) as session, session.begin():
        row = session.get(ProjectRow, str(project_id))
        if row is None:
            raise ProjectNotFoundError(f"Project not found: {project_id}")
        validate_transition(ProjectState(row.state), new_state)
        row.state = new_state.value
        row.updated_at = datetime.now(timezone.utc)
        session.flush()
        return _to_domain(row)


def update_artifact_reference(
    engine: Engine, project_id: UUID, field_name: str, artifact_id: UUID
) -> Project:
    """Point one of the project's artifact-reference fields at a stored artifact."""
    if field_name not in _ARTIFACT_REFERENCE_FIELDS:
        raise ValueError(f"Unknown artifact reference field: {field_name}")
    with Session(engine) as session, session.begin():
        row = session.get(ProjectRow, str(project_id))
        if row is None:
            raise ProjectNotFoundError(f"Project not found: {project_id}")
        setattr(row, field_name, str(artifact_id))
        row.updated_at = datetime.now(timezone.utc)
        session.flush()
        return _to_domain(row)
