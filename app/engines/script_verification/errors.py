"""Script-Verification-Engine-specific domain errors."""

from __future__ import annotations


class MissingResearchPackageArtifactError(Exception):
    """Raised when the engine cannot load a valid ResearchPackage for the
    project (missing reference, missing artifact, or an id mismatch)."""


class MissingNarrativePlanArtifactError(Exception):
    """Raised when the engine cannot load a valid NarrativePlan for the
    project (missing reference, missing artifact, or an id mismatch)."""


class MissingPackagingPrototypeArtifactError(Exception):
    """Raised when the engine cannot load a valid PackagingPrototype for the
    project (missing reference, missing artifact, or an id mismatch)."""


class MissingScriptPlanArtifactError(Exception):
    """Raised when the engine cannot load a valid ScriptPlan for the project
    (missing reference, missing artifact, or an id mismatch)."""


class ScriptVerificationBusinessError(Exception):
    """Raised when a ScriptVerificationReport is Pydantic-valid but still fails
    to acknowledge a deterministic (defense-in-depth) claim-reference finding
    after one bounded correction attempt."""

    def __init__(self, issues: list[str]):
        self.issues = issues
        super().__init__(
            "ScriptVerificationReport failed business validation after one "
            f"correction attempt: {issues}"
        )
