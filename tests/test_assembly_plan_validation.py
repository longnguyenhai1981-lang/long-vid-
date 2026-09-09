from __future__ import annotations

from uuid import uuid4

from app.engines.assembly_plan.validation import normalize_assembly_plan, validate_assembly_plan
from app.models.assembly import AssemblyPlan, AssemblySegment
from app.models.script import ScriptBeat, ScriptLine, ScriptPlan
from app.models.visual import VisualBeat, VisualPlan
from app.models.voice import VoiceChunk, VoicePlan


def _script_line(line_id) -> ScriptLine:
    return ScriptLine(line_id=line_id, text=f"Line {line_id}.", function="INFORM")


def _script_plan(line_ids, estimated_duration_seconds=500) -> ScriptPlan:
    return ScriptPlan(
        estimated_duration_seconds=estimated_duration_seconds,
        beats=[
            ScriptBeat(
                beat_id="SB001", narrative_node="Q0", narrative_function="INFORM",
                lines=[_script_line(lid) for lid in line_ids],
            )
        ],
        qa_status="PASS",
    )


def _voice_chunk(chunk_id, line_ids, music_state="BED") -> VoiceChunk:
    return VoiceChunk(
        chunk_id=chunk_id, line_ids=list(line_ids), voice_state="NEUTRAL", pace="NORMAL",
        energy="MEDIUM", take_count=1, music_state=music_state,
    )


def _voice_plan(chunks) -> VoicePlan:
    return VoicePlan(script_plan_id=uuid4(), chunks=chunks)


def _visual_beat(beat_id, line_ids) -> VisualBeat:
    return VisualBeat(
        beat_id=beat_id, script_line_ids=list(line_ids), narrative_node="Q0",
        visual_level="L1_ESTABLISH", visual_function="STORY", media_type="ASSET_REUSE",
        complexity="C0", concept="A shot.", primary_focus="The subject",
    )


def _visual_plan(beats) -> VisualPlan:
    return VisualPlan(script_plan_id=uuid4(), voice_plan_id=uuid4(), beats=beats)


def _segment(segment_id, script_line_ids, visual_beat_id, start, end, voice_chunk_ids=None, music_state="BED") -> AssemblySegment:
    return AssemblySegment(
        segment_id=segment_id, script_line_ids=list(script_line_ids),
        voice_chunk_ids=voice_chunk_ids or [], visual_beat_id=visual_beat_id,
        start_seconds=start, end_seconds=end, music_state=music_state,
        transition_in="CUT", transition_out="CUT",
    )


def _assembly_plan(segments, total_duration) -> AssemblyPlan:
    return AssemblyPlan(
        script_plan_id=uuid4(), voice_plan_id=uuid4(), visual_plan_id=uuid4(),
        estimated_total_duration_seconds=total_duration, segments=segments,
    )


def _valid_fixture():
    """L1,L2,L3,L4 -> V1=[L1,L2], V2=[L3,L4] -> two contiguous 5s segments."""
    script = _script_plan(["L1", "L2", "L3", "L4"], estimated_duration_seconds=10)
    voice = _voice_plan([_voice_chunk("C1", ["L1", "L2"]), _voice_chunk("C2", ["L3", "L4"])])
    visual = _visual_plan([_visual_beat("V1", ["L1", "L2"]), _visual_beat("V2", ["L3", "L4"])])
    plan = _assembly_plan(
        [
            _segment("S1", ["L1", "L2"], "V1", 0.0, 5.0, voice_chunk_ids=["C1"]),
            _segment("S2", ["L3", "L4"], "V2", 5.0, 10.0, voice_chunk_ids=["C2"]),
        ],
        10.0,
    )
    return plan, script, voice, visual


# ---------------------------------------------------------------------------
# Valid baseline
# ---------------------------------------------------------------------------


def test_valid_plan_has_no_issues():
    plan, script, voice, visual = _valid_fixture()
    assert validate_assembly_plan(plan, script, voice, visual) == []


