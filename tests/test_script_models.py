from __future__ import annotations

import pytest
from pydantic import ValidationError

from app.models.common import PauseIntent
from app.models.script import ScriptBeat, ScriptLine, ScriptPlan


def _line():
    return ScriptLine(
        line_id="L001",
        text="Chào! Một Tí Lý đây.",
        function="INFORM",
        claim_ids=["C001"],
    )


def test_valid_script_plan():
    plan = ScriptPlan(
        estimated_duration_seconds=480,
        beats=[
            ScriptBeat(
                beat_id="B001",
                narrative_node="Q1",
                narrative_function="INFORM",
                lines=[_line()],
            )
        ],
        claim_coverage=["C001"],
        qa_status="PASS",
    )
    assert plan.beats[0].lines[0].line_id == "L001"


def test_invalid_line_function_rejects():
    with pytest.raises(ValidationError):
        ScriptLine(line_id="L001", text="text", function="NOT_A_FUNCTION")


def test_pause_after_defaults_to_none_intent():
    line = _line()
    assert line.pause_after is PauseIntent.NONE


def test_pause_intent_accepts_approved_values():
    for value in ("NONE", "SHORT", "MEDIUM", "LONG"):
        line = ScriptLine(line_id="L001", text="text", function="INFORM", pause_after=value)
        assert line.pause_after.value == value


def test_numeric_pause_after_rejects():
    with pytest.raises(ValidationError):
        ScriptLine(line_id="L001", text="text", function="INFORM", pause_after=1.5)


def test_non_positive_duration_rejects():
    with pytest.raises(ValidationError):
        ScriptPlan(estimated_duration_seconds=0, qa_status="PASS")


def test_script_plan_nested_round_trip_serialization():
    plan = ScriptPlan(
        estimated_duration_seconds=480,
        beats=[
            ScriptBeat(
                beat_id="B001",
                narrative_node="Q1",
                narrative_function="REVEAL",
                lines=[_line()],
                micro_hook="Nhưng chuyện chưa dừng ở đó...",
            )
        ],
        qa_status="PASS",
    )
    dumped = plan.model_dump_json()
    restored = ScriptPlan.model_validate_json(dumped)
    assert restored == plan
