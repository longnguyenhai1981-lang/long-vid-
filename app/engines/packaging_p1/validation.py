"""Deterministic Packaging P1 normalization and business validation.

normalize_final_packaging_plan() runs BEFORE validation, on every
generation attempt (initial and corrected): it overwrites
packaging_prototype_id/script_plan_id/visual_plan_id/assembly_plan_id from
the real upstream artifacts actually loaded, rather than trusting the LLM
to reproduce them -- per the patch's explicit instruction not to spend a
business-correction call on an immutable id whose truth the engine already
knows. validate_final_packaging_plan() then only ever sees an
already-normalized plan.

Unlike Phase 13/14/15, Phase 16 has no structural coverage/order
relationship to check -- title and thumbnail direction are freeform
creative text, not a line-by-line structural mapping. So business
validation here is deliberately simple: every field the patch's own
"BUSINESS VALIDATION" section (26) lists as non-blank (title,
thumbnail_concept, final_promise, expected_payoff, viewer_expectation,
rationale, and thumbnail_text when present) is checked as a *business*
concern here, not a Pydantic model_validator -- so that an LLM response
which is schema-valid but leaves a required field blank is a genuine
business-correction case (patch section 33's explicit test), not a
structured-retry case. This is a deliberate deviation from Phase 13-15's
usual "prefer Pydantic for non-blank checks" convention, made because this
phase's own patch explicitly names "business correction for a blank
required field" as a required test scenario.
"""

from __future__ import annotations

from app.models.packaging import PackagingPrototype
from app.models.packaging_p1 import FinalPackagingPlan
from app.models.script import ScriptPlan
from app.models.visual import VisualPlan
from app.models.assembly import AssemblyPlan

_REQUIRED_TEXT_FIELDS = (
    "title",
    "thumbnail_concept",
    "final_promise",
    "expected_payoff",
    "viewer_expectation",
    "rationale",
)


def normalize_final_packaging_plan(
    plan: FinalPackagingPlan,
    packaging_prototype: PackagingPrototype,
    script_plan: ScriptPlan,
    visual_plan: VisualPlan,
    assembly_plan: AssemblyPlan,
) -> FinalPackagingPlan:
    """Deterministically overwrite the four upstream-reference ids from
    real loaded artifacts. Documented normalized fields:
    FinalPackagingPlan.packaging_prototype_id, script_plan_id,
    visual_plan_id, assembly_plan_id."""
    return plan.model_copy(
        update={
            "packaging_prototype_id": packaging_prototype.id,
            "script_plan_id": script_plan.id,
            "visual_plan_id": visual_plan.id,
            "assembly_plan_id": assembly_plan.id,
        }
    )


def validate_final_packaging_plan(plan: FinalPackagingPlan) -> list[str]:
    """Return human-readable business-rule violations. Empty list means
    valid. Deliberately does not attempt deterministic clickbait/promise
    semantics (patch section 26) -- that judgment stays LLM-evaluated."""
    issues: list[str] = []

    for field_name in _REQUIRED_TEXT_FIELDS:
        value: str = getattr(plan, field_name)
        if not value or not value.strip():
            issues.append(f"{field_name} cannot be blank")

    if plan.thumbnail_text is not None and not plan.thumbnail_text.strip():
        issues.append("thumbnail_text is present but blank after trimming")

    return issues
