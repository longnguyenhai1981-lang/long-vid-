"""Feasibility-Engine-specific domain errors."""

from __future__ import annotations


class MissingIdeaArtifactError(Exception):
    """Raised when FeasibilityEngine cannot load a valid approved IdeaCandidate
    for the project (missing reference, missing artifact, or an id mismatch)."""


class MissingResearchArtifactError(Exception):
    """Raised when FeasibilityEngine cannot load a valid ResearchR0 for the
    project (missing reference, missing artifact, or an id mismatch)."""
