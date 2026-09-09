"""R0-specific domain errors."""

from __future__ import annotations


class MissingIdeaArtifactError(Exception):
    """Raised when R0ResearchEngine cannot load a valid approved IdeaCandidate
    for the project (missing reference, missing artifact, or an id mismatch)."""


class ResearchSourceHallucinationError(Exception):
    """Raised when ResearchR0.candidate_sources still references URLs absent from
    the retrieved evidence set after one bounded correction attempt."""

    def __init__(self, unknown_urls: list[str]):
        self.unknown_urls = unknown_urls
        super().__init__(
            "ResearchR0 candidate_sources referenced URLs not present in retrieved "
            f"evidence, even after one correction attempt: {unknown_urls}"
        )
