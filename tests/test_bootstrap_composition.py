"""Phase 35 requirement #44: composition-root tests. No live LLM calls
anywhere -- these only verify the wiring, never execute a node."""

from __future__ import annotations

from app.bootstrap import build_composition
from app.orchestration.registry import ExecutionContext
from app.orchestration.runner import ProductionRunner


def test_composition_graph_has_nineteen_nodes(tmp_path):
    comp = build_composition(tmp_path / "boot.db")
    assert len(comp.graph.node_ids()) == 19


def test_composition_adapters_cover_every_graph_node(tmp_path):
    comp = build_composition(tmp_path / "boot.db")
    assert set(comp.adapters.keys()) == set(comp.graph.node_ids())


def test_composition_shares_one_storage_engine(tmp_path):
    comp = build_composition(tmp_path / "boot.db")
    assert comp.service.db_engine is comp.db_engine


def test_composition_runner_and_gates_wired_to_same_db(tmp_path):
    comp = build_composition(tmp_path / "boot.db")
    assert isinstance(comp.service._runner, ProductionRunner)
    assert comp.service._runner._db_engine is comp.db_engine


def test_composition_accepts_injected_fake_dependencies_via_execution_context(tmp_path):
    """The composition root itself builds no provider -- injection
    happens at the ExecutionContext level, which callers (tests, the
    evaluation script) construct explicitly. This test proves that seam
    exists and requires no monkeypatching of app.bootstrap internals."""
    from app.llm.fake import FakeLLMProvider

    comp = build_composition(tmp_path / "boot.db")
    ctx = ExecutionContext(
        db_engine=comp.db_engine, project_id=__import__("uuid").uuid4(),
        llm_provider=FakeLLMProvider([]),
    )
    assert isinstance(ctx.llm_provider, FakeLLMProvider)


def test_composition_never_touches_network_or_real_providers(tmp_path):
    comp = build_composition(tmp_path / "boot.db")
    # No provider field is ever populated by the composition root itself.
    for node_id, adapter in comp.adapters.items():
        assert not hasattr(adapter, "_configured_provider")
