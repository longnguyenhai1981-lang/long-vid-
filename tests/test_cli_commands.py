"""Phase 35 requirements #35-42, #44: CLI command tests via Typer's
CliRunner, driving the whole command stack in-process (no subprocess,
no live network) through the two explicit DI seams
(app.cli.main._composition_override / _context_factory_override)."""

from __future__ import annotations

import json
from unittest.mock import MagicMock
from uuid import uuid4

import pytest
from typer.testing import CliRunner

import app.cli.main as cli_main
from app.bootstrap import AppComposition
from app.cli.exit_codes import EXIT_BLOCKED, EXIT_INVALID_INPUT, EXIT_SUCCESS, EXIT_WAITING_APPROVAL
from app.llm.fake import FakeLLMProvider
from app.llm.models import LLMResponse
from app.orchestration.registry import ExecutionContext
from app.production_adapters.registry import build_full_adapters, build_full_graph
from app.services.production_service import ProductionService
from app.storage.database import init_database
from tests.test_idea_engine import VALID_IDEA_JSON, _llm_settings

runner = CliRunner()


def _fake_response(text: str) -> LLMResponse:
    return LLMResponse(text=text, provider="fake", model="fake-model")


@pytest.fixture()
def composition(tmp_path):
    db_engine = init_database(tmp_path / "cli_test.db")
    graph = build_full_graph()
    adapters = build_full_adapters()
    service = ProductionService(db_engine, graph, adapters)
    comp = AppComposition(db_engine=db_engine, graph=graph, adapters=adapters, service=service)
    cli_main._composition_override = comp

    def _context_factory(project_id, db_engine):
        # A fresh FakeLLMProvider queued with exactly one IDEA response per
        # call -- sufficient for every test here, since IDEA is the only
        # node these tests actually execute for real (once produced, it is
        # always reused, never re-executed, on any later resume).
        return ExecutionContext(
            db_engine=db_engine, project_id=project_id,
            llm_provider=FakeLLMProvider([_fake_response(VALID_IDEA_JSON)]),
            llm_settings=_llm_settings(),
        )

    cli_main._context_factory_override = _context_factory
    yield comp
    cli_main._composition_override = None
    cli_main._context_factory_override = None


@pytest.fixture()
def queued_composition(tmp_path):
    """Like `composition`, but the context factory pulls its
    llm_provider from a mutable holder the test can reassign between CLI
    invocations -- mirroring how a real caller would reconfigure a fresh
    FakeLLMProvider between stages when different upstream ids become
    known only after each step actually executes (Phase 34's own
    integration test uses the identical pattern one level down, against
    ProductionRunner directly)."""
    db_engine = init_database(tmp_path / "cli_test_queued.db")
    graph = build_full_graph()
    adapters = build_full_adapters()
    service = ProductionService(db_engine, graph, adapters)
    comp = AppComposition(db_engine=db_engine, graph=graph, adapters=adapters, service=service)
    cli_main._composition_override = comp

    holder: dict = {"provider": FakeLLMProvider([_fake_response(VALID_IDEA_JSON)])}

    def _context_factory(project_id, db_engine):
        return ExecutionContext(
            db_engine=db_engine, project_id=project_id, llm_provider=holder["provider"], llm_settings=_llm_settings(),
            research_retriever=holder.get("retriever"),
        )

    cli_main._context_factory_override = _context_factory
    yield comp, holder
    cli_main._composition_override = None
    cli_main._context_factory_override = None


def test_produce_invokes_service_start_exactly_once(composition, monkeypatch):
    fake_run = MagicMock()
    fake_run.status.value = "WAITING_APPROVAL"
    fake_run.status = __import__("app.orchestration.models", fromlist=["ProductionRunStatus"]).ProductionRunStatus.WAITING_APPROVAL
    fake_run.id = uuid4()
    fake_run.project_id = uuid4()
    fake_run.target_node = "SCRIPT_VERIFY"
    fake_run.stop_reason = "IDEA:WAITING_GATE"
    fake_run.waiting_gate = "IDEA_APPROVAL"
    fake_run.node_states = {}

    start_mock = MagicMock(return_value=fake_run)
    monkeypatch.setattr(composition.service, "start", start_mock)

    result = runner.invoke(cli_main.app, ["produce", "--brief", "Tacoma bridge", "--target", "script"])

    assert start_mock.call_count == 1
    called_target = start_mock.call_args[0][1]
    assert called_target == "script"
    assert result.exit_code == EXIT_WAITING_APPROVAL


def test_produce_descendant_media_nodes_not_executed_for_script_target(composition):
    result = runner.invoke(
        cli_main.app,
        ["produce", "--brief", "Tacoma bridge", "--target", "script", "--json"],
    )
    data = json.loads(result.stdout)
    all_nodes = set(data["executed_nodes"]) | set(data["reused_nodes"]) | set(data["blocked_nodes"]) | set(data["failed_nodes"])
    assert "VOICE_RENDER" not in all_nodes
    assert "VIDEO_RENDER" not in all_nodes
    assert "MEDIA_QC" not in all_nodes


