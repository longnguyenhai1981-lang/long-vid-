"""Project: the top-level record tracking one video through the pipeline."""

from __future__ import annotations

from datetime import datetime
from uuid import UUID, uuid4

from pydantic import Field, model_validator

from app.models.common import MotilyModel, ProjectState, non_blank


class TemplateVersions(MotilyModel):
    idea: str = "0.1"
    research: str = "0.1"
    feasibility: str = "0.1"
    narrative: str = "0.1"
    script: str = "0.1"


class Project(MotilyModel):
    project_id: UUID = Field(default_factory=uuid4)
    title_internal: str
    created_at: datetime
    updated_at: datetime
    state: ProjectState
    template_versions: TemplateVersions = Field(default_factory=TemplateVersions)

    idea_candidate_id: UUID | None = None
    research_r0_id: UUID | None = None
    feasibility_id: UUID | None = None
    research_r1_id: UUID | None = None
    narrative_plan_id: UUID | None = None
    packaging_prototype_id: UUID | None = None
    script_plan_id: UUID | None = None

    @model_validator(mode="after")
    def _check_invariants(self) -> "Project":
        non_blank(self.title_internal, "title_internal")
        if self.created_at.tzinfo is None or self.updated_at.tzinfo is None:
            raise ValueError("created_at and updated_at must be timezone-aware")
        if self.updated_at < self.created_at:
            raise ValueError("updated_at cannot be earlier than created_at")
        return self
