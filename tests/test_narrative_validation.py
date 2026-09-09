from __future__ import annotations

from app.engines.narrative.validation import validate_narrative_plan
from app.models.narrative import SCQA, NarrativePlan, QuestionLadderNode
from app.models.research import Claim, ResearchPackage


def _node(id="Q0", claim_ids=None, creates_next_question=None) -> QuestionLadderNode:
    return QuestionLadderNode(
        id=id,
        question=f"Question for {id}?",
        why_viewer_cares="It matters",
        partial_answer="A partial answer",
        claim_ids=claim_ids or [],
        creates_next_question=creates_next_question,
        information_gap="What's missing",
    )


def _plan(question_ladder=None, claim_ids_used=None) -> NarrativePlan:
    return NarrativePlan(
        central_question="Why did the bridge collapse?",
        scqa=SCQA(situation="s", complication="c", question="q", answer="a"),
        opening="MYSTERY_FIRST",
        question_ladder=question_ladder or [],
        ti_role="Investigator",
        ending="Callback",
        claim_ids_used=claim_ids_used or [],
    )


def _claim(claim_id="C001", status="SAFE") -> Claim:
    return Claim(claim_id=claim_id, claim="Flutter is self-excited", status=status, confidence="HIGH")


def _research_package(claims=None) -> ResearchPackage:
    return ResearchPackage(
        central_question="Why did the bridge collapse?",
        executive_summary="s",
        physics_core="Flutter",
        simplification_boundary="boundary",
        claims=claims or [],
    )


# ---------------------------------------------------------------------------
# Section 38: valid ladder
# ---------------------------------------------------------------------------


def test_valid_linear_ladder_passes():
    ladder = [
        _node("Q0", creates_next_question="Q1"),
        _node("Q1", creates_next_question="Q2"),
        _node("Q2", creates_next_question=None),
    ]
    plan = _plan(question_ladder=ladder)
    assert validate_narrative_plan(plan, _research_package()) == []


def test_empty_ladder_is_not_flagged_by_structure_check():
    plan = _plan(question_ladder=[])
    assert validate_narrative_plan(plan, _research_package()) == []


# ---------------------------------------------------------------------------
# Section 39: unknown next node
# ---------------------------------------------------------------------------


def test_unknown_next_node_reference_is_flagged():
    ladder = [_node("Q0", creates_next_question="Q999")]
    plan = _plan(question_ladder=ladder)
    issues = validate_narrative_plan(plan, _research_package())
    assert any("unknown node id" in issue for issue in issues)


# ---------------------------------------------------------------------------
# Section 40: self loop
# ---------------------------------------------------------------------------


def test_self_loop_is_flagged():
    ladder = [_node("Q1", creates_next_question="Q1")]
    plan = _plan(question_ladder=ladder)
    issues = validate_narrative_plan(plan, _research_package())
    assert any("references itself" in issue for issue in issues)


# ---------------------------------------------------------------------------
# Section 41: cycle
# ---------------------------------------------------------------------------


def test_two_node_cycle_is_flagged():
    ladder = [
        _node("Q0", creates_next_question="Q1"),
        _node("Q1", creates_next_question="Q0"),
    ]
    plan = _plan(question_ladder=ladder)
    issues = validate_narrative_plan(plan, _research_package())
    assert issues != []


# ---------------------------------------------------------------------------
# Section 42: disconnected ladder
# ---------------------------------------------------------------------------


def test_disconnected_ladder_is_flagged():
    ladder = [
        _node("Q0", creates_next_question="Q1"),
        _node("Q1", creates_next_question=None),
        _node("Q2", creates_next_question=None),
    ]
    plan = _plan(question_ladder=ladder)
    issues = validate_narrative_plan(plan, _research_package())
    assert any("multiple final nodes" in issue for issue in issues)


# ---------------------------------------------------------------------------
# Section 43: non-final node missing next
# ---------------------------------------------------------------------------


def test_non_final_missing_next_is_flagged():
    ladder = [
        _node("Q0", creates_next_question=None),
        _node("Q1", creates_next_question="Q2"),
        _node("Q2", creates_next_question=None),
    ]
    plan = _plan(question_ladder=ladder)
    issues = validate_narrative_plan(plan, _research_package())
    assert any("multiple final nodes" in issue for issue in issues)


# ---------------------------------------------------------------------------
# Section 44: duplicate question ids
# ---------------------------------------------------------------------------


def test_duplicate_question_ids_are_flagged():
    ladder = [
        _node("Q0", creates_next_question="Q1"),
        _node("Q0", creates_next_question=None),
    ]
    plan = _plan(question_ladder=ladder)
    issues = validate_narrative_plan(plan, _research_package())
    assert any("Duplicate QuestionLadderNode.id" in issue for issue in issues)


# ---------------------------------------------------------------------------
# Section 45: unknown claim id
# ---------------------------------------------------------------------------


def test_unknown_claim_id_in_ladder_node_is_flagged():
    ladder = [_node("Q0", claim_ids=["C999"], creates_next_question=None)]
    plan = _plan(question_ladder=ladder)
    issues = validate_narrative_plan(plan, _research_package(claims=[_claim("C001")]))
    assert any("unknown claim_id" in issue for issue in issues)


def test_unknown_claim_id_in_claim_ids_used_is_flagged():
    ladder = [_node("Q0", creates_next_question=None)]
    plan = _plan(question_ladder=ladder, claim_ids_used=["C999"])
    issues = validate_narrative_plan(plan, _research_package(claims=[_claim("C001")]))
    assert any(
        "claim_ids_used references unknown claim_id" in issue for issue in issues
    )


# ---------------------------------------------------------------------------
# Section 46: prohibited claim
# ---------------------------------------------------------------------------


def test_prohibited_claim_reference_in_ladder_node_is_flagged():
    ladder = [_node("Q0", claim_ids=["C004"], creates_next_question=None)]
    plan = _plan(question_ladder=ladder)
    research_package = _research_package(claims=[_claim("C004", status="PROHIBITED")])
    issues = validate_narrative_plan(plan, research_package)
    assert any("PROHIBITED" in issue for issue in issues)


def test_prohibited_claim_reference_in_claim_ids_used_is_flagged():
    plan = _plan(claim_ids_used=["C004"])
    research_package = _research_package(claims=[_claim("C004", status="PROHIBITED")])
    issues = validate_narrative_plan(plan, research_package)
    assert any("PROHIBITED" in issue for issue in issues)


def test_valid_claim_reference_with_safe_status_passes():
    ladder = [_node("Q0", claim_ids=["C001"], creates_next_question=None)]
    plan = _plan(question_ladder=ladder, claim_ids_used=["C001"])
    research_package = _research_package(claims=[_claim("C001", status="SAFE")])
    assert validate_narrative_plan(plan, research_package) == []


def test_qualified_and_disputed_claim_references_are_allowed():
    ladder = [_node("Q0", claim_ids=["C001", "C002"], creates_next_question=None)]
    plan = _plan(question_ladder=ladder)
    research_package = _research_package(
        claims=[_claim("C001", status="QUALIFIED"), _claim("C002", status="DISPUTED")]
    )
    assert validate_narrative_plan(plan, research_package) == []
