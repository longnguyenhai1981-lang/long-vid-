from __future__ import annotations

from app.engines.voice_plan.validation import validate_voice_plan
from app.models.script import ScriptBeat, ScriptLine, ScriptPlan
from app.models.voice import VoiceChunk, VoicePlan


def _line(line_id) -> ScriptLine:
    return ScriptLine(line_id=line_id, text=f"Line {line_id}.", function="INFORM")


def _script_plan(line_ids=("L001", "L002", "L003", "L004")) -> ScriptPlan:
    return ScriptPlan(
        estimated_duration_seconds=480,
        beats=[ScriptBeat(beat_id="B001", narrative_node="Q0", narrative_function="INFORM", lines=[_line(lid) for lid in line_ids])],
        qa_status="PASS",
    )


def _chunk(chunk_id, line_ids) -> VoiceChunk:
    return VoiceChunk(
        chunk_id=chunk_id,
        line_ids=list(line_ids),
        voice_state="NEUTRAL",
        pace="NORMAL",
        energy="MEDIUM",
        take_count=1,
        music_state="BED",
    )


def _plan(chunks) -> VoicePlan:
    from uuid import uuid4

    return VoicePlan(script_plan_id=uuid4(), chunks=chunks)


# ---------------------------------------------------------------------------
# Section 40: full coverage (valid baseline)
# ---------------------------------------------------------------------------


def test_full_coverage_has_no_issues():
    script = _script_plan(("L1", "L2", "L3", "L4"))
    plan = _plan([_chunk("C001", ["L1", "L2"]), _chunk("C002", ["L3", "L4"])])
    assert validate_voice_plan(plan, script) == []


def test_single_chunk_covering_everything_has_no_issues():
    script = _script_plan(("L1", "L2", "L3", "L4"))
    plan = _plan([_chunk("C001", ["L1", "L2", "L3", "L4"])])
    assert validate_voice_plan(plan, script) == []


# ---------------------------------------------------------------------------
# Section 41: missing line
# ---------------------------------------------------------------------------


def test_missing_line_flagged():
    script = _script_plan(("L1", "L2", "L3"))
    plan = _plan([_chunk("C001", ["L1", "L3"])])
    issues = validate_voice_plan(plan, script)
    assert any("missing ScriptLine ids" in issue and "L2" in issue for issue in issues)


# ---------------------------------------------------------------------------
# Section 42: extra line
# ---------------------------------------------------------------------------


def test_extra_unknown_line_flagged():
    script = _script_plan(("L1", "L2", "L3"))
    plan = _plan([_chunk("C001", ["L1", "L2", "L3", "L999"])])
    issues = validate_voice_plan(plan, script)
    assert any("unknown ScriptLine ids" in issue and "L999" in issue for issue in issues)


# ---------------------------------------------------------------------------
# Section 43: duplicate line
# ---------------------------------------------------------------------------


def test_duplicate_line_across_chunks_flagged():
    script = _script_plan(("L1", "L2", "L3"))
    plan = _plan([_chunk("C001", ["L1", "L2"]), _chunk("C002", ["L2", "L3"])])
    issues = validate_voice_plan(plan, script)
    assert any("Duplicate line_id: L2" in issue for issue in issues)


# ---------------------------------------------------------------------------
# Section 44: order violation
# ---------------------------------------------------------------------------


def test_order_violation_flagged():
    script = _script_plan(("L1", "L2", "L3"))
    plan = _plan([_chunk("C001", ["L2", "L1", "L3"])])
    issues = validate_voice_plan(plan, script)
    assert any("does not exactly preserve ScriptPlan line order" in issue for issue in issues)


# ---------------------------------------------------------------------------
# Section 45: noncontiguous chunk
# ---------------------------------------------------------------------------


def test_noncontiguous_chunk_flagged():
    script = _script_plan(("L1", "L2", "L3"))
    # Chunk 1 takes L1 and L3, leaving L2 to a later chunk -- L2 lies between
    # them in the script, so the flattened order can never match.
    plan = _plan([_chunk("C001", ["L1", "L3"]), _chunk("C002", ["L2"])])
    issues = validate_voice_plan(plan, script)
    assert any("does not exactly preserve ScriptPlan line order" in issue for issue in issues)


# ---------------------------------------------------------------------------
# Additional: duplicate chunk id
# ---------------------------------------------------------------------------


def test_duplicate_chunk_id_flagged():
    script = _script_plan(("L1", "L2"))
    plan = _plan([_chunk("C001", ["L1"]), _chunk("C001", ["L2"])])
    issues = validate_voice_plan(plan, script)
    assert any("Duplicate chunk_id: C001" in issue for issue in issues)


def test_multiple_issues_all_reported_together():
    script = _script_plan(("L1", "L2", "L3", "L4"))
    plan = _plan([_chunk("C001", ["L1", "L999"])])
    issues = validate_voice_plan(plan, script)
    assert len(issues) >= 2
