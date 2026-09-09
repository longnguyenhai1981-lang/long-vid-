"""Narrative-Engine-specific domain errors."""

from __future__ import annotations


class MissingIdeaArtifactError(Exception):
    """Raised when NarrativeEngine cannot load a valid approved IdeaCandidate
    for the project (missing reference, missing artifact, or an id mismatch)."""


class MissingResearchPackageArtifactError(Exception):
    """Raised when NarrativeEngine cannot load a valid ResearchPackage for the
    project (missing reference, missing artifact, or an id mismatch)."""


class NarrativeBusinessValidationError(Exception):
    """Raised when a NarrativePlan is Pydantic-valid but still fails business
    validation (question-ladder structure, claim referential integrity) after
    one bounded correction attempt."""

    def __init__(self, issues: list[str]):
        self.issues = issues
        super().__init__(
            f"NarrativePlan failed business validation after one correction attempt: {issues}"
        )