# ---------------------------------------------------------------------------
# Section 54: visual beat exactly once
# ---------------------------------------------------------------------------


def test_missing_visual_beat_flagged():
    plan, script, voice, visual = _valid_fixture()
    plan = plan.model_copy(update={"segments": [plan.segments[0]]})
    issues = validate_assembly_plan(plan, script, voice, visual)
    assert any("missing VisualBeat ids" in issue and "V2" in issue for issue in issues)


def test_duplicate_visual_beat_flagged():
    script = _script_plan(["L1", "L2"])
    voice = _voice_plan([_voice_chunk("C1", ["L1", "L2"])])
    visual = _visual_plan([_visual_beat("V1", ["L1", "L2"])])
    plan = _assembly_plan(
        [
            _segment("S1", ["L1", "L2"], "V1", 0.0, 5.0, voice_chunk_ids=["C1"]),
            _segment("S2", ["L1", "L2"], "V1", 5.0, 10.0, voice_chunk_ids=["C1"]),
        ],
        10.0,
    )
    issues = validate_assembly_plan(plan, script, voice, visual)
    assert any("Duplicate visual_beat_id: V1" in issue for issue in issues)


# ---------------------------------------------------------------------------
# Section 55: segment / visual line match
# ---------------------------------------------------------------------------


def test_segment_line_mismatch_with_visual_beat_flagged():
    plan, script, voice, visual = _valid_fixture()
    bad_segment = plan.segments[0].model_copy(update={"script_line_ids": ["L1"]})
    plan = plan.model_copy(update={"segments": [bad_segment, plan.segments[1]]})
    issues = validate_assembly_plan(plan, script, voice, visual)
    assert any("does not exactly match VisualBeat V1.script_line_ids" in issue for issue in issues)


# ---------------------------------------------------------------------------
# Section 56: global line coverage/order
# ---------------------------------------------------------------------------


def test_reordered_segments_flagged_for_both_beat_order_and_line_coverage():
    plan, script, voice, visual = _valid_fixture()
    reordered = plan.model_copy(update={"segments": [plan.segments[1], plan.segments[0]]})
    # Re-fix the timeline so this test isolates the ordering issue, not timing.
    reordered = reordered.model_copy(
        update={
            "segments": [
                plan.segments[1].model_copy(update={"start_seconds": 0.0, "end_seconds": 5.0}),
                plan.segments[0].model_copy(update={"start_seconds": 5.0, "end_seconds": 10.0}),
            ]
        }
    )
    issues = validate_assembly_plan(reordered, script, voice, visual)
    assert any("segment order does not exactly match VisualPlan beat order" in issue for issue in issues)
    assert any("does not exactly equal the ScriptPlan's own line order" in issue for issue in issues)


# ---------------------------------------------------------------------------
# Section 57: voice overlap normalization
# ---------------------------------------------------------------------------


def test_normalize_assembly_plan_derives_voice_chunk_ids():
    script = _script_plan(["L1", "L2", "L3"])
    voice = _voice_plan([_voice_chunk("C1", ["L1", "L2"]), _voice_chunk("C2", ["L3"])])
    visual = _visual_plan([_visual_beat("V1", ["L1", "L2", "L3"])])
    plan = _assembly_plan(
        [_segment("S1", ["L1", "L2", "L3"], "V1", 0.0, 5.0, voice_chunk_ids=["WRONG"])], 5.0
    )

    normalized = normalize_assembly_plan(plan, script, voice, visual)

    assert normalized.segments[0].voice_chunk_ids == ["C1", "C2"]
    assert normalized.script_plan_id == script.id
    assert normalized.voice_plan_id == voice.id
    assert normalized.visual_plan_id == visual.id


