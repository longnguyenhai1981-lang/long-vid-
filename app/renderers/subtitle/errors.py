"""SubtitleRenderer-specific domain errors.

Local to this package, like every renderer's own errors.py -- mirrors
app/renderers/caption/errors.py's own freshness-chain error set (the
same upstream chain must be re-verified here too, since SubtitleRenderer
is a second, independent consumer of TimelineManifest), plus two errors
specific to this package's own extra artifacts: CaptionManifest (its own
direct input) and EncodedVideoAsset (only consulted when burn-in is
requested).
"""

from __future__ import annotations


class RendererStateError(Exception):
    """Raised when the renderer is run against a project in the wrong state."""


class MissingScriptPlanArtifactError(Exception):
    """Raised when the renderer cannot load a valid ScriptPlan for the
    project (missing reference, missing artifact, or an id mismatch)."""


class MissingVoicePlanArtifactError(Exception):
    """Raised when the renderer cannot load a valid VoicePlan artifact."""


class MissingVisualPlanArtifactError(Exception):
    """Raised when the renderer cannot load a valid VisualPlan artifact."""


class MissingAssemblyPlanArtifactError(Exception):
    """Raised when the renderer cannot load a valid AssemblyPlan artifact."""


class MissingVoiceRenderManifestArtifactError(Exception):
    """Raised when the renderer cannot load a valid VoiceRenderManifest
    artifact."""


class MissingVisualRenderManifestArtifactError(Exception):
    """Raised when the renderer cannot load a valid VisualRenderManifest
    artifact."""


class MissingTimelineManifestArtifactError(Exception):
    """Raised when the renderer cannot load a valid TimelineManifest
    artifact."""


class MissingCaptionManifestArtifactError(Exception):
    """Raised when the renderer cannot load a valid CaptionManifest
    artifact -- CaptionBuilder must run before subtitle export/burn-in."""


class MissingEncodedVideoAssetArtifactError(Exception):
    """Raised when burn-in is requested but the project has no valid
    EncodedVideoAsset artifact -- VideoRenderer must run first."""


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
    render-manifest chain other than the one currently in force."""


class StaleCaptionManifestError(Exception):
    """Raised when the current CaptionManifest was built from a
    TimelineManifest other than the one currently in force -- rerun
    CaptionBuilder first."""


class StaleEncodedVideoAssetError(Exception):
    """Raised when burn-in is requested but the current EncodedVideoAsset
    was encoded from a TimelineManifest other than the one currently in
    force -- rerun VideoRenderer first."""
