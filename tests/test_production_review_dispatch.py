"""Phase 35 requirement #40: legacy gate dispatch tests -- for each of
the five upstream gates, the CLI-layer dispatcher must call the exact
canonical app/review/service.py mutation, never a generic writer."""

from __future__ import annotations

import pytest

from app.models.common import ApprovalStatus, GateStatus, ProjectState
from app.orchestration.models import ApprovalGateType
from app.services.production_review import (
    GateNotPendingError,
    GateRejectionNotSupportedError,
    approve_legacy_gate,
    is_legacy_gate,
    reject_legacy_gate,
)
from app.storage.approvals import get_latest_approval_for_stage
from app.storage.artifacts import get_artifact, save_artifact
from app.storage.projects import get_project
from tests.test_feasibility_engine import _create_project_in_feasibility
from tests.test_narrative_engine import _create_project_in_narrative
from tests.test_packaging_p0_engine import _create_project_in_packaging_p0
from tests.test_r0_research_engine import _create_project_in_idea_review
from tests.test_script_verification_engine import _create_project_in_script_verification


def test_is_legacy_gate_classifies_correctly():
    for gate in (
        ApprovalGateType.IDEA_APPROVAL, ApprovalGateType.RESEARCH_APPROVAL,
        ApprovalGateType.NARRATIVE_APPROVAL, ApprovalGateType.PACKAGING_P0_APPROVAL,
        ApprovalGateType.SCRIPT_APPROVAL,
    ):
        assert is_legacy_gate(gate) is True
    assert is_legacy_gate(ApprovalGateType.FINAL_MEDIA_APPROVAL) is False


def test_approve_idea_dispatches_to_approve_idea(engine):
    project_id, idea = _create_project_in_idea_review(engine)
    approve_legacy_gate(engine, project_id, ApprovalGateType.IDEA_APPROVAL)

    assert get_project(engine, project_id).state is ProjectState.R0_RESEARCH
    rows = get_latest_approval_for_stage(engine, project_id, ProjectState.IDEA_REVIEW)
    assert rows.status is ApprovalStatus.APPROVED


def test_approve_idea_fails_when_not_pending(engine):
    project_id, idea = _create_project_in_idea_review(engine)
    approve_legacy_gate(engine, project_id, ApprovalGateType.IDEA_APPROVAL)
    with pytest.raises(GateNotPendingError):
        approve_legacy_gate(engine, project_id, ApprovalGateType.IDEA_APPROVAL)


def test_idea_gate_rejection_not_supported(engine):
    project_id, idea = _create_project_in_idea_review(engine)
    with pytest.raises(GateRejectionNotSupportedError):
        reject_legacy_gate(engine, project_id, ApprovalGateType.IDEA_APPROVAL)


def test_approve_research_dispatches_to_decide_feasibility_pass(engine):
    from app.production_adapters.upstream import FeasibilityAdapter
    from app.orchestration.registry import ExecutionContext
    from app.llm.fake import FakeLLMProvider
    from app.llm.models import LLMResponse
    from tests.test_feasibility_engine import _feasibility_json
    from tests.test_idea_engine import _llm_settings

    project_id, idea, research = _create_project_in_feasibility(engine)
    ctx = ExecutionContext(
        db_engine=engine, project_id=project_id, llm_settings=_llm_settings(),
        llm_provider=FakeLLMProvider([LLMResponse(text=_feasibility_json(), provider="fake", model="fake-model")]),
    )
    FeasibilityAdapter().execute(ctx)

    approve_legacy_gate(engine, project_id, ApprovalGateType.RESEARCH_APPROVAL)

    assert get_project(engine, project_id).state is ProjectState.R1_RESEARCH
    row = get_latest_approval_for_stage(engine, project_id, ProjectState.FEASIBILITY)
    assert row.status is ApprovalStatus.APPROVED


def test_research_gate_rejection_not_supported_once_passed(engine):
    from app.production_adapters.upstream import FeasibilityAdapter
    from app.orchestration.registry import ExecutionContext
    from app.llm.fake import FakeLLMProvider
    from app.llm.models import LLMResponse
    from tests.test_feasibility_engine import _feasibility_json
    from tests.test_idea_engine import _llm_settings

    project_id, idea, research = _create_project_in_feasibility(engine)
    ctx = ExecutionContext(
        db_engine=engine, project_id=project_id, llm_settings=_llm_settings(),
        llm_provider=FakeLLMProvider([LLMResponse(text=_feasibility_json(), provider="fake", model="fake-model")]),
    )
    FeasibilityAdapter().execute(ctx)

    with pytest.raises(GateRejectionNotSupportedError):
        reject_legacy_gate(engine, project_id, ApprovalGateType.RESEARCH_APPROVAL)


def test_approve_narrative_dispatches_to_approve_narrative(engine):
    from app.production_adapters.upstream import NarrativeAdapter
    from app.orchestration.registry import ExecutionContext
    from app.llm.fake import FakeLLMProvider
    from app.llm.models import LLMResponse
    from tests.test_narrative_engine import _plan_json
    from tests.test_idea_engine import _llm_settings

    project_id, idea, research_package = _create_project_in_narrative(engine)
    ctx = ExecutionContext(
        db_engine=engine, project_id=project_id, llm_settings=_llm_settings(),
        llm_provider=FakeLLMProvider([LLMResponse(text=_plan_json(), provider="fake", model="fake-model")]),
    )
    NarrativeAdapter().execute(ctx)

    approve_legacy_gate(engine, project_id, ApprovalGateType.NARRATIVE_APPROVAL)
    assert get_project(engine, project_id).state is ProjectState.PACKAGING_P0


