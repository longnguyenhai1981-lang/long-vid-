"""HumanApproval: a human gate decision recorded against a project stage."""

from __future__ import annotations

from datetime import datetime
from uuid import UUID, uuid4

from pydantic import Field, model_validator

from app.models.common import ApprovalStatus, MotilyModel, ProjectState


class HumanApproval(MotilyModel):
    approval_id: UUID = Field(default_factory=uuid4)
    project_id: UUID
    stage: ProjectState
    status: ApprovalStatus
    user_feedback: str | None = None
    created_at: datetime

    @model_validator(mode="after")
    def _check_timestamp(self) -> "HumanApproval":
        if self.created_at.tzinfo is None:
            raise ValueError("created_at must be timezone-aware")
        return self
