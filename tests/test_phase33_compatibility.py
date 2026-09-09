"""Phase 34 requirement #50: a Phase-33-style persisted ProductionRun
(built with only the 7 downstream nodes wired, the other 12 recorded as
BLOCKED/NODE_NOT_WIRED via UnwiredNodeAdapter) must still deserialize
correctly, and the NEW full registry can execute those same nodes on a
later resume once the required inputs are available -- no destructive
migration, no persistence break (requirement #49)."""

from __future__ import annotations

from uuid import uuid4

from app.orchestration.adapters import build_default_adapters, build_default_graph
from app.orchestration.models import ProductionNodeStatus, ProductionRunStatus
from app.orchestration.registry import ExecutionContext
from app.orchestration.runner import ProductionRunner
from app.production_adapters.registry import build_full_adapters, build_full_graph
from app.storage.database import init_database
from app.storage.production_runs import get_production_run


def test_phase33_style_run_persists_and_deserializes_with_unwired_upstream(tmp_path):
    engine = init_database(tmp_path / "db.sqlite")
    project_id = uuid4()

    old_graph = build_default_graph()
    old_adapters = build_default_adapters()
    old_runner = ProductionRunner(engine, old_graph, old_adapters)
    ctx = ExecutionContext(db_engine=engine, project_id=project_id)

    run = old_runner.run_until(project_id, "MEDIA_QC", ctx=ctx)

    # Every upstream creative node is BLOCKED/NODE_NOT_WIRED, exactly the
    # Phase 33 shape -- and it still stops the whole run at BLOCKED (its
    # own dependency chain never reaches VOICE_RENDER).
    assert run.status is ProductionRunStatus.BLOCKED
    unwired_nodes = {
        "IDEA", "RESEARCH_R0", "FEASIBILITY", "RESEARCH_R1", "NARRATIVE",
        "PACKAGING_P0", "SCRIPT", "SCRIPT_VERIFY", "VOICE_PLAN", "VISUAL_PLAN",
        "ASSEMBLY_PLAN", "PACKAGING_P1",
    }
    for node_id in unwired_nodes & set(run.node_states.keys()):
        assert run.node_states[node_id].status is ProductionNodeStatus.BLOCKED

    # Reload from storage -- a completely fresh read, no in-memory object
    # reused -- proving the persisted row itself is still valid.
    reloaded = get_production_run(engine, run.id)
    assert reloaded.id == run.id
    assert reloaded.status is ProductionRunStatus.BLOCKED
    for node_id, record in reloaded.node_states.items():
        assert record.status == run.node_states[node_id].status


def test_new_registry_can_resume_a_phase33_style_run_once_wired(tmp_path):
    """The SAME persisted ProductionRun row, resumed through the NEW
    Phase 34 registry, can now push past nodes that were previously
    BLOCKED/NODE_NOT_WIRED -- proving the upgrade path needs no
    migration, just resuming with a richer adapter set."""
    engine = init_database(tmp_path / "db.sqlite")
    project_id = uuid4()

    old_graph = build_default_graph()
    old_adapters = build_default_adapters()
    old_runner = ProductionRunner(engine, old_graph, old_adapters)
    ctx = ExecutionContext(db_engine=engine, project_id=project_id)
    old_run = old_runner.run_until(project_id, "MEDIA_QC", ctx=ctx)
    assert old_run.node_states["IDEA"].status is ProductionNodeStatus.BLOCKED

    new_graph = build_full_graph()
    new_adapters = build_full_adapters()
    new_runner = ProductionRunner(engine, new_graph, new_adapters)

    # Resuming the identical run_id with no production input still fails
    # cleanly (IDEA needs a real brief) -- but it is a FAILED node now,
    # not a silently-permanent BLOCKED/NODE_NOT_WIRED: the boundary moved
    # from "no adapter exists" to "adapter exists, needs real input".
    from app.orchestration.registry import ExecutionContext as _ExecutionContext

    resumed = new_runner.resume(old_run.id, ctx=_ExecutionContext(db_engine=engine, project_id=project_id))
    assert resumed.id == old_run.id
    assert resumed.node_states["IDEA"].status is ProductionNodeStatus.FAILED
    assert "ProductionInputMissingError" in resumed.node_states["IDEA"].message