def test_narrative_gate_rejection_not_supported(engine):
    project_id, idea, research_package = _create_project_in_narrative(engine)
    with pytest.raises(GateRejectionNotSupportedError):
        reject_legacy_gate(engine, project_id, ApprovalGateType.NARRATIVE_APPROVAL)


def test_approve_packaging_p0_dispatches_to_approve_packaging_p0(engine):
    from app.production_adapters.upstream import PackagingP0Adapter
    from app.orchestration.registry import ExecutionContext
    from app.llm.fake import FakeLLMProvider
    from app.llm.models import LLMResponse
    from tests.test_packaging_p0_engine import _prototype_json
    from tests.test_idea_engine import _llm_settings

    project_id, idea, research_package, narrative_plan = _create_project_in_packaging_p0(engine)
    ctx = ExecutionContext(
        db_engine=engine, project_id=project_id, llm_settings=_llm_settings(),
        llm_provider=FakeLLMProvider([LLMResponse(text=_prototype_json(), provider="fake", model="fake-model")]),
    )
    PackagingP0Adapter().execute(ctx)

    approve_legacy_gate(engine, project_id, ApprovalGateType.PACKAGING_P0_APPROVAL)
    assert get_project(engine, project_id).state is ProjectState.SCRIPT


def test_packaging_p0_gate_rejection_not_supported(engine):
    project_id, idea, research_package, narrative_plan = _create_project_in_packaging_p0(engine)
    with pytest.raises(GateRejectionNotSupportedError):
        reject_legacy_gate(engine, project_id, ApprovalGateType.PACKAGING_P0_APPROVAL)


def test_approve_script_requires_two_calls_for_the_two_real_steps(engine):
    from app.production_adapters.upstream import ScriptVerifyAdapter
    from app.orchestration.registry import ExecutionContext
    from app.llm.fake import FakeLLMProvider
    from app.llm.models import LLMResponse
    from tests.test_script_verification_engine import _report_json
    from tests.test_idea_engine import _llm_settings

    project_id, research_package, narrative_plan, packaging, script_plan = (
        _create_project_in_script_verification(engine)
    )
    ctx = ExecutionContext(
        db_engine=engine, project_id=project_id, llm_settings=_llm_settings(),
        llm_provider=FakeLLMProvider([LLMResponse(text=_report_json(), provider="fake", model="fake-model")]),
    )
    ScriptVerifyAdapter().execute(ctx)

    # First call: exactly ONE real mutation (accept_script_verification) --
    # never silently chains into approve_final_script too (requirement #29:
    # one explicit, auditable HumanApproval row per CLI call).
    approve_legacy_gate(engine, project_id, ApprovalGateType.SCRIPT_APPROVAL)
    assert get_project(engine, project_id).state is ProjectState.SCRIPT_REVIEW
    verification_row = get_latest_approval_for_stage(engine, project_id, ProjectState.SCRIPT_VERIFICATION)
    assert verification_row.status is ApprovalStatus.APPROVED
    assert get_latest_approval_for_stage(engine, project_id, ProjectState.SCRIPT_REVIEW) is None

    # Second call: the remaining step.
    approve_legacy_gate(engine, project_id, ApprovalGateType.SCRIPT_APPROVAL)
    assert get_project(engine, project_id).state is ProjectState.MVP_COMPLETE
    review_row = get_latest_approval_for_stage(engine, project_id, ProjectState.SCRIPT_REVIEW)
    assert review_row.status is ApprovalStatus.APPROVED


def test_reject_script_gate_requires_script_review_state(engine):
    project_id, research_package, narrative_plan, packaging, script_plan = (
        _create_project_in_script_verification(engine)
    )
    with pytest.raises(GateRejectionNotSupportedError):
        reject_legacy_gate(engine, project_id, ApprovalGateType.SCRIPT_APPROVAL)


def test_reject_script_gate_at_script_review_dispatches_to_reject_final_script(engine):
    from app.production_adapters.upstream import ScriptVerifyAdapter
    from app.orchestration.registry import ExecutionContext
    from app.llm.fake import FakeLLMProvider
    from app.llm.models import LLMResponse
    from tests.test_script_verification_engine import _report_json
    from tests.test_idea_engine import _llm_settings
    from app.review.service import accept_script_verification

    project_id, research_package, narrative_plan, packaging, script_plan = (
        _create_project_in_script_verification(engine)
    )
    ctx = ExecutionContext(
        db_engine=engine, project_id=project_id, llm_settings=_llm_settings(),
        llm_provider=FakeLLMProvider([LLMResponse(text=_report_json(), provider="fake", model="fake-model")]),
    )
    ScriptVerifyAdapter().execute(ctx)
    accept_script_verification(engine, project_id)

    reject_legacy_gate(engine, project_id, ApprovalGateType.SCRIPT_APPROVAL, note="not good enough")

    project = get_project(engine, project_id)
    assert project.state is ProjectState.ARCHIVED
    row = get_latest_approval_for_stage(engine, project_id, ProjectState.SCRIPT_REVIEW)
    assert row.status is ApprovalStatus.REJECTED
    assert row.user_feedback == "not good enough"
