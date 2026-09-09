"""Deterministic overall-status derivation for FeasibilityReport.

The LLM produces an overall status because the locked schema requires one,
but it is never trusted: the engine always overrides it from the five axis
statuses using this fixed rule, the same way Phase 5 overrides
ResearchR0.idea_id rather than trusting the model to echo it correctly.
"""

from __future__ import annotations

from app.models.common import GateStatus
from app.models.feasibility import FeasibilityReport


def derive_overall_status(report: FeasibilityReport) -> GateStatus:
    """If any axis is REJECT, overall is REJECT. Else if any axis is REFRAME,
    overall is REFRAME. Else overall is PASS."""
    axis_statuses = [
        report.audience.status,
        report.science.status,
        report.narrative.status,
        report.visual.status,
        report.production.status,
    ]
    if GateStatus.REJECT in axis_statuses:
        return GateStatus.REJECT
    if GateStatus.REFRAME in axis_statuses:
        return GateStatus.REFRAME
    return GateStatus.PASS
