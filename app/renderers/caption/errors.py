"""CaptionBuilder-specific domain errors.

Local to this package, like every renderer's own errors.py -- not shared
with app/renderers/video/errors.py even though several of these mirror
an equivalent class there exactly (each package independently checks its
own freshness relationship to the same upstream artifacts).
"""

from __future__ import annotations


class RendererStateError(Exception):
    """Raised when the builder is run against a project in the wrong state."""


class MissingScriptPlanArtifactError(Exception):
    """Raised when the builder cannot load a valid ScriptPlan for the
    project (missing reference, missing artifact, or an id mismatch)."""


class MissingVoicePlanArtifactError(Exception):
    """Raised when the builder cannot load a valid VoicePlan artifact."""


class MissingVisualPlanArtifactError(Exception):
    """Raised when the builder cannot load a valid VisualPlan artifact."""


class MissingAssemblyPlanArtifactError(Exception):
    """Raised when the builder cannot load a valid AssemblyPlan artifact."""


class MissingVoiceRenderManifestArtifactError(Exception):
    """Raised when the builder cannot load a valid VoiceRenderManifest
    artifact."""


class MissingVisualRenderManifestArtifactError(Exception):
    """Raised when the builder cannot load a valid VisualRenderManifest
    artifact."""


class MissingTimelineManifestArtifactError(Exception):
    """Raised when the builder cannot load a valid TimelineManifest
    artifact -- TimelineBuilder must run before caption building."""


class StaleVoicePlanError(Exception):
    """Raised when the current VoicePlan was generated for a ScriptPlan
    other than the one the project currently references."""


class StaleVisualPlanError(Exception):
    """Raised when the current VisualPlan was generated for a ScriptPlan or
    VoicePlan other than the ones currently in force."""


class StaleAssemblyPlanError(Exception):
    """Raised when the current AssemblyPlan was generated for a ScriptPlan/
    VoicePlan/VisualPlan combination other than the ones currently in
    force."""


class StaleTimelineManifestError(Exception):
    """Raised when the current TimelineManifest was built for a plan or
    render-manifest chain other than the one currently in force --
    identical check to app/renderers/video/renderer.py's own, since
    captions require exactly the same freshness guarantee video encoding
    does (Phase 31 requirement #9's own "prefer full timeline freshness"
    policy, deliberately not a lighter/special-cased check even though
    caption text/timing does not itself depend on visual data)."""