def test_produce_without_brief_or_project_id_is_invalid_input(composition):
    result = runner.invoke(cli_main.app, ["produce", "--target", "idea"])
    assert result.exit_code == EXIT_INVALID_INPUT


def test_produce_unknown_target_is_invalid_input(composition):
    result = runner.invoke(cli_main.app, ["produce", "--brief", "x", "--target", "not-a-real-target"])
    assert result.exit_code == EXIT_INVALID_INPUT


def test_produce_stops_at_waiting_approval_with_exit_code_2_and_correct_gate(composition):
    result = runner.invoke(
        cli_main.app, ["produce", "--brief", "Tacoma bridge", "--target", "idea", "--json"],
    )
    assert result.exit_code == EXIT_WAITING_APPROVAL
    data = json.loads(result.stdout)
    assert data["status"] == "WAITING_APPROVAL"
    assert data["waiting_gate"] == "IDEA_APPROVAL"
    assert data["subject_artifact_id"] is not None


def test_produce_human_output_shows_gate_and_subject(composition):
    result = runner.invoke(cli_main.app, ["produce", "--brief", "Tacoma bridge", "--target", "idea"])
    assert "Stopped at: IDEA_APPROVAL" in result.stdout
    assert "Subject:" in result.stdout
    assert "motily approve" in result.stdout


def test_approve_does_not_automatically_resume(composition):
    produce_result = runner.invoke(cli_main.app, ["produce", "--brief", "Tacoma bridge", "--target", "idea", "--json"])
    run_id = json.loads(produce_result.stdout)["production_run_id"]

    approve_result = runner.invoke(cli_main.app, ["approve", run_id, "--gate", "idea"])
    assert approve_result.exit_code == EXIT_SUCCESS
    assert "APPROVED" in approve_result.stdout

    status_result = runner.invoke(cli_main.app, ["status", run_id, "--json"])
    # The persisted run row is still the one from `produce` -- WAITING_APPROVAL,
    # since approving alone never re-executes anything.
    assert json.loads(status_result.stdout)["status"] == "WAITING_APPROVAL"


def test_full_approve_then_resume_workflow(composition):
    produce_result = runner.invoke(cli_main.app, ["produce", "--brief", "Tacoma bridge", "--target", "idea", "--json"])
    run_id = json.loads(produce_result.stdout)["production_run_id"]

    runner.invoke(cli_main.app, ["approve", run_id])

    resume_result = runner.invoke(cli_main.app, ["resume", run_id, "--json"])
    data = json.loads(resume_result.stdout)
    assert data["status"] == "SUCCEEDED"
    assert resume_result.exit_code == EXIT_SUCCESS
    assert "IDEA" in data["reused_nodes"]


def _advance_to_script_approval(queued_composition) -> str:
    """Drives produce/approve through IDEA -> RESEARCH_R0 -> FEASIBILITY
    -> RESEARCH_R1 -> NARRATIVE -> PACKAGING_P0 -> SCRIPT -> SCRIPT_VERIFY
    entirely through the CLI, exactly mirroring
    tests/test_full_production_integration.py's own real-adapter chain
    (needed because the orchestrator's freshness model for these five
    nodes requires REAL ModuleRun rows, which the *_engine.py test files'
    own shortcut fixtures deliberately don't create). Returns the run id
    now WAITING_APPROVAL at SCRIPT_APPROVAL."""
    from app.research.fake import FakeResearchRetriever
    from tests.test_feasibility_engine import _feasibility_json
    from tests.test_narrative_engine import _plan_json
    from tests.test_packaging_p0_engine import _prototype_json
    from tests.test_r0_research_engine import _evidence_response, _research_json
    from tests.test_r1_research_engine import _package_json
    from tests.test_script_engine import _beat_dict, _line_dict, _script_json
    from tests.test_script_verification_engine import _report_json

    comp, holder = queued_composition
    evidence = _evidence_response(["https://real.example/source"], ["Real Paper"])
    holder["retriever"] = FakeResearchRetriever([evidence] * 30)

    # ONE target for the whole run -- `resume` always continues toward
    # the SAME target a run was `produce`d with (it has no notion of
    # "advance further"); every gate along the way is handled by
    # approve+resume against that one unchanging target, exactly like a
    # real `motily produce --target script` session would be driven.
    produce_result = runner.invoke(cli_main.app, ["produce", "--brief", "Tacoma bridge", "--target", "script", "--json"])
    run_id = json.loads(produce_result.stdout)["production_run_id"]
    runner.invoke(cli_main.app, ["approve", run_id])

    holder["provider"] = FakeLLMProvider([_fake_response(_research_json(uuid4())), _fake_response(_feasibility_json())])
    runner.invoke(cli_main.app, ["resume", run_id])
    runner.invoke(cli_main.app, ["approve", run_id])

    holder["provider"] = FakeLLMProvider([_fake_response(_package_json()), _fake_response(_plan_json())])
    runner.invoke(cli_main.app, ["resume", run_id])
    runner.invoke(cli_main.app, ["approve", run_id])

    holder["provider"] = FakeLLMProvider([_fake_response(_prototype_json())])
    runner.invoke(cli_main.app, ["resume", run_id])
    runner.invoke(cli_main.app, ["approve", run_id])

    four_line_beats = [
        _beat_dict("B001", lines=[_line_dict("L001"), _line_dict("L002")]),
        _beat_dict("B002", lines=[_line_dict("L003"), _line_dict("L004")]),
    ]
    holder["provider"] = FakeLLMProvider(
        [_fake_response(_script_json(beats=four_line_beats)), _fake_response(_report_json())]
    )
    first_script_resume = runner.invoke(cli_main.app, ["resume", run_id, "--json"])
    data = json.loads(first_script_resume.stdout)
    assert data["status"] == "WAITING_APPROVAL"
    assert data["waiting_gate"] == "SCRIPT_APPROVAL"

    # SCRIPT_APPROVAL spans two real states (SCRIPT_VERIFICATION then
    # SCRIPT_REVIEW) -- one approve+resume cycle clears the first step
    # only; the gate still reads WAITING_APPROVAL until the second.
    runner.invoke(cli_main.app, ["approve", run_id, "--gate", "script"])
    still_waiting = runner.invoke(cli_main.app, ["resume", run_id, "--json"])
    still_data = json.loads(still_waiting.stdout)
    assert still_data["status"] == "WAITING_APPROVAL"
    assert still_data["waiting_gate"] == "SCRIPT_APPROVAL"

    return run_id


