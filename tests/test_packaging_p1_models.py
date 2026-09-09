from __future__ import annotations

from uuid import uuid4

import pytest
from pydantic import ValidationError

from app.models.packaging_p1 import FinalPackagingPlan


def _plan(**overrides) -> FinalPackagingPlan:
    kwargs = dict(
        packaging_prototype_id=uuid4(),
        script_plan_id=uuid4(),
        visual_plan_id=uuid4(),
        assembly_plan_id=uuid4(),
        title="Chiếc cầu tự xé nát chính nó",
        thumbnail_text="CHỈ VÌ GIÓ?",
        thumbnail_concept="Bridge deck twisting sharply; Tí confused in lower-right.",
        final_promise="A sturdy bridge tore itself apart in ordinary wind.",
        expected_payoff="The real aerodynamic mechanism, not resonance.",
        viewer_expectation="An honest investigation into a real physical mechanism.",
        rationale="Sharpens P0's promise now that the reveal is locked in the script.",
        risk_of_misleading="LOW",
    )
    kwargs.update(overrides)
    return FinalPackagingPlan(**kwargs)


def test_final_packaging_plan_constructs_with_required_fields():
    plan = _plan()
    assert plan.id is not None
    assert plan.risk_of_misleading.value == "LOW"
    assert plan.notes is None


def test_final_packaging_plan_reuses_risk_level_enum():
    for risk in ("LOW", "MEDIUM", "HIGH"):
        assert _plan(risk_of_misleading=risk).risk_of_misleading.value == risk


def test_final_packaging_plan_thumbnail_text_optional():
    plan = _plan(thumbnail_text=None)
    assert plan.thumbnail_text is None


def test_final_packaging_plan_accepts_blank_text_at_pydantic_level():
    """Deliberate: non-blank enforcement for these fields is a BUSINESS
    concern (app/engines/packaging_p1/validation.py), not a Pydantic
    model_validator -- so a blank value must still construct successfully
    here; only the engine's own business validation rejects it."""
    plan = _plan(title="", thumbnail_concept="", final_promise="")
    assert plan.title == ""
    assert plan.thumbnail_concept == ""
    assert plan.final_promise == ""


def test_final_packaging_plan_rejects_invalid_risk_level():
    with pytest.raises(ValidationError):
        _plan(risk_of_misleading="EXTREME")


def test_final_packaging_plan_rejects_unknown_fields():
    with pytest.raises(ValidationError):
        _plan(unexpected_field="nope")


def test_final_packaging_plan_accepts_optional_notes():
    plan = _plan(notes="Consider a stronger thumbnail once real footage is sourced.")
    assert plan.notes == "Consider a stronger thumbnail once real footage is sourced."
