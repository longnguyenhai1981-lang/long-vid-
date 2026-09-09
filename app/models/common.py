"""Shared base model and enum definitions for the Motily domain contracts."""

from __future__ import annotations

from enum import Enum

from pydantic import BaseModel, ConfigDict, model_validator


class MotilyModel(BaseModel):
    """Base class for core Motily domain contracts. Unknown fields are rejected."""

    model_config = ConfigDict(extra="forbid")


def non_blank(value: str, field_name: str) -> str:
    """Raise ValueError if value is empty or whitespace-only."""
    if not value or not value.strip():
        raise ValueError(f"{field_name} cannot be blank")
    return value


class PrimaryPayoff(str, Enum):
    EXPLANATION = "EXPLANATION"
    DISCOVERY = "DISCOVERY"
    REVERSAL = "REVERSAL"
    HUMAN_INGENUITY = "HUMAN_INGENUITY"


class GateStatus(str, Enum):
    PASS = "PASS"
    REFRAME = "REFRAME"
    REJECT = "REJECT"


class ResearchRecommendation(str, Enum):
    CONTINUE = "CONTINUE"
    REFRAME = "REFRAME"
    REJECT = "REJECT"


class ClaimStatus(str, Enum):
    SAFE = "SAFE"
    QUALIFIED = "QUALIFIED"
    UNCERTAIN = "UNCERTAIN"
    DISPUTED = "DISPUTED"
    PROHIBITED = "PROHIBITED"


class Confidence(str, Enum):
    HIGH = "HIGH"
    MEDIUM = "MEDIUM"
    LOW = "LOW"


class ScienceDepth(str, Enum):
    LOW = "LOW"
    MEDIUM = "MEDIUM"
    HIGH = "HIGH"


class OpeningType(str, Enum):
    MYSTERY_FIRST = "MYSTERY_FIRST"
    EVENT_FIRST = "EVENT_FIRST"
    QUESTION_FIRST = "QUESTION_FIRST"
    CONTRADICTION_FIRST = "CONTRADICTION_FIRST"


class NarrativeFunction(str, Enum):
    INFORM = "INFORM"
    QUESTION = "QUESTION"
    ANSWER = "ANSWER"
    REVEAL = "REVEAL"
    ESCALATE = "ESCALATE"
    REACT = "REACT"
    JOKE = "JOKE"
    EMPHASIZE = "EMPHASIZE"
    TRANSITION = "TRANSITION"
    CLARIFY = "CLARIFY"


# ScriptLine.function shares the same allowed values as NarrativeFunction.
ScriptLineFunction = NarrativeFunction


class ApprovalStatus(str, Enum):
    APPROVED = "APPROVED"
    REVISE = "REVISE"
    REJECTED = "REJECTED"


class RiskLevel(str, Enum):
    LOW = "LOW"
    MEDIUM = "MEDIUM"
    HIGH = "HIGH"


class ProjectState(str, Enum):
    NEW_PROJECT = "NEW_PROJECT"
    IDEA_DISCOVERY = "IDEA_DISCOVERY"
    IDEA_REVIEW = "IDEA_REVIEW"
    R0_RESEARCH = "R0_RESEARCH"
    FEASIBILITY = "FEASIBILITY"
    R1_RESEARCH = "R1_RESEARCH"
    NARRATIVE = "NARRATIVE"
    NARRATIVE_REVIEW = "NARRATIVE_REVIEW"
    PACKAGING_P0 = "PACKAGING_P0"
    SCRIPT = "SCRIPT"
    SCRIPT_VERIFICATION = "SCRIPT_VERIFICATION"
    SCRIPT_REVIEW = "SCRIPT_REVIEW"
    MVP_COMPLETE = "MVP_COMPLETE"
    ARCHIVED = "ARCHIVED"


class PauseIntent(str, Enum):
    """Authorial/delivery pause intent. Exact timing is out of scope for Phase 1."""

    NONE = "NONE"
    SHORT = "SHORT"
    MEDIUM = "MEDIUM"
    LONG = "LONG"


class ModuleRunStatus(str, Enum):
    RUNNING = "RUNNING"
    SUCCESS = "SUCCESS"
    FAILED = "FAILED"


class GateEvaluation(MotilyModel):
    """A structured pass/reframe/reject judgment with a required reason."""

    status: GateStatus
    reason: str

    @model_validator(mode="after")
    def _check_reason(self) -> "GateEvaluation":
        non_blank(self.reason, "reason")
        return self


class VoiceState(str, Enum):
    """Conceptual vocal-performance state for one VoiceChunk. Deliberately a
    small, fixed set -- no giant emotion taxonomy (Phase 13)."""

    NEUTRAL = "NEUTRAL"
    CURIOUS = "CURIOUS"
    SKEPTICAL = "SKEPTICAL"
    EXCITED = "EXCITED"
    SERIOUS = "SERIOUS"
    DEADPAN = "DEADPAN"
    PANIC = "PANIC"
    LOW_ENERGY = "LOW_ENERGY"


