"""Deterministic R1 business validation.

Runs AFTER generate_structured returns a Pydantic-valid ResearchPackage --
Phase 3's structured layer is never touched. Mirrors Phase 5's R0
source-hallucination guard, extended to cover every R1 referential-integrity
and evidence-by-status rule in one pass so a single correction call can
address all of them together.
"""

from __future__ import annotations

from app.models.common import ClaimStatus
from app.models.research import ResearchPackage
from app.research.models import RetrievedSource

_EVIDENCE_REQUIRED_STATUSES = {ClaimStatus.SAFE, ClaimStatus.QUALIFIED, ClaimStatus.DISPUTED}


def validate_research_package(
    package: ResearchPackage, evidence: list[RetrievedSource]
) -> list[str]:
    """Return human-readable business-rule violations. Empty list means valid."""
    issues: list[str] = []
    retrieved_urls = {source.url for source in evidence}

    claim_ids = [claim.claim_id for claim in package.claims]
    source_ids = [source.source_id for source in package.sources]
    claim_id_set = set(claim_ids)
    source_id_set = set(source_ids)

    issues.extend(_duplicate_issues("claim_id", claim_ids))
    issues.extend(_duplicate_issues("source_id", source_ids))

    # Source URL provenance (section 15): no invented URLs.
    for source in package.sources:
        if source.url is not None and source.url not in retrieved_urls:
            issues.append(
                f"Source {source.source_id} references a URL not present in "
                f"retrieved evidence: {source.url}"
            )

    # Claim -> Source referential integrity (section 16).
    for claim in package.claims:
        for source_id in claim.source_ids:
            if source_id not in source_id_set:
                issues.append(
                    f"Claim {claim.claim_id} references unknown source_id: {source_id}"
                )

    # Source -> Claim referential integrity (section 16).
    for source in package.sources:
        for claim_id in source.supports_claims:
            if claim_id not in claim_id_set:
                issues.append(
                    f"Source {source.source_id} references unknown claim_id: {claim_id}"
                )

    # Evidence-by-status requirement (section 18): SAFE/QUALIFIED/DISPUTED need
    # at least one source_id; UNCERTAIN/PROHIBITED may have zero.
    for claim in package.claims:
        if claim.status in _EVIDENCE_REQUIRED_STATUSES and not claim.source_ids:
            issues.append(
                f"Claim {claim.claim_id} has status {claim.status.value} but no "
                f"source_ids (SAFE/QUALIFIED/DISPUTED claims require at least one)"
            )

    return issues


def _duplicate_issues(field_name: str, values: list[str]) -> list[str]:
    seen: set[str] = set()
    duplicates: set[str] = set()
    for value in values:
        if value in seen:
            duplicates.add(value)
        seen.add(value)
    return [f"Duplicate {field_name}: {value}" for value in sorted(duplicates)]
