from __future__ import annotations

import pytest
from pydantic import ValidationError

from app.models.feasibility import FeasibilityReport, ProductionEvaluation, SubEvaluation


def _sub(status="PASS"):
    return SubEvaluation(status=status, reason="ok")


def test_valid_feasibility_report():
    report = FeasibilityReport(
        status="PASS",
        audience=_sub(),
        science=_sub(),
        narrative=_sub(),
        visual=_sub(),
        production=ProductionEvaluation(
            status="PASS", estimated_complexity="low", reason="ok"
        ),
    )
    assert report.status.value == "PASS"


def test_invalid_status_rejects():
    with pytest.raises(ValidationError):
        FeasibilityReport(
            status="MAYBE",
            audience=_sub(),
            science=_sub(),
            narrative=_sub(),
            visual=_sub(),
            production=ProductionEvaluation(
                status="PASS", estimated_complexity="low", reason="ok"
            ),
        )


def test_missing_sub_evaluation_rejects():
    with pytest.raises(ValidationError):
        FeasibilityReport(
            status="PASS",
            audience=_sub(),
            science=_sub(),
            narrative=_sub(),
            production=ProductionEvaluation(
                status="PASS", estimated_complexity="low", reason="ok"
            ),
        )
