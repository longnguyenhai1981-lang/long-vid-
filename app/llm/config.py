"""Minimal typed LLM provider configuration.

No API key field: Phase 3 ships no concrete remote provider to consume one
(see docs/TECHNICAL_SPEC_v0.1.md). When a real provider is added, its key
must be read from an environment variable at call time -- never hard-coded,
logged, persisted to SQLite, or placed in ModuleRun/audit metadata.
"""

from __future__ import annotations

from pydantic import model_validator

from app.models.common import MotilyModel, non_blank


class LLMSettings(MotilyModel):
    provider: str
    default_model: str
    max_structured_retries: int = 2
    default_temperature: float | None = None
    default_max_output_tokens: int | None = None

    @model_validator(mode="after")
    def _check_invariants(self) -> "LLMSettings":
        non_blank(self.provider, "provider")
        non_blank(self.default_model, "default_model")
        if self.max_structured_retries < 0:
            raise ValueError("max_structured_retries must be >= 0")
        return self
