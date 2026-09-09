"""Visual-Planning-Engine-specific domain errors."""

from __future__ import annotations


class MissingNarrativePlanArtifactError(Exception):
    """Raised when the engine cannot load a valid NarrativePlan for the
    project (missing reference, missing artifact, or an id mismatch)."""


class MissingScriptPlanArtifactError(Exception):
    """Raised when the engine cannot load a valid ScriptPlan for the project
    (missing reference, missing artifact, or an id mismatch)."""


class MissingVoicePlanArtifactError(Exception):
    """Raised when the engine cannot load a valid VoicePlan for the project
    (missing artifact, or the artifact does not validate)."""


class MissingResearchPackageArtifactError(Exception):
    """Raised when the engine cannot load a valid ResearchPackage for the
    project (missing reference, missing artifact, or an id mismatch)."""


class StaleVoicePlanError(Exception):
    """Raised when the current VoicePlan was generated for a ScriptPlan
    other than the one the project currently references (VoicePlan.
    script_plan_id != Project.script_plan_id). A real production-integrity
    gate, checked before any LLM call, ModuleRun, or write -- Visual
    Planning must never be built on a stale delivery plan."""


class VisualPlanBusinessValidationError(Exception):
    """Raised when a VisualPlan is Pydantic-valid but still fails
    deterministic business validation (line coverage/order/adjacency,
    narrative-node alignment, evidence-source integrity, complexity budget)
    after one bounded correction attempt."""

    def __init__(self, issues: list[str]):
        self.issues = issues
        super().__init__(
            f"VisualPlan failed business validation after one correction attempt: {issues}"
        )
