"""Voice-Planning-Engine-specific domain errors."""

from __future__ import annotations


class MissingScriptPlanArtifactError(Exception):
    """Raised when the engine cannot load a valid ScriptPlan for the project
    (missing reference, missing artifact, or an id mismatch)."""


class VoicePlanBusinessValidationError(Exception):
    """Raised when a VoicePlan is Pydantic-valid but still fails deterministic
    business validation (line coverage/order/adjacency against the current
    ScriptPlan) after one bounded correction attempt."""

    def __init__(self, issues: list[str]):
        self.issues = issues
        super().__init__(
            f"VoicePlan failed business validation after one correction attempt: {issues}"
        )
