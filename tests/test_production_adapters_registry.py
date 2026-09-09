"""Phase 34 requirement #41: full-graph registration coverage.

Every node app/orchestration/graph.py's own 19-node topology declares
must have a REAL, executable adapter in app/production_adapters/
registry.py's combined set -- no UnwiredNodeAdapter left silently
standing in for a node this phase claims to support.
"""

from __future__ import annotations

from app.orchestration.registry import UnwiredNodeAdapter
from app.production_adapters.registry import build_full_adapters, build_full_graph

_EXPECTED_NODE_IDS = {
    "IDEA", "RESEARCH_R0", "FEASIBILITY", "RESEARCH_R1", "NARRATIVE", "PACKAGING_P0",
    "SCRIPT", "SCRIPT_VERIFY", "VOICE_PLAN", "VISUAL_PLAN", "ASSEMBLY_PLAN", "PACKAGING_P1",
    "VOICE_RENDER", "VISUAL_RENDER", "TIMELINE", "VIDEO_RENDER", "CAPTION_BUILD",
    "SUBTITLE_EXPORT", "MEDIA_QC",
}


def test_full_graph_has_exactly_nineteen_nodes():
    graph = build_full_graph()
    assert set(graph.node_ids()) == _EXPECTED_NODE_IDS


def test_full_adapter_set_covers_every_graph_node():
    graph = build_full_graph()
    adapters = build_full_adapters()
    assert set(adapters.keys()) == set(graph.node_ids()) == _EXPECTED_NODE_IDS


def test_no_unwired_adapter_remains_in_the_full_registry():
    """Requirement #30/#41: an intentionally-unsupported node must force
    an explicit, documented exemption rather than silently passing --
    there is no such exemption in Phase 34, so none may remain."""
    adapters = build_full_adapters()
    unwired = [node_id for node_id, adapter in adapters.items() if isinstance(adapter, UnwiredNodeAdapter)]
    assert unwired == [], f"UnwiredNodeAdapter still present for: {unwired}"


def test_exactly_six_gates_are_placed():
    graph = build_full_graph()
    gated = {node_id: graph.get(node_id).gate_after for node_id in graph.node_ids() if graph.get(node_id).gate_after is not None}
    assert set(gated.keys()) == {"IDEA", "FEASIBILITY", "NARRATIVE", "PACKAGING_P0", "SCRIPT_VERIFY", "MEDIA_QC"}