class Pace(str, Enum):
    """Delivery speed as a small typed scale, not exact words-per-minute
    (Phase 13 -- future audio rendering owns exact timing)."""

    SLOW = "SLOW"
    NORMAL = "NORMAL"
    FAST = "FAST"


class Energy(str, Enum):
    """Delivery energy as a small typed scale, not a numeric 1-100 score
    (Phase 13)."""

    LOW = "LOW"
    MEDIUM = "MEDIUM"
    HIGH = "HIGH"


class MusicState(str, Enum):
    """Background-music intent for one VoiceChunk -- not a full composition
    plan (Phase 13)."""

    BED = "BED"
    DUCK = "DUCK"
    LIFT = "LIFT"


class VisualLevel(str, Enum):
    """The three-tier visual grammar (Phase 14) -- deliberately no L4/L5."""

    L1_ESTABLISH = "L1_ESTABLISH"
    L2_ACTION = "L2_ACTION"
    L3_RELATIONSHIP = "L3_RELATIONSHIP"


class VisualFunction(str, Enum):
    """What one VisualBeat is doing for the viewer (Phase 14)."""

    STORY = "STORY"
    EVIDENCE = "EVIDENCE"
    MECHANISM = "MECHANISM"
    METAPHOR = "METAPHOR"
    EMPHASIS = "EMPHASIS"


class VisualMediaType(str, Enum):
    """The fixed media router vocabulary (Phase 14; COMPOSITION added
    Phase 26) -- no other media type.

    COMPOSITION is deliberately a rendering-layer-only concept, not
    something Visual Planning's LLM is prompted to choose (see
    app/engines/visual_plan/prompt.py, unchanged by Phase 26): it
    represents combining other beats' own already-rendered outputs into
    one final frame via VisualLayerCompositor
    (app/layer_compositor/), never new visual content of its own.
    """

    ASSET_REUSE = "ASSET_REUSE"
    TI_STATE = "TI_STATE"
    DIAGRAM = "DIAGRAM"
    GENERATED_STILL = "GENERATED_STILL"
    LIMITED_MOTION = "LIMITED_MOTION"
    EVIDENCE_MEDIA = "EVIDENCE_MEDIA"
    AI_HERO_VIDEO = "AI_HERO_VIDEO"
    COMPOSITION = "COMPOSITION"


class ComplexityClass(str, Enum):
    """Production-effort class for one VisualBeat (Phase 14) -- not a
    numeric cost estimate."""

    C0 = "C0"
    C1 = "C1"
    C2 = "C2"
    C3 = "C3"


class TransitionIntent(str, Enum):
    """A deliberately small transition vocabulary (Phase 15) -- not a
    cinematic-transition taxonomy."""

    CUT = "CUT"
    DISSOLVE = "DISSOLVE"
    MATCH = "MATCH"
    PUSH = "PUSH"
    NONE = "NONE"


class AudioFormat(str, Enum):
    """The fixed audio-container vocabulary (Phase 17) -- no other format
    yet. WAV is the preferred default: a lossless intermediate for later
    assembly."""

    WAV = "WAV"
    MP3 = "MP3"


class VisualTiState(str, Enum):
    """Tí's visual expression/state (Phase 14). Deliberately a separate
    enum from VoiceState -- visual and vocal expression states differ."""

    NEUTRAL = "NEUTRAL"
    CURIOUS = "CURIOUS"
    SKEPTICAL = "SKEPTICAL"
    CONFUSED = "CONFUSED"
    SURPRISED = "SURPRISED"
    PANIC = "PANIC"
    SMUG = "SMUG"
    DEADPAN = "DEADPAN"
    EXCITED = "EXCITED"


class VisualOutputFormat(str, Enum):
    """The fixed still-image container vocabulary (Phase 19) -- no other
    format yet. Still-asset rendering only; video formats are explicitly
    out of scope until a future phase."""

    PNG = "PNG"
    JPG = "JPG"


class VisualRequirementStatus(str, Enum):
    """What happened to one VisualBeat during visual rendering (Phase 19;
    CANONICAL_ASSET_READY added Phase 23).

    RENDERED: a VisualProvider produced an asset for this beat.
    EXTERNAL_REQUIRED: a future runtime (not this renderer) must supply it.
    REUSE_ONLY: an existing asset should be reused; no provider was called.
    CANONICAL_ASSET_READY: a TI_STATE beat resolved to a canonical Tí asset
    (via TiAssetRetriever) but was not composited onto any background --
    no explicit background was supplied for this beat. Never produced by a
    provider call or AI generation; the beat's `reference` points directly
    at the canonical asset file.
    """

    RENDERED = "RENDERED"
    EXTERNAL_REQUIRED = "EXTERNAL_REQUIRED"
    REUSE_ONLY = "REUSE_ONLY"
    CANONICAL_ASSET_READY = "CANONICAL_ASSET_READY"
