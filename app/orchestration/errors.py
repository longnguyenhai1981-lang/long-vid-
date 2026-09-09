"""Domain errors for app/orchestration/ (Phase 33).

Two categories, mirroring every prior phase's own infra-vs-ordinary-
outcome split:

- Graph/configuration errors (DuplicateNodeError, UnknownDependencyError,
  SelfDependencyError, ProductionGraphCycleError) are infrastructure/
  configuration errors -- a malformed graph is a programming bug, never a
  per-project outcome, and these are raised at graph-construction time
  (module import / test collection), never during a normal run.
- NodeNotWiredError is raised only by UnwiredNodeAdapter.execute() -- it
  means Phase 33 has no real adapter for this node (an LLM-based
  creative-planning stage; see app/orchestration/adapters.py's own module
  docstring), never that the node's artifact is missing or stale (those
  are ordinary ProductionNodeStatus.BLOCKED outcomes, not exceptions).
- ApprovalGateError / UnknownApprovalGateError are raised for malformed
  approval-service calls (e.g. approving a gate with no matching node),
  a caller-contract violation, not a media/content defect.

Ordinary per-node outcomes (a dependency unsatisfied, a gate awaiting a
decision, a rejected gate, a stale upstream artifact, a failed module
execution) are represented as ProductionNodeStatus values on a
NodeRunRecord -- never exceptions. ProductionRunner.run_until() only
raises for the categories above; an ordinary blocked/failed/waiting
production run is a normal return value.
"""

from __future__ import annotations


class OrchestrationError(Exception):
    """Base class for all app/orchestration/ errors."""


class DuplicateNodeError(OrchestrationError):
    """Raised when two ProductionNodeDefinitions share the same node_id."""


class UnknownDependencyError(OrchestrationError):
    """Raised when a node declares a dependency on a node_id that is not
    itself registered in the graph."""


class SelfDependencyError(OrchestrationError):
    """Raised when a node declares itself as its own dependency."""


class ProductionGraphCycleError(OrchestrationError):
    """Raised when the dependency graph contains a cycle -- no valid
    topological order exists."""

    def __init__(self, remaining_node_ids: list[str]):
        self.remaining_node_ids = remaining_node_ids
        super().__init__(f"Production graph has a cycle involving: {sorted(remaining_node_ids)}")


class UnknownNodeError(OrchestrationError):
    """Raised when a caller (e.g. run_until(target_node)) names a
    node_id that is not registered in the graph."""


class NodeNotWiredError(OrchestrationError):
    """Raised by UnwiredNodeAdapter.execute() -- this node has no real
    Phase 33 adapter (it is an LLM-based creative-planning stage; see
    app/orchestration/adapters.py). Its existing engine/review-service
    flow must be run directly; the orchestrator can only detect whether
    its artifact already exists, never produce one."""


class UnknownApprovalGateError(OrchestrationError):
    """Raised when approve_gate/reject_gate is called with a gate_type
    that has no node in the graph declaring it via gate_after."""


class LegacyGateNotWritableError(OrchestrationError):
    """Raised by approve_gate/reject_gate (Phase 34) when called with a
    gate_type backed by the existing app/review/service.py flow (every
    ApprovalGateType except FINAL_MEDIA_APPROVAL). The real decision must
    be made through that service's own functions (approve_idea,
    decide_feasibility, approve_narrative, approve_packaging_p0,
    accept_script_verification, approve_final_script/reject_final_script,
    or their revise/send-back counterparts) -- never duplicated into a
    second, competing ApprovalDecision row."""
