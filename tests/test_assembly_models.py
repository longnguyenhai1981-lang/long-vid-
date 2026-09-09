from __future__ import annotations

from uuid import uuid4

import pytest
from pydantic import ValidationError

from app.models.common import TransitionIntent
from app.models.assembly import AssemblyPlan, AssemblySegment


def _segment(segment_id="S001", script_line_ids=("L001",), visual_beat_id="VB001", start=0.0, end=5.0) -> AssemblySegment:
    return AssemblySegment(
        segment_id=segment_id,
        script_line_ids=list(script_line_ids),
        visual_beat_id=visual_beat_id,
        start_seconds=start,
        end_seconds=end,
        music_state="BED",
        transition_in="CUT",
        transition_out="CUT",
    )


# ---------------------------------------------------------------------------
# Enum
# ---------------------------------------------------------------------------


def test_transition_intent_has_exactly_five_values():
    assert {member.value for member in TransitionIntent} == {"CUT", "DISSOLVE", "MATCH", "PUSH", "NONE"}


# ---------------------------------------------------------------------------
# AssemblySegment
# ---------------------------------------------------------------------------


def test_assembly_segment_constructs_with_required_fields():
    segment = _segment()
    assert segment.segment_id == "S001"
    assert segment.voice_chunk_ids == []
    assert segment.emphasis_note is None
    assert segment.assembly_note is None


def test_assembly_segment_rejects_blank_segment_id():
    with pytest.raises(ValidationError):
        _segment(segment_id="   ")


def test_assembly_segment_rejects_blank_visual_beat_id():
    with pytest.raises(ValidationError):
        _segment(visual_beat_id="   ")


def test_assembly_segment_rejects_empty_script_line_ids():
    with pytest.raises(ValidationError):
        AssemblySegment(
            segment_id="S001", script_line_ids=[], visual_beat_id="VB001",
            start_seconds=0.0, end_seconds=5.0, music_state="BED",
            transition_in="CUT", transition_out="CUT",
        )


def test_assembly_segment_rejects_negative_start():
    with pytest.raises(ValidationError):
        _segment(start=-1.0, end=5.0)


def test_assembly_segment_rejects_negative_end():
    with pytest.raises(ValidationError):
        _segment(start=0.0, end=-1.0)


@pytest.mark.parametrize("start,end", [(5.0, 5.0), (5.0, 4.0)])
def test_assembly_segment_rejects_end_not_greater_than_start(start, end):
    with pytest.raises(ValidationError):
        _segment(start=start, end=end)


def test_assembly_segment_accepts_end_greater_than_start():
    segment = _segment(start=0.0, end=5.0)
    assert segment.end_seconds > segment.start_seconds


def test_assembly_segment_rejects_unknown_fields():
    with pytest.raises(ValidationError):
        AssemblySegment(
            segment_id="S001", script_line_ids=["L001"], visual_beat_id="VB001",
            start_seconds=0.0, end_seconds=5.0, music_state="BED",
            transition_in="CUT", transition_out="CUT", unexpected_field="nope",
        )


# ---------------------------------------------------------------------------
# AssemblyPlan
# ---------------------------------------------------------------------------


def test_assembly_plan_constructs_with_segments():
    plan = AssemblyPlan(
        script_plan_id=uuid4(), voice_plan_id=uuid4(), visual_plan_id=uuid4(),
        estimated_total_duration_seconds=5.0, segments=[_segment()],
    )
    assert plan.id is not None
    assert len(plan.segments) == 1
    assert plan.required_assets == []


def test_assembly_plan_rejects_empty_segments():
    with pytest.raises(ValidationError):
        AssemblyPlan(
            script_plan_id=uuid4(), voice_plan_id=uuid4(), visual_plan_id=uuid4(),
            estimated_total_duration_seconds=5.0, segments=[],
        )


def test_assembly_plan_rejects_negative_total_duration():
    with pytest.raises(ValidationError):
        AssemblyPlan(
            script_plan_id=uuid4(), voice_plan_id=uuid4(), visual_plan_id=uuid4(),
            estimated_total_duration_seconds=-1.0, segments=[_segment()],
        )


def test_assembly_plan_rejects_blank_required_asset():
    with pytest.raises(ValidationError):
        AssemblyPlan(
            script_plan_id=uuid4(), voice_plan_id=uuid4(), visual_plan_id=uuid4(),
            estimated_total_duration_seconds=5.0, segments=[_segment()],
            required_assets=["bridge_side_view", "   "],
        )


def test_assembly_plan_accepts_valid_required_assets():
    plan = AssemblyPlan(
        script_plan_id=uuid4(), voice_plan_id=uuid4(), visual_plan_id=uuid4(),
        estimated_total_duration_seconds=5.0, segments=[_segment()],
        required_assets=["bridge_side_view", "evidence:S003"],
    )
    assert plan.required_assets == ["bridge_side_view", "evidence:S003"]


def test_assembly_plan_rejects_unknown_fields():
    with pytest.raises(ValidationError):
        AssemblyPlan(
            script_plan_id=uuid4(), voice_plan_id=uuid4(), visual_plan_id=uuid4(),
            estimated_total_duration_seconds=5.0, segments=[_segment()],
            extra_field="nope",
        )
