"""Timing-/Assembly-Planning-Engine-specific domain errors."""

from __future__ import annotations


class MissingScriptPlanArtifactError(Exception):
    """Raised when the engine cannot load a valid ScriptPlan for the project
    (missing reference, missing artifact, or an id mismatch)."""


class MissingVoicePlanArtifactError(Exception):
    """Raised when the engine cannot load a valid VoicePlan for the project
    (missing artifact, or the artifact does not validate)."""


class MissingVisualPlanArtifactError(Exception):
    """Raised when the engine cannot load a valid VisualPlan for the project
    (missing artifact, or the artifact does not validate)."""


class StaleVoicePlanError(Exception):
    """Raised when the current VoicePlan was generated for a ScriptPlan
    other than the one the project currently references (VoicePlan.
    script_plan_id != Project.script_plan_id). A real production-integrity
    gate, checked before any LLM call, ModuleRun, or write."""


class StaleVisualPlanError(Exception):
    """Raised when the current VisualPlan was generated for a ScriptPlan or
    VoicePlan other than the ones currently current (VisualPlan.
    script_plan_id != Project.script_plan_id, or VisualPlan.voice_plan_id
    != the current VoicePlan.id). A real production-integrity gate, checked
    before any LLM call, ModuleRun, or write."""


class AssemblyPlanBusinessValidationError(Exception):
    """Raised when an AssemblyPlan is Pydantic-valid but still fails
    deterministic business validation (visual-beat coverage/order, script
    line coverage, voice-chunk overlap, timeline contiguity, duration
    tolerance) after one bounded correction attempt."""

    def __init__(self, issues: list[str]):
        self.issues = issues
        super().__init__(
            f"AssemblyPlan failed business validation after one correction attempt: {issues}"
        )
