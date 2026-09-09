"""Timeline Builder input/output contracts.

TimelineManifest (app/models/timeline.py) is the single approved business
output -- these are thin wrappers around it, not a competing schema.
"""

from __future__ import annotations

from uuid import UUID

from pydantic import Field

from app.models.common import MotilyModel
from app.models.timeline import TimelineManifest, VisualMotionType

TIMELINE_MANIFEST_ARTIFACT_TYPE = "timeline_manifest"


class TimelineBuilderInput(MotilyModel):
    project_id: UUID
    visual_motions: dict[str, VisualMotionType] = Field(default_factory=dict)
    """Optional per-segment camera-motion overrides, keyed by
    AssemblySegment.segment_id (Phase 29) -- the explicit-authoring
    surface for TimelineSegment.visual_motion, mirroring
    VisualRendererInput.ti_state_sources/composition_specs's own per-item
    override shape. A segment with no entry here gets STATIC (never
    inferred from content). Deliberately NOT sourced from AssemblyPlan/
    VisualPlan/ScriptPlan -- motion-lite is presentation execution, not
    narrative/scientific content, so it has no business living in those
    contracts."""


class TimelineBuilderResult(MotilyModel):
    manifest: TimelineManifest
    module_run_id: UUID
    segment_count: int
    total_duration_ms: int
    cue_count: int
