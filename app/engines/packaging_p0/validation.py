"""Deterministic Packaging P0 business validation.

Runs AFTER generate_structured returns a Pydantic-valid PackagingPrototype --
Phase 3's structured layer is never touched. Checks only what is genuinely
structural: the locked PackagingPrototype model has no non-blank constraints
of its own (Phase 9 is authorized to add only an `id` field to it, nothing
else), so blank-string checks live here instead.

Promise integrity (is the promise actually deliverable by the ResearchPackage
and NarrativePlan) is explicitly NOT checked here -- Phase 9 keeps that
LLM-evaluated via the prompt, per the approved scope, rather than building a
second LLM critic/debate agent or a fake deterministic claim-extraction
engine. risk_of_misleading == HIGH is also not a validation failure here --
it is a legitimate, honestly-reported outcome; see the engine and
app/review/service.py for how HIGH risk is actually handled.
"""

from __future__ import annotations

from app.models.packaging import PackagingPrototype

_REQUIRED_TEXT_FIELDS = ("promise", "title_direction", "thumbnail_conflict", "viewer_expectation")


def validate_packaging_prototype(prototype: PackagingPrototype) -> list[str]:
    """Return human-readable business-rule violations. Empty list means valid."""
    issues: list[str] = []
    for field_name in _REQUIRED_TEXT_FIELDS:
        value: str = getattr(prototype, field_name)
        if not value or not value.strip():
            issues.append(f"{field_name} cannot be blank")
    return issues
