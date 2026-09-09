"""NodeAdapter contract and ExecutionContext (Phase 33 requirement #10/
#19/#20).

`NodeAdapter` is the boundary between the orchestrator and every
existing module: the orchestrator only ever calls `load_current`/
`is_fresh`/`execute`/`gate_ok` -- it never knows how a renderer resolves
its own inputs, builds an FFmpeg filter graph, or calls a provider
(requirement #2). Real adapters for the wired slice (VOICE_RENDER,
VISUAL_RENDER, TIMELINE, VIDEO_RENDER, CAPTION_BUILD, SUBTITLE_EXPORT,
MEDIA_QC) live in app/orchestration/adapters.py; `UnwiredNodeAdapter`
here covers every node this phase does NOT wire (the twelve LLM-based
creative-planning stages -- see that class's own docstring).

Freshness is intentionally NOT centralized here (requirement #10's own
"do not duplicate dozens of stale rules centrally"): each real adapter's
own `is_fresh` reads whatever foreign-key id fields its OWN artifact
model already declares (e.g. TimelineManifest.assembly_plan_id) and
compares them against the CURRENT upstream artifacts, fetched via the
exact same `get_artifact` calls the underlying renderer's own
`_verify_*_is_fresh` methods already use internally -- a thin, honest
reuse of information the artifact already carries, never a rebuilt
central rule engine.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Protocol
from uuid import UUID

from sqlalchemy import Engine

from app.orchestration.errors import NodeNotWiredError
from app.storage.artifacts import get_artifact
from app.storage.errors import ArtifactNotFoundError, ArtifactValidationError


@dataclass
class ExecutionContext:
    """Everything a real adapter might need to call its own underlying
    renderer/builder -- deliberately a flat bag of optional fields rather
    than one contract per adapter, since a single ProductionRunner call
    only ever needs to populate the fields its OWN target's ancestor
    closure will actually touch."""

    db_engine: Engine
    project_id: UUID
    audio_store: Any = None
    visual_store: Any = None
    video_store: Any = None
    subtitle_store: Any = None
    tts_provider: Any = None
    tts_settings: Any = None
    visual_provider: Any = None
    visual_settings: Any = None
    ti_compositor: Any = None
    diagram_renderer: Any = None
    layer_compositor: Any = None
    ti_state_sources: dict = field(default_factory=dict)
    composition_specs: dict = field(default_factory=dict)
    visual_motions: dict = field(default_factory=dict)
    audio_bindings: Any = None
    burn_in_settings: Any = None

    # Phase 34: the twelve upstream creative/planning engines all share
    # the same (db_engine, provider, global_config, llm_settings)
    # constructor shape (research_r0/r1 additionally need a retriever) --
    # see app/production_adapters/upstream.py. A single shared
    # llm_provider/llm_settings/global_config triple is deliberately
    # reused across all twelve rather than per-node fields: orchestration
    # itself never chooses a model/provider (requirement #32), it only
    # carries through whatever the caller already configured.
    llm_provider: Any = None
    llm_settings: Any = None
    global_config: Any = None
    research_retriever: Any = None
    initial_idea_input: Any = None
    """The production's own initial brief (an IdeaEngineInput -- seed/
    domain/discovery_mode/additional_context) -- reused as-is rather than
    inventing a new ProductionInput contract (requirement #7). Only
    consulted the first time IDEA actually needs to execute; a fresh
    project with no idea yet and no initial_idea_input raises
    ProductionInputMissingError rather than guessing a brief."""


@dataclass(frozen=True)
class NodeExecutionResult:
    """What `NodeAdapter.execute()` returns -- an internal data-transfer
    object, never persisted directly (the runner turns it into a
    NodeRunRecord)."""

    artifact_id: UUID
    module_run_id: UUID | None = None


class NodeAdapter(Protocol):
    """Every real and unwired adapter implements this shape. `node_id` is
    a plain class attribute (not a method) so the registry can introspect
    it without instantiation quirks.

    Optional class attribute (Phase 34): `gate_not_ready_reason:
    BlockedReason` -- the reason the runner records when `gate_ok()`
    returns False for THIS node specifically (e.g. FEASIBILITY_NOT_PASSED,
    SCRIPT_VERIFICATION_NOT_PASSED, PACKAGING_RISK_TOO_HIGH). The runner
    reads it via `getattr(adapter, "gate_not_ready_reason",
    BlockedReason.QC_NOT_READY)`, so an adapter that doesn't declare it
    (every Phase 33 adapter) keeps the original QC_NOT_READY default --
    a backward-compatible, minimal protocol extension rather than a
    breaking change to `gate_ok`'s own return type."""

    node_id: str

    def load_current(self, ctx: ExecutionContext) -> object | None:
        """The project's current artifact for this node, or None if it
        does not exist yet. Never raises for a missing artifact."""
        ...

    def is_fresh(self, artifact: object, ctx: ExecutionContext) -> bool:
        """True if `artifact` (already known to exist) is still current
        relative to its own upstream dependencies."""
        ...

    def execute(self, ctx: ExecutionContext) -> NodeExecutionResult:
        """Run the real underlying module. Raises whatever the
        underlying renderer/builder itself raises (a Missing*/Stale*
        error, or the renderer's own RendererStateError) -- the runner
        catches this and records FAILED, never letting it escape
        run_until() for an ordinary module failure."""
        ...

    def gate_ok(self, artifact: object) -> bool:
        """True if this node's OWN artifact permits proceeding past a
        `gate_after` declared on it (e.g. MediaQCReport.
        ready_for_human_review). True for every node with no such
        concept -- most nodes always return True here."""
        ...


class UnwiredNodeAdapter:
    """Covers a node Phase 33 does not execute (the twelve LLM-based
    creative-planning stages: IDEA, FEASIBILITY, RESEARCH_R0,
    RESEARCH_R1, NARRATIVE, PACKAGING_P0, SCRIPT, SCRIPT_VERIFY,
    VOICE_PLAN, VISUAL_PLAN, ASSEMBLY_PLAN, PACKAGING_P1) -- every one of
    their own engine.py modules imports app.llm directly (confirmed
    before writing this phase), which app/orchestration/ itself must
    never import (requirement #30). `execute()` always raises
    NodeNotWiredError -- the orchestrator can detect whether the node's
    artifact already exists (a real project always produces these via
    the existing engine + app/review/service.py review-gate flow
    directly), but can never produce one itself.

    Freshness for an unwired node is deliberately SHALLOW (existence
    only, `is_fresh` always returns True once an artifact is found) --
    verifying these artifacts' own deep upstream-chain freshness would
    mean reimplementing app/review/service.py's own already-established
    rules a second time, which this phase does not do. In practice this
    is safe for Phase 33's own wired slice: every real adapter's own
    renderer independently re-verifies the SAME upstream plan chain
    inside its own `execute()` regardless of what this shallow check
    reported, so a truly stale plan is still caught -- just one node
    later, inside the wired adapter's own Stale*Error, not here."""

    def __init__(self, node_id: str, artifact_type: str, model_class: type):
        self.node_id = node_id
        self.artifact_type = artifact_type
        self.model_class = model_class

    def load_current(self, ctx: ExecutionContext) -> object | None:
        try:
            return get_artifact(ctx.db_engine, ctx.project_id, self.artifact_type, self.model_class)
        except (ArtifactNotFoundError, ArtifactValidationError):
            return None

    def is_fresh(self, artifact: object, ctx: ExecutionContext) -> bool:
        return True

    def execute(self, ctx: ExecutionContext) -> NodeExecutionResult:
        raise NodeNotWiredError(
            f"{self.node_id} has no Phase 33 adapter -- it is an LLM-based creative-planning "
            f"stage. Produce its artifact via the existing engine + app/review/service.py "
            f"review-gate flow directly, then rerun the orchestrator."
        )

    def gate_ok(self, artifact: object) -> bool:
        return True
