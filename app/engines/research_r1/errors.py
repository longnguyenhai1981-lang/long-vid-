"""R1-specific domain errors."""

from __future__ import annotations


class MissingIdeaArtifactError(Exception):
    """Raised when R1ResearchEngine cannot load a valid approved IdeaCandidate
    for the project (missing reference, missing artifact, or an id mismatch)."""


class MissingResearchR0ArtifactError(Exception):
    """Raised when R1ResearchEngine cannot load a valid ResearchR0 for the
    project (missing reference, missing artifact, or an id mismatch)."""


class MissingFeasibilityArtifactError(Exception):
    """Raised when R1ResearchEngine cannot load a valid FeasibilityReport for
    the project (missing reference, missing artifact, or an id mismatch)."""


class FeasibilityNotPassedError(Exception):
    """Raised when the stored FeasibilityReport.status is not PASS. R1 may only
    run once feasibility was approved -- this is a deterministic precondition,
    not something R1 re-judges."""


class R1BusinessValidationError(Exception):
    """Raised when a ResearchPackage is Pydantic-valid but still fails R1's
    deterministic business validation after one bounded correction attempt
    (source URL provenance, claim/source referential integrity, id uniqueness,
    evidence-by-status requirements)."""

    def __init__(self, issues: list[str]):
        self.issues = issues
        super().__init__(
            "ResearchPackage failed business validation after one correction "
            f"attempt: {issues}"
        )
