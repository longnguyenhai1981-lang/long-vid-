"""Phase 34 requirement #42: legacy review-service <-> ProductionRunner
gate compatibility.

Drives the REAL app/review/service.py functions to produce approved/
rejected/pending states, then confirms app/orchestration/gates.py's
gate_decision_for() reconstructs the correct ApprovalDecision-shaped
result purely by reading ProjectState + HumanApproval -- no live UI, no
separate decision table for these five gates. Artifacts are produced by
running the same real upstream adapters (app/production_adapters/
upstream.py) already covered by tests/test_production_adapters_upstream.py,
so the project reaches the exact state app/review/service.py's own
freshness checks require (e.g. accept_script_verification's
_verify_script_verification_is_fresh) rather than being hand-faked.
"""

from __future__ import annotations

from uuid import uuid4

import pytest

from app.llm.fake import FakeLLMProvider
from app.llm.models import LLMResponse
from app.models.common import ApprovalStatus, GateStatus, ProjectState
from app.orchestration.errors import LegacyGateNotWritableError
from app.orchestration.gates import approve_gate, gate_decision_for
from app.orchestration.models import ApprovalDecisionType, ApprovalGateType
from app.production_adapters.registry import build_full_graph
from app.production_adapters.upstream import (
    FeasibilityAdapter,
    IdeaAdapter,
    NarrativeAdapter,
    PackagingP0Adapter,
    ScriptVerifyAdapter,
)
from app.review.service import (
    accept_script_verification,
    approve_final_script,
    approve_idea,
    approve_narrative,
    approve_packaging_p0,
    decide_feasibility,
    reject_final_script,
    revise_idea,
)
from app.storage.approvals import list_approvals_for_project
from tests.test_feasibility_engine import _create_project_in_feasibility, _feasibility_json
from tests.test_idea_engine import VALID_IDEA_JSON, _llm_settings
from tests.test_narrative_engine import _create_project_in_narrative, _plan_json
from tests.test_packaging_p0_engine import _create_project_in_packaging_p0, _prototype_json
from tests.test_r0_research_engine import _create_project_in_idea_review
from tests.test_script_verification_engine import _create_project_in_script_verification, _report_json


def _response(text: str) -> LLMResponse:
    return LLMResponse(text=text, provider="fake", model="fake-model")


def _run_adapter(adapter, engine, project_id, json_text):
    from app.orchestration.registry import ExecutionContext

    ctx = ExecutionContext(
        db_engine=engine, project_id=project_id,
        llm_provider=FakeLLMProvider([_response(json_text)]), llm_settings=_llm_settings(),
    )
    adapter.execute(ctx)
    return adapter.load_current(ctx)


def test_idea_gate_pending_then_approved(engine):
    project_id, idea = _create_project_in_idea_review(engine)
    assert gate_decision_for(engine, project_id, ApprovalGateType.IDEA_APPROVAL, idea.id) is None

    approve_idea(engine, project_id)

    decision = gate_decision_for(engine, project_id, ApprovalGateType.IDEA_APPROVAL, idea.id)
    assert decision is not None
    assert decision.decision is ApprovalDecisionType.APPROVED
    assert decision.subject_artifact_id == idea.id


def test_idea_gate_revise_is_never_read_as_rejected(engine):
    project_id, idea = _create_project_in_idea_review(engine)
    revise_idea(engine, project_id, feedback="try a different angle")

    # revise_idea sends the project back to IDEA_DISCOVERY -- the gate is
    # PENDING again (needs a fresh idea + fresh approve_idea), never
    # REJECTED, since app/review/service.py has no reject_idea at all.
    decision = gate_decision_for(engine, project_id, ApprovalGateType.IDEA_APPROVAL, idea.id)
    assert decision is None


def test_feasibility_gate_approved(engine):
    project_id, idea, research = _create_project_in_feasibility(engine)
    report = _run_adapter(FeasibilityAdapter(), engine, project_id, _feasibility_json())
    assert gate_decision_for(engine, project_id, ApprovalGateType.RESEARCH_APPROVAL, report.id) is None

    decide_feasibility(engine, project_id, GateStatus.PASS)

    decision = gate_decision_for(engine, project_id, ApprovalGateType.RESEARCH_APPROVAL, report.id)
    assert decision.decision is ApprovalDecisionType.APPROVED