def test_unnormalized_wrong_voice_chunk_ids_flagged_by_validation():
    script = _script_plan(["L1", "L2", "L3"])
    voice = _voice_plan([_voice_chunk("C1", ["L1", "L2"]), _voice_chunk("C2", ["L3"])])
    visual = _visual_plan([_visual_beat("V1", ["L1", "L2", "L3"])])
    plan = _assembly_plan(
        [_segment("S1", ["L1", "L2", "L3"], "V1", 0.0, 5.0, voice_chunk_ids=["WRONG"])], 5.0
    )
    issues = validate_assembly_plan(plan, script, voice, visual)
    assert any("does not match the VoiceChunks that actually overlap" in issue for issue in issues)


# ---------------------------------------------------------------------------
# Section 58: unknown visual beat
# ---------------------------------------------------------------------------


def test_unknown_visual_beat_flagged():
    script = _script_plan(["L1"])
    voice = _voice_plan([_voice_chunk("C1", ["L1"])])
    visual = _visual_plan([_visual_beat("V1", ["L1"])])
    plan = _assembly_plan([_segment("S1", ["L1"], "V999", 0.0, 5.0, voice_chunk_ids=["C1"])], 5.0)
    issues = validate_assembly_plan(plan, script, voice, visual)
    assert any("unknown VisualBeat ids" in issue and "V999" in issue for issue in issues)


# ---------------------------------------------------------------------------
# Section 59: timeline start
# ---------------------------------------------------------------------------


def test_first_segment_must_start_at_zero():
    plan, script, voice, visual = _valid_fixture()
    shifted = plan.segments[0].model_copy(update={"start_seconds": 1.5, "end_seconds": 6.5})
    plan = plan.model_copy(update={"segments": [shifted, plan.segments[1]]})
    issues = validate_assembly_plan(plan, script, voice, visual)
    assert any("must start at 0" in issue for issue in issues)


# ---------------------------------------------------------------------------
# Section 60/61: timeline gap / overlap
# ---------------------------------------------------------------------------


def test_timeline_gap_flagged():
    script = _script_plan(["L1", "L2"])
    voice = _voice_plan([_voice_chunk("C1", ["L1"]), _voice_chunk("C2", ["L2"])])
    visual = _visual_plan([_visual_beat("V1", ["L1"]), _visual_beat("V2", ["L2"])])
    plan = _assembly_plan(
        [
            _segment("S1", ["L1"], "V1", 0.0, 10.0, voice_chunk_ids=["C1"]),
            _segment("S2", ["L2"], "V2", 11.0, 20.0, voice_chunk_ids=["C2"]),
        ],
        20.0,
    )
    issues = validate_assembly_plan(plan, script, voice, visual)
    assert any("timeline must be contiguous" in issue for issue in issues)


def test_timeline_overlap_flagged():
    script = _script_plan(["L1", "L2"])
    voice = _voice_plan([_voice_chunk("C1", ["L1"]), _voice_chunk("C2", ["L2"])])
    visual = _visual_plan([_visual_beat("V1", ["L1"]), _visual_beat("V2", ["L2"])])
    plan = _assembly_plan(
        [
            _segment("S1", ["L1"], "V1", 0.0, 10.0, voice_chunk_ids=["C1"]),
            _segment("S2", ["L2"], "V2", 9.0, 20.0, voice_chunk_ids=["C2"]),
        ],
        20.0,
    )
    issues = validate_assembly_plan(plan, script, voice, visual)
    assert any("timeline must be contiguous" in issue for issue in issues)


# ---------------------------------------------------------------------------
# Section 63: total duration match
# ---------------------------------------------------------------------------


def test_total_duration_matching_final_segment_end_is_valid():
    script = _script_plan(["L1"])
    voice = _voice_plan([_voice_chunk("C1", ["L1"])])
    visual = _visual_plan([_visual_beat("V1", ["L1"])])
    plan = _assembly_plan([_segment("S1", ["L1"], "V1", 0.0, 500.0, voice_chunk_ids=["C1"])], 500.0)
    assert validate_assembly_plan(plan, script, voice, visual) == []


