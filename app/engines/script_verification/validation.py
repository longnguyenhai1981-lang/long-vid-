"""Deterministic Script Verification support: status normalization and a
defense-in-depth claim-reference pre-check.

The locked ScriptVerificationReport (Phase 1) holds its findings as plain
`list[str]` -- not structured line/claim references -- so most of the
"business validation" other engines perform (referential integrity against
typed ids) has no structural target here. Per docs/TECHNICAL_SPEC_v0.1.md,
Phase 11: two things ARE genuinely checkable without regex-parsing free text:

1. Status must be internally consistent with the issue collections (PASS iff
   they are all empty) -- this is *always* deterministically normalized,
   never merely validated-and-rejected, per the spec's own instruction to
   "implement the simplest deterministic normalization compatible with it."
2. A defense-in-depth pre-check on the ScriptPlan being reviewed (independent
   of ScriptEngine's own, already-locked validation) can name specific
   line_ids that reference an unknown or PROHIBITED claim. Whether the
   report's own unsupported_lines *mentions* one of those known-bad line_ids
   is a plain substring containment check, not a regex extraction of new
   information from prose -- so it is safe to check deterministically and is
   the one genuine "business correction" trigger for this engine.
"""

from __future__ import annotations

from app.models.common import ClaimStatus, GateStatus
from app.models.research import ResearchPackage
from app.models.script import ScriptPlan, ScriptVerificationReport


def normalize_status(report: ScriptVerificationReport) -> ScriptVerificationReport:
    """Always enforce the PASS / non-PASS boundary from the issue collections;
    never trust an inconsistent LLM-supplied status. When issues exist but the
    model inconsistently claimed PASS, default to the milder REFRAME rather
    than manufacturing a REJECT judgment the model never made -- but when the
    model already reported REFRAME or REJECT, its severity choice is kept
    as-is; that judgment is not something deterministic code should
    second-guess."""
    has_issues = bool(
        report.unsupported_lines or report.overstated_lines or report.dangerous_simplifications
    )
    if not has_issues:
        normalized = GateStatus.PASS
    elif report.status is GateStatus.PASS:
        normalized = GateStatus.REFRAME
    else:
        normalized = report.status

    if normalized == report.status:
        return report
    return report.model_copy(update={"status": normalized})


def find_deterministic_claim_issues(script_plan: ScriptPlan, research_package: ResearchPackage) -> set[str]:
    """Defense in depth: ScriptEngine (Phase 10, locked) already guarantees
    every ScriptLine.claim_ids entry exists and is never PROHIBITED, but
    verification checks independently. Returns the line_ids that violate
    this, regardless of what the LLM says about them."""
    valid_claim_ids = {claim.claim_id for claim in research_package.claims}
    prohibited_claim_ids = {
        claim.claim_id for claim in research_package.claims if claim.status is ClaimStatus.PROHIBITED
    }
    bad_line_ids: set[str] = set()
    for beat in script_plan.beats:
        for line in beat.lines:
            for claim_id in line.claim_ids:
                if claim_id not in valid_claim_ids or claim_id in prohibited_claim_ids:
                    bad_line_ids.add(line.line_id)
    return bad_line_ids


def find_unacknowledged_claim_issues(
    report: ScriptVerificationReport, deterministic_bad_line_ids: set[str]
) -> list[str]:
    """Which deterministically-known-bad line_ids are NOT mentioned anywhere in
    the report's unsupported_lines. A plain substring containment check
    against a known token -- not a regex parse of unstructured prose."""
    unacknowledged = [
        line_id
        for line_id in deterministic_bad_line_ids
        if not any(line_id in entry for entry in report.unsupported_lines)
    ]
    return sorted(unacknowledged)
