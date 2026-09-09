"""Packaging-P0-Engine-specific domain errors."""

from __future__ import annotations


class MissingIdeaArtifactError(Exception):
    """Raised when PackagingP0Engine cannot load a valid approved IdeaCandidate
    for the project (missing reference, missing artifact, or an id mismatch)."""


class MissingResearchPackageArtifactError(Exception):
    """Raised when PackagingP0Engine cannot load a valid ResearchPackage for the
    project (missing reference, missing artifact, or an id mismatch)."""


class MissingNarrativePlanArtifactError(Exception):
    """Raised when PackagingP0Engine cannot load a valid NarrativePlan for the
    project (missing reference, missing artifact, or an id mismatch)."""


class PackagingBusinessValidationError(Exception):
    """Raised when a PackagingPrototype is Pydantic-valid but still fails
    business validation (blank promise/title_direction/thumbnail_conflict/
    viewer_expectation) after one bounded correction attempt.

    Note: risk_of_misleading == HIGH is deliberately NOT a business-validation
    failure -- it is a legitimate, honestly-reported outcome that the engine
    persists as-is. Only a human approval action rejects it (see
    PackagingRiskTooHighError in app/review/errors.py)."""

    def __init__(self, issues: list[str]):
        self.issues = issues
        super().__init__(
            f"PackagingPrototype failed business validation after one correction attempt: {issues}"
        )
