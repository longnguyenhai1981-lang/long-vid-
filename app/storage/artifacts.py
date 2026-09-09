"""Artifact persistence: validated Pydantic module outputs stored as JSON payloads.

One row per (project_id, artifact_type); save_artifact upserts, so only the
current artifact of each type is kept -- no version history in Phase 2.
This keeps ResearchPackage, NarrativePlan, ScriptPlan, etc. un-normalized,
each remaining the single source of truth for its own domain (see
docs/TECHNICAL_SPEC_v0.1.md).
"""

from __future__ import annotations

from datetime import datetime, timezone
from uuid import UUID

from pydantic import BaseModel, ValidationError
from sqlalchemy import Engine
from sqlalchemy.orm import Session

from app.storage.errors import ArtifactNotFoundError, ArtifactValidationError
from app.storage.orm import ArtifactRow


def save_artifact(
    engine: Engine,
    project_id: UUID,
    artifact_type: str,
    artifact_model: BaseModel,
    schema_version: str = "0.1",
) -> None:
    """Validate-and-store a domain model as the current artifact of its type."""
    artifact_id = getattr(artifact_model, "id", None)
    payload_json = artifact_model.model_dump_json()
    with Session(engine) as session, session.begin():
        row = session.get(ArtifactRow, (str(project_id), artifact_type))
        if row is None:
            row = ArtifactRow(project_id=str(project_id), artifact_type=artifact_type)
            session.add(row)
        row.artifact_id = str(artifact_id) if artifact_id is not None else None
        row.schema_version = schema_version
        row.payload_json = payload_json
        row.created_at = datetime.now(timezone.utc)


def get_artifact(
    engine: Engine,
    project_id: UUID,
    artifact_type: str,
    model_class: type[BaseModel],
) -> BaseModel:
    """Fetch and re-validate the current artifact of a type through model_class."""
    with Session(engine) as session:
        row = session.get(ArtifactRow, (str(project_id), artifact_type))
        if row is None:
            raise ArtifactNotFoundError(
                f"No {artifact_type} artifact stored for project {project_id}"
            )
        try:
            return model_class.model_validate_json(row.payload_json)
        except ValidationError as exc:
            raise ArtifactValidationError(
                f"Stored {artifact_type} payload for project {project_id} "
                f"does not validate as {model_class.__name__}"
            ) from exc
