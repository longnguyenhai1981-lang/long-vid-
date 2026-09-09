"""Script-Engine-specific domain errors."""

from __future__ import annotations


class MissingIdeaArtifactError(Exception):
    """Raised when ScriptEngine cannot load a valid approved IdeaCandidate for
    the project (missing reference, missing artifact, or an id mismatch)."""


class MissingResearchPackageArtifactError(Exception):
    """Raised when ScriptEngine cannot load a valid ResearchPackage for the
    project (missing reference, missing artifact, or an id mismatch)."""


class MissingNarrativePlanArtifactError(Exception):
    """Raised when ScriptEngine cannot load a valid NarrativePlan for the
    project (missing reference, missing artifact, or an id mismatch)."""


class MissingPackagingPrototypeArtifactError(Exception):
    """Raised when ScriptEngine cannot load a valid PackagingPrototype for the
    project (missing reference, missing artifact, or an id mismatch)."""


class ScriptBusinessValidationError(Exception):
    """Raised when a ScriptPlan is Pydantic-valid but still fails business
    validation (beat/line structure, narrative-node integrity and order,
    claim referential integrity, duration sanity) after one bounded
    correction attempt."""

    def __init__(self, issues: list[str]):
        self.issues = issues
        super().__init__(
            f"ScriptPlan failed business validation after one correction attempt: {issues}"
        )
