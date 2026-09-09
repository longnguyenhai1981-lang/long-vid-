"""Minimal typed visual provider configuration.

No API key field: Phase 19 ships no concrete remote provider to consume one
(see docs/TECHNICAL_SPEC_v0.1.md). When a real provider is added, its key
must be read from an environment variable at call time -- never hard-coded,
logged, persisted to SQLite, or placed in ModuleRun/manifest metadata.
"""

from __future__ import annotations

from pydantic import model_validator

from app.models.common import MotilyModel, VisualOutputFormat, non_blank


class VisualSettings(MotilyModel):
    provider: str
    output_format: VisualOutputFormat = VisualOutputFormat.PNG
    max_provider_retries: int = 1

    @model_validator(mode="after")
    def _check_invariants(self) -> "VisualSettings":
        non_blank(self.provider, "provider")
        if self.max_provider_retries < 0:
            raise ValueError("max_provider_retries must be >= 0")
        return self
