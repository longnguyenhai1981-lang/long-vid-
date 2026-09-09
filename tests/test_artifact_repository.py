from __future__ import annotations

from datetime import datetime, timezone
from uuid import UUID, uuid4

import pytest
from sqlalchemy.orm import Session

from app.models.common import GateEvaluation, OpeningType, PrimaryPayoff
from app.models.idea import ABT, IdeaCandidate
from app.models.narrative import SCQA, NarrativePlan
from app.models.research import Claim, ResearchPackage, Source
from app.models.script import ScriptBeat, ScriptLine, ScriptPlan
from app.storage.artifacts import get_artifact, save_artifact
from app.storage.errors import ArtifactNotFoundError, ArtifactValidationError
from app.storage.orm import ArtifactRow


def _idea() -> IdeaCandidate:
    return IdeaCandidate(
        topic="Vi khuẩn kháng kháng sinh",
        central_question="Vì sao vi khuẩn ngày càng lờn thuốc?",
        abt=ABT(
            and_context="Kháng sinh từng là phép màu của y học",
            but_complication="Ngày càng nhiều vi khuẩn kháng thuốc",
            therefore_investigation="Điều tra cơ chế kháng thuốc",
        ),
        primary_payoff=PrimaryPayoff.DISCOVERY,
        physics_core="Chọn lọc tự nhiên ở cấp độ vi sinh",
        audience_prerequisite="none",
        brand_fit=GateEvaluation(status="PASS", reason="Đúng tinh thần Một Tí Lý"),
        general_audience_gate=GateEvaluation(status="PASS", reason="Không cần kiến thức nền"),
        longform_potential=GateEvaluation(status="PASS", reason="Đủ chất liệu 8-10 phút"),
    )


def _research_package() -> ResearchPackage:
    return ResearchPackage(
        central_question="Vì sao vi khuẩn kháng thuốc?",
        executive_summary="Tóm tắt nghiên cứu",
        physics_core="Chọn lọc tự nhiên",
        simplification_boundary="Không đơn giản hoá cơ chế di truyền",
        claims=[
            Claim(claim_id="C001", claim="Kháng sinh giết vi khuẩn", status="SAFE", confidence="HIGH")
        ],
        sources=[
            Source(
                source_id="S001", title="Nature paper", type="paper", quality_tier=1, authoritative=True
            )
        ],
    )


def _narrative_plan() -> NarrativePlan:
    return NarrativePlan(
        central_question="Vì sao vi khuẩn kháng thuốc?",
        scqa=SCQA(
            situation="Vi khuẩn từng dễ bị tiêu diệt",
            complication="Nay ngày càng kháng thuốc",
            question="Vì sao?",
            answer="Chọn lọc tự nhiên",
        ),
        opening=OpeningType.MYSTERY_FIRST,
        ti_role="Người điều tra dẫn chuyện",
        ending="Kêu gọi dùng kháng sinh đúng cách",
    )


def _script_plan() -> ScriptPlan:
    return ScriptPlan(
        estimated_duration_seconds=480,
        beats=[
            ScriptBeat(
                beat_id="B001",
                narrative_node="Q1",
                narrative_function="INFORM",
                lines=[ScriptLine(line_id="L001", text="Chào! Một Tí Lý đây.", function="INFORM")],
            )
        ],
        qa_status="PASS",
    )


ARTIFACT_CASES = [
    ("IdeaCandidate", _idea, IdeaCandidate),
    ("ResearchPackage", _research_package, ResearchPackage),
    ("NarrativePlan", _narrative_plan, NarrativePlan),
    ("ScriptPlan", _script_plan, ScriptPlan),
]


@pytest.mark.parametrize("artifact_type,factory,model_class", ARTIFACT_CASES)
def test_artifact_round_trip(engine, artifact_type, factory, model_class):
    project_id = uuid4()
    original = factory()
    save_artifact(engine, project_id, artifact_type, original)
    restored = get_artifact(engine, project_id, artifact_type, model_class)
    assert restored == original


def test_nested_enums_and_uuid_restored(engine):
    project_id = uuid4()
    original = _idea()
    save_artifact(engine, project_id, "IdeaCandidate", original)
    restored = get_artifact(engine, project_id, "IdeaCandidate", IdeaCandidate)

    assert isinstance(restored.id, UUID)
    assert restored.id == original.id
    assert restored.primary_payoff is PrimaryPayoff.DISCOVERY
    assert restored.general_audience_gate.status.value == "PASS"
    assert isinstance(restored.general_audience_gate, GateEvaluation)


def test_save_artifact_upserts_current_version(engine):
    project_id = uuid4()
    save_artifact(engine, project_id, "IdeaCandidate", _idea())
    updated = _idea()
    save_artifact(engine, project_id, "IdeaCandidate", updated)
    restored = get_artifact(engine, project_id, "IdeaCandidate", IdeaCandidate)
    assert restored == updated


def test_corrupt_payload_fails_cleanly(engine):
    project_id = uuid4()
    with Session(engine) as session, session.begin():
        session.add(
            ArtifactRow(
                project_id=str(project_id),
                artifact_type="IdeaCandidate",
                artifact_id=None,
                schema_version="0.1",
                payload_json="{not valid json",
                created_at=datetime.now(timezone.utc),
            )
        )
    with pytest.raises(ArtifactValidationError):
        get_artifact(engine, project_id, "IdeaCandidate", IdeaCandidate)


def test_incorrect_expected_model_class_fails_validation(engine):
    project_id = uuid4()
    save_artifact(engine, project_id, "IdeaCandidate", _idea())
    with pytest.raises(ArtifactValidationError):
        get_artifact(engine, project_id, "IdeaCandidate", NarrativePlan)


def test_missing_artifact_raises_not_found(engine):
    with pytest.raises(ArtifactNotFoundError):
        get_artifact(engine, uuid4(), "IdeaCandidate", IdeaCandidate)


def test_artifact_project_isolation(engine):
    project_a, project_b = uuid4(), uuid4()
    save_artifact(engine, project_a, "IdeaCandidate", _idea())
    with pytest.raises(ArtifactNotFoundError):
        get_artifact(engine, project_b, "IdeaCandidate", IdeaCandidate)
