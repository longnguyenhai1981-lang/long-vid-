"""Video-Renderer-specific domain errors.

Local to this package, like every renderer's own errors.py -- not shared
with app/renderers/timeline/errors.py or app/video_encoder/errors.py,
even though this package's freshness checks mirror the shape of
app/renderers/timeline/errors.py's own Stale*Error classes exactly (one
extra upstream link: the TimelineManifest itself, plus the two render
manifests it was built from).
"""

from __future__ import annotations


class RendererStateError(Exception):
    """Raised when the renderer is run against a project in the wrong state."""


class MissingScriptPlanArtifactError(Exception):
    """Raised when the renderer cannot load a valid ScriptPlan for the project."""


class MissingVoicePlanArtifactError(Exception):
    """Raised when the renderer cannot load a valid VoicePlan artifact."""


class MissingVisualPlanArtifactError(Exception):
    """Raised when the renderer cannot load a valid VisualPlan artifact."""


class MissingAssemblyPlanArtifactError(Exception):
    """Raised when the renderer cannot load a valid AssemblyPlan artifact."""


class MissingTimelineManifestArtifactError(Exception):
    """Raised when the renderer cannot load a valid TimelineManifest
    artifact -- TimelineBuilder must run before video encoding."""


class MissingVoiceRenderManifestArtifactError(Exception):
    """Raised when the renderer cannot load the VoiceRenderManifest a
    TimelineManifest was built from."""


class MissingVisualRenderManifestArtifactError(Exception):
    """Raised when the renderer cannot load the VisualRenderManifest a
    TimelineManifest was built from."""


class StaleVoicePlanError(Exception):
    """Raised when the current VoicePlan was generated for a different ScriptPlan."""


class StaleVisualPlanError(Exception):
    """Raised when the current VisualPlan was generated for a different
    ScriptPlan/VoicePlan."""


class StaleAssemblyPlanError(Exception):
    """Raised when the current AssemblyPlan was generated for a different
    ScriptPlan/VoicePlan/VisualPlan."""


class StaleTimelineManifestError(Exception):
    """Raised when the current TimelineManifest does not match the current
    ScriptPlan/VoicePlan/VisualPlan/AssemblyPlan chain, OR when the
    currently-stored VoiceRenderManifest/VisualRenderManifest is not the
    exact one (by id) the TimelineManifest recorded building from --
    meaning voice or visual assets were re-rendered after the timeline was
    built, without the timeline itself being rebuilt. TimelineBuilder must
    run again before encoding."""


class UnrenderedVisualSegmentError(Exception):
    """Raised when a TimelineSegment's visual reference is a
    VisualRenderRequirement (never an asset file -- e.g. a standalone
    canonical Tí reference, an ASSET_REUSE reuse_key, or an
    EXTERNAL_REQUIRED placeholder) rather than a rendered file. Phase 28
    can only encode a real image; it never renders one itself."""
