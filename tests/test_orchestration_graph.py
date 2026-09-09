"""Phase 33 focused tests: app/orchestration/graph.py's DAG validation
and deterministic topological ordering (requirements #5/#31)."""

from __future__ import annotations

from app.orchestration.errors import (
    DuplicateNodeError,
    ProductionGraphCycleError,
    SelfDependencyError,
    UnknownDependencyError,
    UnknownNodeError,
)
from app.orchestration.graph import ProductionGraph, ProductionNodeDefinition
import pytest


def test_valid_linear_graph_topological_order():
    graph = ProductionGraph(
        [
            ProductionNodeDefinition(node_id="A"),
            ProductionNodeDefinition(node_id="B", dependencies=("A",)),
            ProductionNodeDefinition(node_id="C", dependencies=("B",)),
        ]
    )
    assert graph.topological_order() == ["A", "B", "C"]


def test_duplicate_node_id_rejected():
    with pytest.raises(DuplicateNodeError):
        ProductionGraph([ProductionNodeDefinition(node_id="A"), ProductionNodeDefinition(node_id="A")])


def test_unknown_dependency_rejected():
    with pytest.raises(UnknownDependencyError):
        ProductionGraph([ProductionNodeDefinition(node_id="A", dependencies=("MISSING",))])


def test_self_dependency_rejected():
    with pytest.raises(SelfDependencyError):
        ProductionGraph([ProductionNodeDefinition(node_id="A", dependencies=("A",))])


def test_cycle_rejected():
    with pytest.raises(ProductionGraphCycleError) as exc_info:
        ProductionGraph(
            [
                ProductionNodeDefinition(node_id="A", dependencies=("B",)),
                ProductionNodeDefinition(node_id="B", dependencies=("A",)),
            ]
        )
    assert exc_info.value.remaining_node_ids == ["A", "B"]


def test_get_unknown_node_raises():
    graph = ProductionGraph([ProductionNodeDefinition(node_id="A")])
    with pytest.raises(UnknownNodeError):
        graph.get("MISSING")


def test_ancestors_closure_includes_target_and_dependencies_only():
    graph = ProductionGraph(
        [
            ProductionNodeDefinition(node_id="A"),
            ProductionNodeDefinition(node_id="B", dependencies=("A",)),
            ProductionNodeDefinition(node_id="C", dependencies=("B",)),
            ProductionNodeDefinition(node_id="D"),  # unrelated branch
        ]
    )
    assert graph.ancestors_closure("B") == ["A", "B"]
    assert graph.ancestors_closure("C") == ["A", "B", "C"]
    assert "D" not in graph.ancestors_closure("C")


def test_ancestors_closure_unknown_target_raises():
    graph = ProductionGraph([ProductionNodeDefinition(node_id="A")])
    with pytest.raises(UnknownNodeError):
        graph.ancestors_closure("MISSING")


def test_topological_order_is_stable_tie_break_across_independent_nodes():
    """Two independent nodes with no dependency relationship must always
    sort by node_id, never by insertion/dict order (requirement #31)."""
    graph_one = ProductionGraph(
        [ProductionNodeDefinition(node_id="Z"), ProductionNodeDefinition(node_id="A")]
    )
    graph_two = ProductionGraph(
        [ProductionNodeDefinition(node_id="A"), ProductionNodeDefinition(node_id="Z")]
    )
    assert graph_one.topological_order() == ["A", "Z"] == graph_two.topological_order()


def test_topological_order_deterministic_across_repeated_calls():
    graph = ProductionGraph(
        [
            ProductionNodeDefinition(node_id="A"),
            ProductionNodeDefinition(node_id="B"),
            ProductionNodeDefinition(node_id="C", dependencies=("A", "B")),
        ]
    )
    first = graph.topological_order()
    second = graph.topological_order()
    assert first == second == ["A", "B", "C"]


def test_diamond_dependency_ancestor_closure_deterministic():
    graph = ProductionGraph(
        [
            ProductionNodeDefinition(node_id="A"),
            ProductionNodeDefinition(node_id="B", dependencies=("A",)),
            ProductionNodeDefinition(node_id="C", dependencies=("A",)),
            ProductionNodeDefinition(node_id="D", dependencies=("B", "C")),
        ]
    )
    assert graph.ancestors_closure("D") == ["A", "B", "C", "D"]
