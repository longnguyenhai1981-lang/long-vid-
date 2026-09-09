"""MediaQCRenderer-specific domain errors.

Local to this package, like every renderer's own errors.py -- mirrors
app/renderers/subtitle/errors.py's own freshness-chain error set (the
same full upstream chain must be independently re-verified here too),
plus errors specific to this package's own optional caption-adjacent
artifacts (CaptionManifest/SubtitleFileAsset/CaptionedVideoAsset are all
OPTIONAL for a given project -- their absence is not an error, only
their STALENESS is, per Phase 32 requirement #17's "reject stale chains,
never invent a special exception").
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


class MissingEncodedVideoAssetArtifactError(Exception):
    """Raised when the project has no valid EncodedVideoAsset artifact --
    VideoRenderer must run before QC can inspect anything."""


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


class StaleEncodedVideoAssetError(Exception):
    """Raised when the current EncodedVideoAsset was encoded from a
    TimelineManifest other than the one currently in force -- rerun
    VideoRenderer first."""


class StaleCaptionManifestError(Exception):
    """Raised when a present CaptionManifest was built from a
    TimelineManifest other than the one currently in force. A MISSING
    CaptionManifest is not an error (captions are optional per project) --
    only a STALE one is."""


class StaleSubtitleFileAssetError(Exception):
    """Raised when a present SubtitleFileAsset was exported from a
    CaptionManifest other than the current one."""


class StaleCaptionedVideoAssetError(Exception):
    """Raised when a present CaptionedVideoAsset references a source
    EncodedVideoAsset, CaptionManifest, or SubtitleFileAsset other than
    the ones currently in force."""