def test_reject_blocks_production_and_reports_correctly(queued_composition):
    run_id = _advance_to_script_approval(queued_composition)

    reject_result = runner.invoke(cli_main.app, ["reject", run_id, "--gate", "script", "--note", "not ready"])
    assert reject_result.exit_code == EXIT_SUCCESS
    assert "REJECTED" in reject_result.stdout
    assert "blocked" in reject_result.stdout.lower()

    resume_result = runner.invoke(cli_main.app, ["resume", run_id, "--json"])
    data = json.loads(resume_result.stdout)
    assert data["status"] == "BLOCKED"
    assert data["stop_reason"].endswith("GATE_REJECTED")
    assert resume_result.exit_code == EXIT_BLOCKED


def test_status_is_read_only(composition):
    from app.storage.module_runs import list_module_runs_for_project

    produce_result = runner.invoke(cli_main.app, ["produce", "--brief", "Tacoma bridge", "--target", "idea", "--json"])
    data = json.loads(produce_result.stdout)
    run_id = data["production_run_id"]
    project_id = data["project_id"]

    runs_before = len(list_module_runs_for_project(composition.db_engine, __import__("uuid").UUID(project_id)))

    status_result = runner.invoke(cli_main.app, ["status", run_id, "--verbose"])
    assert status_result.exit_code == EXIT_SUCCESS
    assert "IDEA" in status_result.stdout
    assert "NODE" in status_result.stdout  # verbose trace header

    runs_after = len(list_module_runs_for_project(composition.db_engine, __import__("uuid").UUID(project_id)))
    assert runs_before == runs_after  # no new ModuleRun created by a read-only status call


def test_status_unknown_run_id_is_invalid_input(composition):
    result = runner.invoke(cli_main.app, ["status", str(uuid4())])
    assert result.exit_code == EXIT_INVALID_INPUT


def test_runs_lists_newest_first_with_limit(composition):
    project_id = composition.service.create_project(title="multi-run project")
    ids = []
    for i in range(3):
        # A bare context (no llm_provider/initial_idea_input) -- IDEA always
        # FAILS immediately, which is irrelevant here; only run ordering/
        # limit is under test.
        ctx = ExecutionContext(db_engine=composition.db_engine, project_id=project_id)
        run = composition.service._runner.run_until(project_id, "IDEA", ctx=ctx)
        ids.append(str(run.id))

    result = runner.invoke(cli_main.app, ["runs", "--project-id", str(project_id), "--limit", "2", "--json"])
    data = json.loads(result.stdout)["runs"]
    assert len(data) == 2
    assert [r["production_run_id"] for r in data] == list(reversed(ids))[:2]


def test_runs_does_not_execute_anything(composition):
    project_id = composition.service.create_project(title="empty project")
    result = runner.invoke(cli_main.app, ["runs", "--project-id", str(project_id)])
    assert result.exit_code == EXIT_SUCCESS
    assert "No production runs found" in result.stdout
