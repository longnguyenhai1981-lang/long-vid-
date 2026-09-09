"""Packaging-P1-Engine-specific domain errors."""

from __future__ import annotations


class MissingPackagingPrototypeArtifactError(Exception):
    """Raised when the engine cannot load the currently-referenced,
    approved P0 PackagingPrototype for the project (missing reference,
    missing artifact, or an id mismatch). Packaging P1 must never build on
    a stale or accidentally-supplied older PackagingPrototype."""


class MissingNarrativePlanArtifactError(Exception):
    """Raised when the engine cannot load a valid NarrativePlan for the
    project (missing reference, missing artifact, or an id mismatch)."""


class MissingResearchPackageArtifactError(Exception):
    """Raised when the engine cannot load a valid ResearchPackage for the
    project (missing reference, missing artifact, or an id mismatch)."""


class MissingScriptPlanArtifactError(Exception):
    """Raised when the engine cannot load a valid ScriptPlan for the project
    (missing reference, missing artifact, or an id mismatch)."""


class MissingVoicePlanArtifactError(Exception):
    """Raised when the engine cannot load a valid VoicePlan for the project
    (missing artifact, or the artifact does not validate). VoicePlan is
    never referenced by FinalPackagingPlan itself -- it is loaded only to
    verify the freshness chain VisualPlan/AssemblyPlan depend on."""


class MissingVisualPlanArtifactError(Exception):
    """Raised when the engine cannot load a valid VisualPlan for the
    project (missing artifact, or the artifact does not validate)."""


class MissingAssemblyPlanArtifactError(Exception):
    """Raised when the engine cannot load a valid AssemblyPlan for the
    project (missing artifact, or the artifact does not validate)."""


class StaleVoicePlanError(Exception):
    """Raised when the current VoicePlan was generated for a ScriptPlan
    other than the one the project currently references. A real
    production-integrity gate: even though FinalPackagingPlan never
    references VoicePlan directly, VisualPlan/AssemblyPlan's own freshness
    is only meaningful relative to a VoicePlan that is itself fresh."""


class StaleVisualPlanError(Exception):
    """Raised when the current VisualPlan was generated for a ScriptPlan or
    VoicePlan other than the ones currently current (VisualPlan.
    script_plan_id != current ScriptPlan.id, or VisualPlan.voice_plan_id
    != current VoicePlan.id). A real production-integrity gate, checked
    before any LLM call, ModuleRun, or write."""


class StaleAssemblyPlanError(Exception):
    """Raised when the current AssemblyPlan was generated for a ScriptPlan,
    VoicePlan, or VisualPlan other than the ones currently current. A real
    production-integrity gate, checked before any LLM call, ModuleRun, or
    write."""


class PackagingP1BusinessValidationError(Exception):
    """Raised when a FinalPackagingPlan is Pydantic-valid but still fails
    deterministic business validation (a required field is blank) after
    one bounded correction attempt."""

    def __init__(self, issues: list[str]):
        self.issues = issues
        super().__init__(
            f"FinalPackagingPlan failed business validation after one correction attempt: {issues}"
        )
