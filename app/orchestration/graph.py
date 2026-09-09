"""ProductionGraph: a static, code-defined dependency DAG over
production node ids (Phase 33).

Dependencies are never inferred from artifact fields at runtime
(requirement #4) -- `ProductionNodeDefinition.dependencies` is the SOLE
source of execution order; artifact fields (e.g. TimelineManifest.
assembly_plan_id) are used elsewhere only to validate that an artifact
is CURRENT, never to decide what depends on what.

Validated once at construction time (requirement #5): every node id is
unique, every dependency references a node actually registered in the
graph, no node depends on itself, and the graph contains no cycle. Any
violation is a `DuplicateNodeError`/`UnknownDependencyError`/
`SelfDependencyError`/`ProductionGraphCycleError` -- a configuration bug,
never a per-project outcome, so these are all appropriate to raise
directly rather than represent as a node status.

Topological order is deterministic (requirement #5/#31): Kahn's
algorithm, but at every step the set of currently-runnable (zero
remaining in-degree) nodes is processed in sorted node_id order rather
than arbitrary set/dict iteration order -- so two independent nodes with
no dependency relationship always appear in the same relative order
across repeated calls, process restarts, and Python versions.
"""

from __future__ import annotations

from dataclasses import dataclass

from app.orchestration.errors import (
    DuplicateNodeError,
    ProductionGraphCycleError,
    SelfDependencyError,
    UnknownDependencyError,
    UnknownNodeError,
)
from app.orchestration.models import ApprovalGateType


@dataclass(frozen=True)
class ProductionNodeDefinition:
    """One node's own static identity in the graph. `adapter` is set by
    app/orchestration/registry.py when building the default graph -- kept
    optional here so app/orchestration/graph.py itself never needs to
    import any adapter/renderer, staying a pure topology module."""

    node_id: str
    dependencies: tuple[str, ...] = ()
    gate_after: ApprovalGateType | None = None


class ProductionGraph:
    def __init__(self, definitions: list[ProductionNodeDefinition]):
        self._definitions: dict[str, ProductionNodeDefinition] = {}
        for definition in definitions:
            if definition.node_id in self._definitions:
                raise DuplicateNodeError(f"Duplicate production node id: {definition.node_id!r}")
            self._definitions[definition.node_id] = definition

        for definition in self._definitions.values():
            for dependency in definition.dependencies:
                if dependency == definition.node_id:
                    raise SelfDependencyError(
                        f"Node {definition.node_id!r} declares itself as its own dependency"
                    )
                if dependency not in self._definitions:
                    raise UnknownDependencyError(
                        f"Node {definition.node_id!r} depends on unknown node {dependency!r}"
                    )

        self._topological_order: list[str] = _compute_topological_order(self._definitions)

    def get(self, node_id: str) -> ProductionNodeDefinition:
        try:
            return self._definitions[node_id]
        except KeyError:
            raise UnknownNodeError(f"Unknown production node id: {node_id!r}") from None

    def node_ids(self) -> list[str]:
        return list(self._topological_order)

    def topological_order(self) -> list[str]:
        """The full graph's own deterministic topological order."""
        return list(self._topological_order)

    def ancestors_closure(self, target_node: str) -> list[str]:
        """Every node the target transitively depends on, PLUS the
        target itself, in the graph's own stable topological order
        (requirement #31: 'target ancestor closure'). Never includes a
        descendant of the target -- `run_until(target)` must never
        execute beyond what was requested (requirement #8)."""
        self.get(target_node)  # raises UnknownNodeError if not registered

        required: set[str] = set()
        stack = [target_node]
        while stack:
            node_id = stack.pop()
            if node_id in required:
                continue
            required.add(node_id)
            stack.extend(self._definitions[node_id].dependencies)

        return [node_id for node_id in self._topological_order if node_id in required]


def _compute_topological_order(definitions: dict[str, ProductionNodeDefinition]) -> list[str]:
    in_degree = {node_id: 0 for node_id in definitions}
    dependents: dict[str, list[str]] = {node_id: [] for node_id in definitions}
    for definition in definitions.values():
        in_degree[definition.node_id] = len(definition.dependencies)
        for dependency in definition.dependencies:
            dependents[dependency].append(definition.node_id)

    ready = sorted(node_id for node_id, degree in in_degree.items() if degree == 0)
    order: list[str] = []

    while ready:
        node_id = ready.pop(0)
        order.append(node_id)
        newly_ready = []
        for dependent in sorted(dependents[node_id]):
            in_degree[dependent] -= 1
            if in_degree[dependent] == 0:
                newly_ready.append(dependent)
        ready = sorted(ready + newly_ready)

    if len(order) != len(definitions):
        remaining = sorted(set(definitions) - set(order))
        raise ProductionGraphCycleError(remaining)

    return order
