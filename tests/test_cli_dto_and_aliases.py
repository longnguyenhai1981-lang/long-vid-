"""Phase 35 requirements #34/#43: target/gate alias resolution and DTO
JSON stability."""

from __future__ import annotations

from datetime import datetime, timezone
from uuid import uuid4

import pytest

from app.cli.dto import build_run_dto
from app.cli.exit_codes import (
    EXIT_BLOCKED,
    EXIT_FAILED,
    EXIT_SUCCESS,
    EXIT_WAITING_APPROVAL,
    exit_code_for_status,
)
from app.orchestration.models import (
    ApprovalGateType,
    BlockedReason,
    NodeRunRecord,
    ProductionNodeStatus,
    ProductionRun,
    ProductionRunStatus,
)
from app.services.production_service import (
    GATE_ALIASES,
    TARGET_ALIASES,
    UnknownGateAliasError,
    UnknownTargetAliasError,
    gate_alias_for,
    resolve_gate_alias,
    resolve_target_alias,
)


# ---------------------------------------------------------------------------
# Target aliases
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "alias,expected_node",
    [
        ("idea", "IDEA"),
        ("research", "FEASIBILITY"),
        ("script", "SCRIPT_VERIFY"),
        ("plans", "ASSEMBLY_PLAN"),
        ("video", "VIDEO_RENDER"),
        ("qc", "MEDIA_QC"),
        ("final", "MEDIA_QC"),
    ],
)
def test_target_alias_resolves_to_exact_node(alias, expected_node):
    assert resolve_target_alias(alias) == expected_node


@pytest.mark.parametrize("bad_alias", ["", "typo", "IDEA", "media_qc", "voice_render", "not-a-real-alias"])
def test_unknown_target_alias_rejected(bad_alias):
    with pytest.raises(UnknownTargetAliasError):
        resolve_target_alias(bad_alias)


def test_every_target_alias_maps_to_a_real_graph_node():
    from app.production_adapters.registry import build_full_graph

    graph = build_full_graph()
    node_ids = set(graph.node_ids())
    for alias, node_id in TARGET_ALIASES.items():
        assert node_id in node_ids, f"alias {alias!r} maps to unregistered node {node_id!r}"


# ---------------------------------------------------------------------------
# Gate aliases
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "alias,expected_gate",
    [
        ("idea", ApprovalGateType.IDEA_APPROVAL),
        ("research", ApprovalGateType.RESEARCH_APPROVAL),
        ("narrative", ApprovalGateType.NARRATIVE_APPROVAL),
        ("packaging", ApprovalGateType.PACKAGING_P0_APPROVAL),
        ("script", ApprovalGateType.SCRIPT_APPROVAL),
        ("final", ApprovalGateType.FINAL_MEDIA_APPROVAL),
    ],
)
def test_gate_alias_resolves_to_exact_gate_type(alias, expected_gate):
    assert resolve_gate_alias(alias) is expected_gate
    assert gate_alias_for(expected_gate) == alias


@pytest.mark.parametrize("bad_alias", ["", "typo", "IDEA_APPROVAL", "media", "not-a-real-gate"])
def test_unknown_gate_alias_rejected(bad_alias):
    with pytest.raises(UnknownGateAliasError):
        resolve_gate_alias(bad_alias)


def test_gate_aliases_are_exactly_the_six_gate_types():
    assert set(GATE_ALIASES.values()) == set(ApprovalGateType)


# ---------------------------------------------------------------------------
# Exit codes
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "status,expected_code",
    [
        (ProductionRunStatus.SUCCEEDED, EXIT_SUCCESS),
        (ProductionRunStatus.WAITING_APPROVAL, EXIT_WAITING_APPROVAL),
        (ProductionRunStatus.BLOCKED, EXIT_BLOCKED),
        (ProductionRunStatus.FAILED, EXIT_FAILED),
    ],
)
def test_exit_code_for_status(status, expected_code):
    assert exit_code_for_status(status) == expected_code


# ---------------------------------------------------------------------------
# DTO stability
# ---------------------------------------------------------------------------


def _run(status, node_states) -> ProductionRun:
    now = datetime.now(timezone.utc)
    return ProductionRun(
        project_id=uuid4(), target_node="MEDIA_QC", status=status, node_states=node_states,
        created_at=now, updated_at=now, stop_reason="MEDIA_QC:WAITING_GATE", waiting_gate="FINAL_MEDIA_APPROVAL",
    )


def test_dto_has_exact_stable_keys():
    artifact_id = str(uuid4())
    run = _run(
        ProductionRunStatus.WAITING_APPROVAL,
        {
            "A": NodeRunRecord(node_id="A", status=ProductionNodeStatus.SUCCEEDED, reused_existing_artifact=True, artifact_id=str(uuid4())),
            "MEDIA_QC": NodeRunRecord(
                node_id="MEDIA_QC", status=ProductionNodeStatus.WAITING_APPROVAL, executed_this_run=True,
                artifact_id=artifact_id, reason=BlockedReason.WAITING_GATE,
            ),
        },
    )
    dto = build_run_dto(run).to_json_dict()

    assert set(dto.keys()) == {
        "production_run_id", "project_id", "target", "status", "stop_reason", "waiting_gate",
        "subject_artifact_id", "executed_nodes", "reused_nodes", "blocked_nodes", "failed_nodes",
    }
    assert dto["production_run_id"] == str(run.id)
    assert dto["project_id"] == str(run.project_id)
    assert dto["status"] == "WAITING_APPROVAL"
    assert dto["waiting_gate"] == "FINAL_MEDIA_APPROVAL"
    assert dto["subject_artifact_id"] == artifact_id
    assert dto["executed_nodes"] == ["MEDIA_QC"]
    assert dto["reused_nodes"] == ["A"]
    assert dto["blocked_nodes"] == []
    assert dto["failed_nodes"] == []


def test_dto_never_contains_secrets_or_provider_config():
    run = _run(ProductionRunStatus.SUCCEEDED, {})
    dto = build_run_dto(run).to_json_dict()
    serialized = str(dto).lower()
    for forbidden in ("api_key", "apikey", "secret", "password", "token"):
        assert forbidden not in serialized
