from __future__ import annotations

from uuid import uuid4

from app.engines.visual_plan.validation import validate_visual_plan
from app.models.narrative import SCQA, NarrativePlan, QuestionLadderNode
from app.models.research import ResearchPackage, Source
from app.models.script import ScriptBeat, ScriptLine, ScriptPlan
from app.models.visual import VisualBeat, VisualPlan


def _script_line(line_id) -> ScriptLine:
    return ScriptLine(line_id=line_id, text=f"Line {line_id}.", function="INFORM")


def _script_plan(node_to_lines: dict[str, list[str]]) -> ScriptPlan:
    beats = [
        ScriptBeat(
            beat_id=f"SB_{node}",
            narrative_node=node,
            narrative_function="INFORM",
            lines=[_script_line(lid) for lid in line_ids],
        )
        for node, line_ids in node_to_lines.items()
    ]
    return ScriptPlan(estimated_duration_seconds=480, beats=beats, qa_status="PASS")


def _ladder_node(node_id) -> QuestionLadderNode:
    return QuestionLadderNode(
        id=node_id, question=f"Question {node_id}?", why_viewer_cares="matters",
        partial_answer="answer", information_gap="gap",
    )


def _narrative_plan(node_ids=("Q0",)) -> NarrativePlan:
    return NarrativePlan(
        central_question="Why?",
        scqa=SCQA(situation="s", complication="c", question="q", answer="a"),
        opening="MYSTERY_FIRST",
        question_ladder=[_ladder_node(nid) for nid in node_ids],
        ti_role="Investigator",
        ending="Callback",
    )


def _source(source_id="S001") -> Source:
    return Source(source_id=source_id, title=f"Source {source_id}", type="photo", quality_tier=1, authoritative=True)


def _research_package(sources=None) -> ResearchPackage:
    return ResearchPackage(
        central_question="Why?", executive_summary="s", physics_core="Flutter",
        simplification_boundary="boundary", sources=sources or [_source()],
    )


def _beat(beat_id, script_line_ids, narrative_node="Q0", media_type="ASSET_REUSE", complexity="C0", evidence_source_ids=None) -> VisualBeat:
    return VisualBeat(
        beat_id=beat_id,
        script_line_ids=list(script_line_ids),
        narrative_node=narrative_node,
        visual_level="L1_ESTABLISH",
        visual_function="STORY",
        media_type=media_type,
        complexity=complexity,
        concept="A shot.",
        primary_focus="The subject",
        evidence_source_ids=evidence_source_ids or [],
    )


def _plan(beats) -> VisualPlan:
    return VisualPlan(script_plan_id=uuid4(), voice_plan_id=uuid4(), beats=beats)


# ---------------------------------------------------------------------------
# Section 55: full coverage (valid baseline)
# ---------------------------------------------------------------------------


def test_full_coverage_has_no_issues():
    script = _script_plan({"Q0": ["L1", "L2", "L3", "L4"]})
    narrative = _narrative_plan(("Q0",))
    research = _research_package()
    plan = _plan([_beat("B001", ["L1", "L2"]), _beat("B002", ["L3"]), _beat("B003", ["L4"])])
    assert validate_visual_plan(plan, script, narrative, research) == []


# ---------------------------------------------------------------------------
# Section 56: missing line
# ---------------------------------------------------------------------------


def test_missing_line_flagged():
    script = _script_plan({"Q0": ["L1", "L2", "L3"]})
    plan = _plan([_beat("B001", ["L1", "L3"])])
    issues = validate_visual_plan(plan, script, _narrative_plan(), _research_package())
    assert any("missing ScriptLine ids" in issue and "L2" in issue for issue in issues)


# ---------------------------------------------------------------------------
# Section 57: extra line
# ---------------------------------------------------------------------------


def test_extra_unknown_line_flagged():
    script = _script_plan({"Q0": ["L1", "L2"]})
    plan = _plan([_beat("B001", ["L1", "L2", "L999"])])
    issues = validate_visual_plan(plan, script, _narrative_plan(), _research_package())
    assert any("unknown ScriptLine ids" in issue and "L999" in issue for issue in issues)


# ---------------------------------------------------------------------------
# Section 58: duplicate line
# ---------------------------------------------------------------------------


def test_duplicate_line_across_beats_flagged():
    script = _script_plan({"Q0": ["L1", "L2", "L3"]})
    plan = _plan([_beat("B001", ["L1", "L2"]), _beat("B002", ["L2", "L3"])])
    issues = validate_visual_plan(plan, script, _narrative_plan(), _research_package())
    assert any("Duplicate script_line_id: L2" in issue for issue in issues)


# ---------------------------------------------------------------------------
# Section 59: order violation
# ---------------------------------------------------------------------------


def test_order_violation_flagged():
    script = _script_plan({"Q0": ["L1", "L2", "L3"]})
    plan = _plan([_beat("B001", ["L2", "L1", "L3"])])
    issues = validate_visual_plan(plan, script, _narrative_plan(), _research_package())
    assert any("does not exactly preserve ScriptPlan line order" in issue for issue in issues)


# ---------------------------------------------------------------------------
# Section 60: cross-narrative beat
# ---------------------------------------------------------------------------


def test_cross_narrative_beat_flagged():
    script = _script_plan({"Q0": ["L1"], "Q1": ["L2"]})
    narrative = _narrative_plan(("Q0", "Q1"))
    plan = _plan([_beat("B001", ["L1", "L2"], narrative_node="Q0")])
    issues = validate_visual_plan(plan, script, narrative, _research_package())
    assert any("covers ScriptLines from multiple narrative nodes" in issue for issue in issues)


