"""ModuleRun: an audit-log record of one pipeline module execution.

This is metadata only — nothing in Phase 1/2 actually executes a module.
"""

from __future__ import annotations

from datetime import datetime
from uuid import UUID, uuid4

from pydantic import Field, model_validator

from app.models.common import ModuleRunStatus, MotilyModel, non_blank


class ModuleRun(MotilyModel):
    run_id: UUID = Field(default_factory=uuid4)
    project_id: UUID
    module: str
    module_version: str
    started_at: datetime
    completed_at: datetime | None = None
    input_ids: list[str] = Field(default_factory=list)
    output_id: str | None = None
    status: ModuleRunStatus
    error_message: str | None = None

    @model_validator(mode="after")
    def _check_invariants(self) -> "ModuleRun":
        non_blank(self.module, "module")
        non_blank(self.module_version, "module_version")

        if self.started_at.tzinfo is None:
            raise ValueError("started_at must be timezone-aware")

        if self.completed_at is not None:
            if self.completed_at.tzinfo is None:
                raise ValueError("completed_at must be timezone-aware")
            if self.completed_at < self.started_at:
                raise ValueError("completed_at cannot be earlier than started_at")

        if self.status is ModuleRunStatus.SUCCESS and self.completed_at is None:
            raise ValueError("SUCCESS status requires completed_at")

        if self.status is ModuleRunStatus.FAILED:
            if self.completed_at is None:
                raise ValueError("FAILED status requires completed_at")
            non_blank(self.error_message or "", "error_message")

        return self
