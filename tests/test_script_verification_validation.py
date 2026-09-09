from __future__ import annotations

from app.engines.script_verification.validation import (
    find_deterministic_claim_issues,
    find_unacknowledged_claim_issues,
    normalize_status,
)
from app.models.research import Claim, ResearchPackage
from app.models.script import ScriptBeat, ScriptLine, ScriptPlan, ScriptVerificationReport


def _line(line_id="L001", claim_ids=None, function="INFORM") -> ScriptLine:
    return ScriptLine(
        line_id=line_id,
        text="Some spoken line.",
        function=function,
        claim_ids=claim_ids or [],
    )


def _beat(beat_id="B001", narrative_node="Q0", lines=None) -> ScriptBeat:
    return ScriptBeat(
        beat_id=beat_id,
        narrative_node=narrative_node,
        narrative_function="INFORM",
        lines=lines if lines is not None else [_line()],
    )


def _plan(beats=None, duration=480) -> ScriptPlan:
    return ScriptPlan(
        estimated_duration_seconds=duration,
        beats=beats if beats is not None else [_beat()],
        qa_status="PASS",
    )


def _claim(claim_id="C001", status="SAFE") -> Claim:
    return Claim(claim_id=claim_id, claim="Flutter is self-excited", status=status, confidence="HIGH")


def _research_package(claims=None) -> ResearchPackage:
    return ResearchPackage(
        central_question="Why?",
        executive_summary="s",
        physics_core="Flutter",
        simplification_boundary="boundary",
        claims=claims or [],
    )


def _report(
    status="PASS",
    unsupported_lines=None,
    overstated_lines=None,
    dangerous_simplifications=None,
) -> ScriptVerificationReport:
    return ScriptVerificationReport(
        status=status,
        unsupported_lines=unsupported_lines or [],
        overstated_lines=overstated_lines or [],
        dangerous_simplifications=dangerous_simplifications or [],
    )


# ---------------------------------------------------------------------------
# normalize_status
# ---------------------------------------------------------------------------


def test_normalize_status_pass_stays_pass_when_no_issues():
    report = _report(status="PASS")
    assert normalize_status(report).status == "PASS"


def test_normalize_status_reframe_downgraded_to_pass_when_no_issues():
    report = _report(status="REFRAME")
    assert normalize_status(report).status == "PASS"


def test_normalize_status_reject_downgraded_to_pass_when_no_issues():
    report = _report(status="REJECT")
    assert normalize_status(report).status == "PASS"


def test_normalize_status_pass_upgraded_to_reframe_when_unsupported_lines():
    report = _report(status="PASS", unsupported_lines=["L001: no support"])
    assert normalize_status(report).status == "REFRAME"


def test_normalize_status_pass_upgraded_to_reframe_when_overstated_lines():
    report = _report(status="PASS", overstated_lines=["L001: overstated"])
    assert normalize_status(report).status == "REFRAME"


def test_normalize_status_pass_upgraded_to_reframe_when_dangerous_simplifications():
    report = _report(status="PASS", dangerous_simplifications=["L001: false analogy"])
    assert normalize_status(report).status == "REFRAME"


def test_normalize_status_reframe_kept_as_is_when_issues_present():
    report = _report(status="REFRAME", unsupported_lines=["L001: no support"])
    assert normalize_status(report).status == "REFRAME"


def test_normalize_status_reject_kept_as_is_when_issues_present():
    report = _report(status="REJECT", unsupported_lines=["L001: no support"])
    assert normalize_status(report).status == "REJECT"


def test_normalize_status_returns_same_object_when_already_consistent():
    report = _report(status="PASS")
    assert normalize_status(report) is report


def test_normalize_status_returns_new_object_when_changed():
    report = _report(status="REFRAME")
    normalized = normalize_status(report)
    assert normalized is not report
    assert report.status == "REFRAME"


# ---------------------------------------------------------------------------
# find_deterministic_claim_issues
# ---------------------------------------------------------------------------


def test_no_claim_issues_when_all_claim_ids_valid_and_safe():
    plan = _plan(beats=[_beat("B001", "Q0", lines=[_line("L001", claim_ids=["C001"])])])
    issues = find_deterministic_claim_issues(plan, _research_package(claims=[_claim("C001")]))
    assert issues == set()


def test_no_claim_issues_when_line_has_no_claim_ids():
    plan = _plan(beats=[_beat("B001", "Q0", lines=[_line("L001", claim_ids=[])])])
    issues = find_deterministic_claim_issues(plan, _research_package(claims=[_claim("C001")]))
    assert issues == set()


def test_unknown_claim_id_flagged():
    plan = _plan(beats=[_beat("B001", "Q0", lines=[_line("L001", claim_ids=["C999"])])])
    issues = find_deterministic_claim_issues(plan, _research_package(claims=[_claim("C001")]))
    assert issues == {"L001"}


def test_prohibited_claim_id_flagged():
    plan = _plan(beats=[_beat("B001", "Q0", lines=[_line("L001", claim_ids=["C001"])])])
    research_package = _research_package(claims=[_claim("C001", status="PROHIBITED")])
    issues = find_deterministic_claim_issues(plan, research_package)
    assert issues == {"L001"}


def test_one_bad_claim_id_among_several_still_flags_line():
    plan = _plan(beats=[_beat("B001", "Q0", lines=[_line("L001", claim_ids=["C001", "C999"])])])
    issues = find_deterministic_claim_issues(plan, _research_package(claims=[_claim("C001")]))
    assert issues == {"L001"}


def test_claim_issues_collected_across_beats_and_lines():
    plan = _plan(
        beats=[
            _beat("B001", "Q0", lines=[_line("L001", claim_ids=["C999"])]),
            _beat("B002", "Q1", lines=[_line("L002", claim_ids=["C001"]), _line("L003", claim_ids=["C888"])]),
        ]
    )
    issues = find_deterministic_claim_issues(plan, _research_package(claims=[_claim("C001")]))
    assert issues == {"L001", "L003"}


# ---------------------------------------------------------------------------
# find_unacknowledged_claim_issues
# ---------------------------------------------------------------------------


def test_no_unacknowledged_issues_when_no_deterministic_bad_lines():
    report = _report(status="PASS")
    assert find_unacknowledged_claim_issues(report, set()) == []


def test_acknowledged_line_not_flagged():
    report = _report(status="REFRAME", unsupported_lines=["L001: references an invalid claim"])
    assert find_unacknowledged_claim_issues(report, {"L001"}) == []


def test_unacknowledged_line_flagged():
    report = _report(status="PASS", unsupported_lines=[])
    assert find_unacknowledged_claim_issues(report, {"L001"}) == ["L001"]


def test_partially_acknowledged_lines_only_flags_missing_ones():
    report = _report(status="REFRAME", unsupported_lines=["L001: bad claim reference"])
    assert find_unacknowledged_claim_issues(report, {"L001", "L002"}) == ["L002"]


def test_unacknowledged_results_are_sorted():
    report = _report(status="PASS", unsupported_lines=[])
    assert find_unacknowledged_claim_issues(report, {"L003", "L001", "L002"}) == ["L001", "L002", "L003"]
