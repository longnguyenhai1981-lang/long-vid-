"""Phase 35 requirement #33: a stable CLI DTO, decoupled from
`ProductionRun`'s own persisted shape -- a future change to the
orchestration model must not accidentally break every script parsing
`motily ... --json` output.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from app.orchestration.models import NodeRunRecord, ProductionNodeStatus, ProductionRun


@dataclass(frozen=True)
class ProductionRunDTO:
    production_run_id: str
    project_id: str
    target: str
    status: str
    stop_reason: str | None
    waiting_gate: str | None
    subject_artifact_id: str | None
    executed_nodes: list[str] = field(default_factory=list)
    reused_nodes: list[str] = field(default_factory=list)
    blocked_nodes: list[str] = field(default_factory=list)
    failed_nodes: list[str] = field(default_factory=list)

    def to_json_dict(self) -> dict:
        return {
            "production_run_id": self.production_run_id,
            "project_id": self.project_id,
            "target": self.target,
            "status": self.status,
            "stop_reason": self.stop_reason,
            "waiting_gate": self.waiting_gate,
            "subject_artifact_id": self.subject_artifact_id,
            "executed_nodes": self.executed_nodes,
            "reused_nodes": self.reused_nodes,
            "blocked_nodes": self.blocked_nodes,
            "failed_nodes": self.failed_nodes,
        }


def _find_waiting_node(run: ProductionRun) -> tuple[str, NodeRunRecord] | None:
    for node_id, record in run.node_states.items():
        if record.status is ProductionNodeStatus.WAITING_APPROVAL:
            return node_id, record
    return None


def build_run_dto(run: ProductionRun) -> ProductionRunDTO:
    waiting = _find_waiting_node(run)
    executed = [n for n, r in run.node_states.items() if r.executed_this_run]
    reused = [n for n, r in run.node_states.items() if r.reused_existing_artifact]
    blocked = [n for n, r in run.node_states.items() if r.status is ProductionNodeStatus.BLOCKED]
    failed = [n for n, r in run.node_states.items() if r.status is ProductionNodeStatus.FAILED]
    return ProductionRunDTO(
        production_run_id=str(run.id),
        project_id=str(run.project_id),
        target=run.target_node,
        status=run.status.value,
        stop_reason=run.stop_reason,
        waiting_gate=run.waiting_gate,
        subject_artifact_id=waiting[1].artifact_id if waiting else None,
        executed_nodes=executed,
        reused_nodes=reused,
        blocked_nodes=blocked,
        failed_nodes=failed,
    )