def test_total_duration_mismatch_flagged():
    script = _script_plan(["L1"])
    voice = _voice_plan([_voice_chunk("C1", ["L1"])])
    visual = _visual_plan([_visual_beat("V1", ["L1"])])
    plan = _assembly_plan([_segment("S1", ["L1"], "V1", 0.0, 500.0, voice_chunk_ids=["C1"])], 520.0)
    issues = validate_assembly_plan(plan, script, voice, visual)
    assert any("does not match the final segment's end_seconds" in issue for issue in issues)


# ---------------------------------------------------------------------------
# Section 64: script duration tolerance (script=500, tolerance=max(30,50)=50)
# ---------------------------------------------------------------------------


def _duration_fixture(total_duration: float):
    script = _script_plan(["L1"])  # estimated_duration_seconds=500 (see _script_plan default)
    voice = _voice_plan([_voice_chunk("C1", ["L1"])])
    visual = _visual_plan([_visual_beat("V1", ["L1"])])
    plan = _assembly_plan(
        [_segment("S1", ["L1"], "V1", 0.0, total_duration, voice_chunk_ids=["C1"])], total_duration
    )
    return plan, script, voice, visual


def test_duration_at_lower_tolerance_boundary_is_valid():
    plan, script, voice, visual = _duration_fixture(450.0)
    assert validate_assembly_plan(plan, script, voice, visual) == []


def test_duration_at_upper_tolerance_boundary_is_valid():
    plan, script, voice, visual = _duration_fixture(550.0)
    assert validate_assembly_plan(plan, script, voice, visual) == []


def test_duration_just_below_lower_tolerance_boundary_is_invalid():
    plan, script, voice, visual = _duration_fixture(449.0)
    issues = validate_assembly_plan(plan, script, voice, visual)
    assert any("exceeding the allowed tolerance" in issue for issue in issues)


def test_duration_just_above_upper_tolerance_boundary_is_invalid():
    plan, script, voice, visual = _duration_fixture(551.0)
    issues = validate_assembly_plan(plan, script, voice, visual)
    assert any("exceeding the allowed tolerance" in issue for issue in issues)


# ---------------------------------------------------------------------------
# Section 65: float tolerance
# ---------------------------------------------------------------------------


def test_contiguity_within_float_tolerance_passes():
    script = _script_plan(["L1", "L2"])
    voice = _voice_plan([_voice_chunk("C1", ["L1"]), _voice_chunk("C2", ["L2"])])
    visual = _visual_plan([_visual_beat("V1", ["L1"]), _visual_beat("V2", ["L2"])])
    plan = _assembly_plan(
        [
            _segment("S1", ["L1"], "V1", 0.0, 10.0000, voice_chunk_ids=["C1"]),
            _segment("S2", ["L2"], "V2", 10.0005, 500.0, voice_chunk_ids=["C2"]),
        ],
        500.0,
    )
    issues = validate_assembly_plan(plan, script, voice, visual)
    assert not any("timeline must be contiguous" in issue for issue in issues)


def test_contiguity_beyond_float_tolerance_fails():
    script = _script_plan(["L1", "L2"])
    voice = _voice_plan([_voice_chunk("C1", ["L1"]), _voice_chunk("C2", ["L2"])])
    visual = _visual_plan([_visual_beat("V1", ["L1"]), _visual_beat("V2", ["L2"])])
    plan = _assembly_plan(
        [
            _segment("S1", ["L1"], "V1", 0.0, 10.00, voice_chunk_ids=["C1"]),
            _segment("S2", ["L2"], "V2", 10.01, 500.0, voice_chunk_ids=["C2"]),
        ],
        500.0,
    )
    issues = validate_assembly_plan(plan, script, voice, visual)
    assert any("timeline must be contiguous" in issue for issue in issues)