def test_declared_node_mismatch_with_lines_flagged():
    script = _script_plan({"Q0": ["L1"], "Q1": ["L2"]})
    narrative = _narrative_plan(("Q0", "Q1"))
    plan = _plan([_beat("B001", ["L1"], narrative_node="Q1"), _beat("B002", ["L2"], narrative_node="Q1")])
    issues = validate_visual_plan(plan, script, narrative, _research_package())
    assert any("declares narrative_node='Q1' but its covered lines belong to narrative_node='Q0'" in issue for issue in issues)


# ---------------------------------------------------------------------------
# Section 61: unknown narrative node
# ---------------------------------------------------------------------------


def test_unknown_narrative_node_flagged():
    script = _script_plan({"Q0": ["L1"]})
    plan = _plan([_beat("B001", ["L1"], narrative_node="Q999")])
    issues = validate_visual_plan(plan, script, _narrative_plan(("Q0",)), _research_package())
    assert any("unknown NarrativePlan question_ladder node id" in issue for issue in issues)


# ---------------------------------------------------------------------------
# Section 62: duplicate visual beat id
# ---------------------------------------------------------------------------


def test_duplicate_visual_beat_id_flagged():
    script = _script_plan({"Q0": ["L1", "L2"]})
    plan = _plan([_beat("B001", ["L1"]), _beat("B001", ["L2"])])
    issues = validate_visual_plan(plan, script, _narrative_plan(), _research_package())
    assert any("Duplicate beat_id: B001" in issue for issue in issues)


# ---------------------------------------------------------------------------
# Section 63: evidence media valid
# ---------------------------------------------------------------------------


def test_evidence_media_with_valid_source_passes():
    script = _script_plan({"Q0": ["L1"]})
    research = _research_package(sources=[_source("S001")])
    plan = _plan([_beat("B001", ["L1"], media_type="EVIDENCE_MEDIA", evidence_source_ids=["S001"])])
    assert validate_visual_plan(plan, script, _narrative_plan(), research) == []


# ---------------------------------------------------------------------------
# Section 64: evidence media without source
# ---------------------------------------------------------------------------


def test_evidence_media_without_source_flagged():
    script = _script_plan({"Q0": ["L1"]})
    plan = _plan([_beat("B001", ["L1"], media_type="EVIDENCE_MEDIA")])
    issues = validate_visual_plan(plan, script, _narrative_plan(), _research_package())
    assert any("EVIDENCE_MEDIA but no evidence_source_ids" in issue for issue in issues)


# ---------------------------------------------------------------------------
# Section 65: unknown evidence source
# ---------------------------------------------------------------------------


def test_unknown_evidence_source_flagged():
    script = _script_plan({"Q0": ["L1"]})
    research = _research_package(sources=[_source("S001")])
    plan = _plan([_beat("B001", ["L1"], media_type="EVIDENCE_MEDIA", evidence_source_ids=["S999"])])
    issues = validate_visual_plan(plan, script, _narrative_plan(), research)
    assert any("unknown evidence source_ids" in issue and "S999" in issue for issue in issues)


# ---------------------------------------------------------------------------
# Section 66: source on non-evidence media
# ---------------------------------------------------------------------------


def test_source_on_non_evidence_media_flagged():
    script = _script_plan({"Q0": ["L1"]})
    research = _research_package(sources=[_source("S001")])
    plan = _plan([_beat("B001", ["L1"], media_type="GENERATED_STILL", evidence_source_ids=["S001"])])
    issues = validate_visual_plan(plan, script, _narrative_plan(), research)
    assert any("declares evidence_source_ids" in issue for issue in issues)


# ---------------------------------------------------------------------------
# Section 67: C3 budget
# ---------------------------------------------------------------------------


def test_c3_budget_at_exactly_twenty_percent_is_valid():
    script = _script_plan({"Q0": ["L1", "L2", "L3", "L4", "L5"]})
    plan = _plan(
        [
            _beat("B001", ["L1"], complexity="C3"),
            _beat("B002", ["L2"]),
            _beat("B003", ["L3"]),
            _beat("B004", ["L4"]),
            _beat("B005", ["L5"]),
        ]
    )
    assert validate_visual_plan(plan, script, _narrative_plan(), _research_package()) == []


def test_c3_budget_over_twenty_percent_is_invalid():
    script = _script_plan({"Q0": ["L1", "L2", "L3", "L4", "L5"]})
    plan = _plan(
        [
            _beat("B001", ["L1"], complexity="C3"),
            _beat("B002", ["L2"], complexity="C3"),
            _beat("B003", ["L3"]),
            _beat("B004", ["L4"]),
            _beat("B005", ["L5"]),
        ]
    )
    issues = validate_visual_plan(plan, script, _narrative_plan(), _research_package())
    assert any("exceed the 20% budget" in issue for issue in issues)


def test_c3_budget_not_enforced_below_five_beats():
    script = _script_plan({"Q0": ["L1", "L2", "L3"]})
    plan = _plan(
        [
            _beat("B001", ["L1"], complexity="C3"),
            _beat("B002", ["L2"], complexity="C3"),
            _beat("B003", ["L3"], complexity="C3"),
        ]
    )
    issues = validate_visual_plan(plan, script, _narrative_plan(), _research_package())
    assert not any("budget" in issue for issue in issues)
