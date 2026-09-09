from __future__ import annotations

import pytest
from pydantic import ValidationError

from app.models.common import GateEvaluation
from app.models.idea import ABT, IdeaCandidate


def _gate(status="PASS", reason="ok"):
    return GateEvaluation(status=status, reason=reason)


def _base_kwargs(**overrides):
    kwargs = dict(
        topic="Vi khuẩn kháng kháng sinh",
        central_question="Vì sao vi khuẩn ngày càng lờn thuốc?",
        abt=ABT(
            and_context="Kháng sinh từng là phép màu của y học",
            but_complication="Ngày càng nhiều vi khuẩn kháng thuốc",
            therefore_investigation="Điều tra cơ chế kháng thuốc",
        ),
        primary_payoff="DISCOVERY",
        physics_core="Chọn lọc tự nhiên ở cấp độ vi sinh",
        audience_prerequisite="none",
        brand_fit=_gate(reason="Đúng tinh thần Một Tí Lý"),
        general_audience_gate=_gate(reason="Không cần kiến thức nền"),
        longform_potential=_gate(reason="Đủ chất liệu cho 8-10 phút"),
    )
    kwargs.update(overrides)
    return kwargs


def test_valid_idea_candidate():
    idea = IdeaCandidate(**_base_kwargs())
    assert idea.secondary_payoffs == []
    assert idea.primary_payoff.value == "DISCOVERY"
    assert idea.general_audience_gate.status.value == "PASS"


def test_gate_fields_are_structured_not_strings():
    idea = IdeaCandidate(**_base_kwargs())
    for gate in (idea.brand_fit, idea.general_audience_gate, idea.longform_potential):
        assert isinstance(gate, GateEvaluation)
        assert gate.status.value == "PASS"
        assert gate.reason


def test_blank_central_question_rejects():
    with pytest.raises(ValidationError):
        IdeaCandidate(**_base_kwargs(central_question="   "))


def test_missing_abt_complication_rejects():
    with pytest.raises(ValidationError):
        ABT(and_context="ctx", but_complication="   ", therefore_investigation="inv")


def test_unknown_field_rejects():
    with pytest.raises(ValidationError):
        IdeaCandidate(**_base_kwargs(not_a_real_field="oops"))


def test_gate_evaluation_rejects_invalid_status():
    with pytest.raises(ValidationError):
        GateEvaluation(status="MAYBE", reason="ok")


def test_gate_evaluation_rejects_blank_reason():
    with pytest.raises(ValidationError):
        GateEvaluation(status="PASS", reason="   ")


def test_bare_string_gate_field_rejects():
    with pytest.raises(ValidationError):
        IdeaCandidate(**_base_kwargs(general_audience_gate="PASS"))