def test_feasibility_gate_rejected(engine):
    project_id, idea, research = _create_project_in_feasibility(engine)
    report = _run_adapter(
        FeasibilityAdapter(), engine, project_id,
        _feasibility_json(axis_statuses={"science": "REJECT"}, overall="REJECT"),
    )

    decide_feasibility(engine, project_id, GateStatus.REJECT, feedback=None)

    decision = gate_decision_for(engine, project_id, ApprovalGateType.RESEARCH_APPROVAL, report.id)
    assert decision.decision is ApprovalDecisionType.REJECTED


def test_narrative_gate_approved(engine):
    project_id, idea, research_package = _create_project_in_narrative(engine)
    plan = _run_adapter(NarrativeAdapter(), engine, project_id, _plan_json())
    assert gate_decision_for(engine, project_id, ApprovalGateType.NARRATIVE_APPROVAL, plan.id) is None

    approve_narrative(engine, project_id)

    decision = gate_decision_for(engine, project_id, ApprovalGateType.NARRATIVE_APPROVAL, plan.id)
    assert decision.decision is ApprovalDecisionType.APPROVED


def test_packaging_p0_gate_approved(engine):
    project_id, idea, research_package, narrative_plan = _create_project_in_packaging_p0(engine)
    packaging = _run_adapter(PackagingP0Adapter(), engine, project_id, _prototype_json())
    assert gate_decision_for(engine, project_id, ApprovalGateType.PACKAGING_P0_APPROVAL, packaging.id) is None

    approve_packaging_p0(engine, project_id)

    decision = gate_decision_for(engine, project_id, ApprovalGateType.PACKAGING_P0_APPROVAL, packaging.id)
    assert decision.decision is ApprovalDecisionType.APPROVED


def test_script_approval_gate_spans_verification_and_review(engine):
    """SCRIPT_APPROVAL is PENDING through both SCRIPT_VERIFICATION and
    SCRIPT_REVIEW, and only APPROVED once BOTH accept_script_verification
    and approve_final_script have happened."""
    project_id, research_package, narrative_plan, packaging, script_plan = (
        _create_project_in_script_verification(engine)
    )
    _run_adapter(ScriptVerifyAdapter(), engine, project_id, _report_json())

    assert gate_decision_for(engine, project_id, ApprovalGateType.SCRIPT_APPROVAL, None) is None

    accept_script_verification(engine, project_id)
    assert gate_decision_for(engine, project_id, ApprovalGateType.SCRIPT_APPROVAL, None) is None  # still pending

    approve_final_script(engine, project_id)
    decision = gate_decision_for(engine, project_id, ApprovalGateType.SCRIPT_APPROVAL, None)
    assert decision.decision is ApprovalDecisionType.APPROVED
    assert decision.subject_artifact_id == script_plan.id  # resolved from project.script_plan_id


def test_script_approval_gate_rejected(engine):
    project_id, research_package, narrative_plan, packaging, script_plan = (
        _create_project_in_script_verification(engine)
    )
    _run_adapter(ScriptVerifyAdapter(), engine, project_id, _report_json())
    accept_script_verification(engine, project_id)

    reject_final_script(engine, project_id, feedback="not good enough")

    decision = gate_decision_for(engine, project_id, ApprovalGateType.SCRIPT_APPROVAL, None)
    assert decision.decision is ApprovalDecisionType.REJECTED


def test_wrong_subject_id_does_not_matter_for_legacy_gates(engine):
    """Unlike FINAL_MEDIA_APPROVAL, legacy gates never match on
    subject_artifact_id (there is no artifact-id-keyed decision table for
    them) -- the decision is entirely a function of ProjectState/
    HumanApproval, so an arbitrary subject id still returns the correct
    reconstructed decision."""
    project_id, idea = _create_project_in_idea_review(engine)
    approve_idea(engine, project_id)

    decision = gate_decision_for(engine, project_id, ApprovalGateType.IDEA_APPROVAL, uuid4())
    assert decision.decision is ApprovalDecisionType.APPROVED


def test_decision_persists_across_storage_reload(engine):
    project_id, idea = _create_project_in_idea_review(engine)
    approve_idea(engine, project_id)

    first = gate_decision_for(engine, project_id, ApprovalGateType.IDEA_APPROVAL, idea.id)
    second = gate_decision_for(engine, project_id, ApprovalGateType.IDEA_APPROVAL, idea.id)
    assert first.decision == second.decision == ApprovalDecisionType.APPROVED


def test_approve_gate_refuses_legacy_gate_types(engine):
    graph = build_full_graph()
    with pytest.raises(LegacyGateNotWritableError):
        approve_gate(engine, graph, uuid4(), ApprovalGateType.IDEA_APPROVAL, uuid4())
