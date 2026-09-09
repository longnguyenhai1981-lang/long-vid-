from __future__ import annotations

from app.engines.script.validation import validate_script_plan
from app.models.narrative import SCQA, NarrativePlan, QuestionLadderNode
from app.models.research import Claim, ResearchPackage
from app.models.script import ScriptBeat, ScriptLine, ScriptPlan


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


def _ladder_node(id="Q0", creates_next_question=None) -> QuestionLadderNode:
    return QuestionLadderNode(
        id=id,
        question=f"Question {id}?",
        why_viewer_cares="matters",
        partial_answer="answer",
        creates_next_question=creates_next_question,
        information_gap="gap",
    )


def _narrative_plan(question_ladder=None) -> NarrativePlan:
    return NarrativePlan(
        central_question="Why?",
        scqa=SCQA(situation="s", complication="c", question="q", answer="a"),
        opening="MYSTERY_FIRST",
        question_ladder=question_ladder
        if question_ladder is not None
        else [
            _ladder_node("Q0", creates_next_question="Q1"),
            _ladder_node("Q1", creates_next_question="Q2"),
            _ladder_node("Q2", creates_next_question=None),
        ],
        ti_role="Investigator",
        ending="Callback",
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


def _valid_ladder():
    return [
        _ladder_node("Q0", creates_next_question="Q1"),
        _ladder_node("Q1", creates_next_question="Q2"),
        _ladder_node("Q2", creates_next_question=None),
    ]


# ---------------------------------------------------------------------------
# Valid baseline
# ---------------------------------------------------------------------------


def test_valid_plan_has_no_issues():
    plan = _plan(beats=[_beat("B001", "Q0"), _beat("B002", "Q1", lines=[_line("L002")])])
    narrative_plan = _narrative_plan(_valid_ladder())
    assert validate_script_plan(plan, _research_package(claims=[_claim()]), narrative_plan) == []


# ---------------------------------------------------------------------------
# Section 48: duplicate beat ids
# ---------------------------------------------------------------------------


def test_duplicate_beat_ids_flagged():
    plan = _plan(beats=[_beat("B001", "Q0"), _beat("B001", "Q1", lines=[_line("L002")])])
    issues = validate_script_plan(plan, _research_package(), _narrative_plan(_valid_ladder()))
    assert any("Duplicate beat_id" in issue for issue in issues)


# ---------------------------------------------------------------------------
# Section 49: duplicate line ids across beats
# ---------------------------------------------------------------------------


def test_duplicate_line_ids_across_beats_flagged():
    plan = _plan(
        beats=[
            _beat("B001", "Q0", lines=[_line("L001")]),
            _beat("B002", "Q1", lines=[_line("L001")]),
        ]
    )
    issues = validate_script_plan(plan, _research_package(), _narrative_plan(_valid_ladder()))
    assert any("Duplicate line_id" in issue for issue in issues)


# ---------------------------------------------------------------------------
# Section 50: unknown claim
# ---------------------------------------------------------------------------


def test_unknown_claim_id_flagged():
    plan = _plan(beats=[_beat("B001", "Q0", lines=[_line("L001", claim_ids=["C999"])])])
    issues = validate_script_plan(plan, _research_package(claims=[_claim("C001")]), _narrative_plan(_valid_ladder()))
    assert any("unknown claim_id" in issue for issue in issues)


# ---------------------------------------------------------------------------
# Section 51: prohibited claim
# ---------------------------------------------------------------------------


def test_prohibited_claim_flagged():
    plan = _plan(beats=[_beat("B001", "Q0", lines=[_line("L001", claim_ids=["C004"])])])
    research_package = _research_package(claims=[_claim("C004", status="PROHIBITED")])
    issues = validate_script_plan(plan, research_package, _narrative_plan(_valid_ladder()))
    assert any("PROHIBITED" in issue for issue in issues)


# ---------------------------------------------------------------------------
# Section 52: unknown narrative node
# ---------------------------------------------------------------------------


def test_unknown_narrative_node_flagged():
    plan = _plan(beats=[_beat("B001", "Q999")])
    issues = validate_script_plan(plan, _research_package(), _narrative_plan(_valid_ladder()))
    assert any("unknown narrative_node" in issue for issue in issues)


# ---------------------------------------------------------------------------
# Section 53: narrative order regression / repeats allowed
# ---------------------------------------------------------------------------


def test_narrative_order_regression_flagged():
    plan = _plan(
        beats=[
            _beat("B001", "Q0", lines=[_line("L001")]),
            _beat("B002", "Q2", lines=[_line("L002")]),
            _beat("B003", "Q1", lines=[_line("L003")]),
        ]
    )
    issues = validate_script_plan(plan, _research_package(), _narrative_plan(_valid_ladder()))
    assert any("out of approved narrative order" in issue for issue in issues)


def test_repeated_narrative_node_is_allowed():
    plan = _plan(
        beats=[
            _beat("B001", "Q0", lines=[_line("L001")]),
            _beat("B002", "Q0", lines=[_line("L002")]),
            _beat("B003", "Q1", lines=[_line("L003")]),
            _beat("B004", "Q1", lines=[_line("L004")]),
            _beat("B005", "Q2", lines=[_line("L005")]),
        ]
    )
    issues = validate_script_plan(plan, _research_package(), _narrative_plan(_valid_ladder()))
    assert not any("out of approved narrative order" in issue for issue in issues)


# ---------------------------------------------------------------------------
# Section 54: empty beats
# ---------------------------------------------------------------------------


def test_empty_beats_flagged():
    plan = _plan(beats=[])
    issues = validate_script_plan(plan, _research_package(), _narrative_plan(_valid_ladder()))
    assert any("ScriptPlan.beats must not be empty" in issue for issue in issues)


# ---------------------------------------------------------------------------
# Section 55: empty beat lines
# ---------------------------------------------------------------------------


def test_empty_beat_lines_flagged():
    plan = _plan(beats=[_beat("B001", "Q0", lines=[])])
    issues = validate_script_plan(plan, _research_package(), _narrative_plan(_valid_ladder()))
    assert any("has no lines" in issue for issue in issues)


# ---------------------------------------------------------------------------
# Section 56: duration band
# ---------------------------------------------------------------------------


def test_duration_within_band_accepted():
    for duration in (420, 480, 600, 660):
        plan = _plan(duration=duration)
        issues = validate_script_plan(plan, _research_package(), _narrative_plan(_valid_ladder()))
        assert not any("estimated_duration_seconds" in issue for issue in issues), duration


def test_duration_below_band_flagged():
    plan = _plan(duration=419)
    issues = validate_script_plan(plan, _research_package(), _narrative_plan(_valid_ladder()))
    assert any("estimated_duration_seconds" in issue for issue in issues)


def test_duration_above_band_flagged():
    plan = _plan(duration=661)
    issues = validate_script_plan(plan, _research_package(), _narrative_plan(_valid_ladder()))
    assert any("estimated_duration_seconds" in issue for issue in issues)


def test_multiple_issues_all_reported_together():
    plan = _plan(
        beats=[_beat("B001", "Q999", lines=[_line("L001", claim_ids=["C999"])])],
        duration=100,
    )
    issues = validate_script_plan(plan, _research_package(), _narrative_plan(_valid_ladder()))
    assert len(issues) >= 3
