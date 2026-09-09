"""Phase 34's complete, one-graph production adapter set: the twelve
upstream adapters (app/production_adapters/upstream.py) combined with
the seven Phase 33 downstream adapters (app/orchestration/adapters.py,
imported unchanged -- their business logic is not rewritten, requirement
#29) into a single 19-node ProductionGraph with the full, real approval
gate placement.

`app/orchestration/adapters.py`'s own `build_default_graph()`/
`build_default_adapters()` are left completely untouched (Phase 33's own
evaluation script and tests keep working unmodified, requirement #49) --
this module is the NEW, additional entry point for the full pipeline.
"""

from __future__ import annotations

from app.orchestration.adapters import (
    CaptionBuildAdapter,
    MediaQCAdapter,
    SubtitleExportAdapter,
    TimelineAdapter,
    VideoRenderAdapter,
    VisualRenderAdapter,
    VoiceRenderAdapter,
    _NODE_DEPENDENCIES,
)
from app.orchestration.graph import ProductionGraph, ProductionNodeDefinition
from app.orchestration.models import ApprovalGateType
from app.production_adapters.upstream import (
    AssemblyPlanAdapter,
    FeasibilityAdapter,
    IdeaAdapter,
    NarrativeAdapter,
    PackagingP0Adapter,
    PackagingP1Adapter,
    ResearchR0Adapter,
    ResearchR1Adapter,
    ScriptAdapter,
    ScriptVerifyAdapter,
    VisualPlanAdapter,
    VoicePlanAdapter,
)

# Every real, meaningful human gate this project actually has (requirement
# #13) -- precisely the six app/review/service.py decision points (five
# legacy, collapsed to five gate types since SCRIPT_VERIFICATION and
# SCRIPT_REVIEW share one SCRIPT_APPROVAL gate on the SCRIPT_VERIFY node)
# plus the one genuinely new FINAL_MEDIA_APPROVAL. No gate is placed after
# RESEARCH_R0, RESEARCH_R1, SCRIPT, VOICE_PLAN, VISUAL_PLAN, ASSEMBLY_PLAN,
# or PACKAGING_P1 -- none of those has a human review step in the real
# codebase (requirement #13's "do NOT place human approval after every
# engine").
_GATE_AFTER: dict[str, ApprovalGateType] = {
    "IDEA": ApprovalGateType.IDEA_APPROVAL,
    "FEASIBILITY": ApprovalGateType.RESEARCH_APPROVAL,
    "NARRATIVE": ApprovalGateType.NARRATIVE_APPROVAL,
    "PACKAGING_P0": ApprovalGateType.PACKAGING_P0_APPROVAL,
    "SCRIPT_VERIFY": ApprovalGateType.SCRIPT_APPROVAL,
    "MEDIA_QC": ApprovalGateType.FINAL_MEDIA_APPROVAL,
}


def build_full_graph() -> ProductionGraph:
    """The same 19-node topology app/orchestration/adapters.py has
    registered since Phase 33 (dependencies never changed -- only which
    nodes have real adapters and which nodes carry a gate_after changed
    this phase), reused directly rather than re-declared, to guarantee
    the two graphs can never silently drift apart."""
    definitions = [
        ProductionNodeDefinition(
            node_id=node_id, dependencies=dependencies, gate_after=_GATE_AFTER.get(node_id)
        )
        for node_id, dependencies in _NODE_DEPENDENCIES.items()
    ]
    return ProductionGraph(definitions)


def build_full_adapters() -> dict[str, object]:
    """All 19 nodes with real, executable adapters -- no UnwiredNodeAdapter
    remains (requirement #30)."""
    return {
        # Upstream (Phase 34, new)
        "IDEA": IdeaAdapter(),
        "RESEARCH_R0": ResearchR0Adapter(),
        "FEASIBILITY": FeasibilityAdapter(),
        "RESEARCH_R1": ResearchR1Adapter(),
        "NARRATIVE": NarrativeAdapter(),
        "PACKAGING_P0": PackagingP0Adapter(),
        "SCRIPT": ScriptAdapter(),
        "SCRIPT_VERIFY": ScriptVerifyAdapter(),
        "VOICE_PLAN": VoicePlanAdapter(),
        "VISUAL_PLAN": VisualPlanAdapter(),
        "ASSEMBLY_PLAN": AssemblyPlanAdapter(),
        "PACKAGING_P1": PackagingP1Adapter(),
        # Downstream (Phase 33, re-exported unchanged)
        "VOICE_RENDER": VoiceRenderAdapter(),
        "VISUAL_RENDER": VisualRenderAdapter(),
        "TIMELINE": TimelineAdapter(),
        "VIDEO_RENDER": VideoRenderAdapter(),
        "CAPTION_BUILD": CaptionBuildAdapter(),
        "SUBTITLE_EXPORT": SubtitleExportAdapter(),
        "MEDIA_QC": MediaQCAdapter(),
    }
