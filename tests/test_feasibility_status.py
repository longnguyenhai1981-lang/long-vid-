from __future__ import annotations

from app.engines.feasibility.status import derive_overall_status
from app.models.feasibility import FeasibilityReport, ProductionEvaluation, SubEvaluation


def _report(audience="PASS", science="PASS", narrative="PASS", visual="PASS", production="PASS", overall="PASS"):
    return FeasibilityReport(
        status=overall,
        audience=SubEvaluation(status=audience, reason="r"),
        science=SubEvaluation(status=science, reason="r"),
        narrative=SubEvaluation(status=narrative, reason="r"),
        visual=SubEvaluation(status=visual, reason="r"),
        production=ProductionEvaluation(status=production, estimated_complexity="LOW", reason="r"),
    )


def test_all_pass_derives_pass():
    assert derive_overall_status(_report()).value == "PASS"


def test_single_reframe_axis_derives_reframe():
    assert derive_overall_status(_report(narrative="REFRAME")).value == "REFRAME"


def test_single_reject_axis_derives_reject():
    assert derive_overall_status(_report(science="REJECT")).value == "REJECT"


def test_reject_takes_priority_over_reframe():
    assert derive_overall_status(_report(narrative="REFRAME", science="REJECT")).value == "REJECT"


def test_llm_overall_pass_but_axis_reframe_is_overridden():
    report = _report(visual="REFRAME", overall="PASS")
    assert derive_overall_status(report).value == "REFRAME"


def test_llm_overall_pass_but_axis_reject_is_overridden():
    report = _report(production="REJECT", overall="PASS")
    assert derive_overall_status(report).value == "REJECT"


def test_llm_overall_reframe_but_all_axes_pass_is_overridden():
    report = _report(overall="REFRAME")
    assert derive_overall_status(report).value == "PASS"


def test_all_axes_reject_derives_reject():
    report = _report(
        audience="REJECT", science="REJECT", narrative="REJECT", visual="REJECT", production="REJECT"
    )
    assert derive_overall_status(report).value == "REJECT"
