"""Canonical Tí brand-asset contracts (Phase 21).

Tí is a locked brand character, not an AI-generated one: Phase 20/20.2 proved
that prompt-only generation (Gemini's or Cloudflare FLUX's) cannot hold Tí's
visual identity consistent across independently generated stills. This module
defines the typed contracts for a small, deterministic, human-curated set of
canonical Tí image assets -- one per required expression/state -- that a
future compositor can look up and reuse instead of ever re-generating Tí from
a prompt.

Deliberately scoped to *contracts* only: no filesystem I/O, no SQLite, no
provider/LLM call, and no wiring into VisualRenderer. See app/ti_assets/ for
the storage/retrieval layer built on top of these contracts.
"""

from __future__ import annotations

from datetime import datetime
from enum import Enum
from uuid import UUID, uuid4

from pydantic import Field, model_validator

from app.models.common import MotilyModel, VisualOutputFormat, non_blank


class TiAssetType(str, Enum):
    """The kind of canonical asset one TiAsset row represents.

    STILL is the only member Phase 21 defines -- a single static cutout
    image per state. A pose/presentation-variant type is deliberately NOT
    added here: nothing in the existing visual grammar (VisualBeat/
    VisualTiState/VisualMediaType.TI_STATE) distinguishes a "pose" from a
    "state" today, so inventing one would be speculative. A future phase
    may add e.g. LIMITED_MOTION here without changing this contract's shape.
    """

    STILL = "STILL"


class TiState(str, Enum):
    """Tí's canonical-asset expression/state vocabulary (Phase 21).

    Deliberately matches VoiceState's 8-member vocabulary exactly ("align
    with the existing voice-state system where practical" -- Phase 21's
    human instruction), NOT VisualTiState's 9-member vocabulary (Phase 14;
    CONFUSED/SURPRISED/SMUG instead of SERIOUS/LOW_ENERGY). The two visual
    enums are deliberately not unified in this phase -- see
    docs/TECHNICAL_SPEC_v0.1.md's Phase 21 section for why this mismatch is
    recorded as an open decision rather than resolved here.
    """

    NEUTRAL = "NEUTRAL"
    CURIOUS = "CURIOUS"
    SKEPTICAL = "SKEPTICAL"
    EXCITED = "EXCITED"
    SERIOUS = "SERIOUS"
    DEADPAN = "DEADPAN"
    PANIC = "PANIC"
    LOW_ENERGY = "LOW_ENERGY"


REQUIRED_TI_STATES: frozenset[TiState] = frozenset(TiState)
"""Every TiState is required for a canonical asset set to be complete -- the
MVP vocabulary is deliberately small and closed, so "required" and "all
defined members" are the same set (Phase 21)."""

TI_ASSET_EXTENSION_BY_FORMAT: dict[VisualOutputFormat, str] = {
    VisualOutputFormat.PNG: "png",
    VisualOutputFormat.JPG: "jpg",
}
"""File-extension convention shared with the visual-provider adapters
(app/visual/providers/cloudflare.py writes '.jpg' for VisualOutputFormat.JPG,
the evaluation scripts write '.jpg'/'.png' the same way) -- reused here, not
redefined, so the two subsystems can never silently drift apart."""

TI_ASSET_MIME_BY_FORMAT: dict[VisualOutputFormat, str] = {
    VisualOutputFormat.PNG: "image/png",
    VisualOutputFormat.JPG: "image/jpeg",
}


class TiAsset(MotilyModel):
    """One canonical, human-curated Tí image file for exactly one TiState.

    file bytes are never a field here -- only a relative_path into a
    TiAssetFileStore root (see app/ti_assets/storage.py), mirroring
    RenderedVisualAsset's "metadata only, never bytes" contract exactly.
    """

    id: UUID = Field(default_factory=uuid4)
    asset_set_id: UUID
    state: TiState
    asset_type: TiAssetType = TiAssetType.STILL
    relative_path: str
    output_format: VisualOutputFormat
    mime_type: str
    width: int
    height: int
    transparent_background: bool
    notes: str | None = None

    @model_validator(mode="after")
    def _check_invariants(self) -> "TiAsset":
        non_blank(self.relative_path, "relative_path")
        non_blank(self.mime_type, "mime_type")
        if self.width <= 0:
            raise ValueError("width must be > 0")
        if self.height <= 0:
            raise ValueError("height must be > 0")

        expected_mime = TI_ASSET_MIME_BY_FORMAT[self.output_format]
        if self.mime_type != expected_mime:
            raise ValueError(
                f"mime_type {self.mime_type!r} does not match output_format "
                f"{self.output_format.value} (expected {expected_mime!r})"
            )

        expected_ext = "." + TI_ASSET_EXTENSION_BY_FORMAT[self.output_format]
        if not self.relative_path.lower().endswith(expected_ext):
            raise ValueError(
                f"relative_path {self.relative_path!r} does not end with "
                f"{expected_ext!r} required by output_format {self.output_format.value}"
            )

        if self.transparent_background and self.output_format is not VisualOutputFormat.PNG:
            raise ValueError(
                "transparent_background requires output_format PNG "
                f"(JPG has no alpha channel); got {self.output_format.value}"
            )

        return self


class TiAssetSet(MotilyModel):
    """One versioned, complete collection of canonical Tí assets -- one
    TiAsset per required TiState, no more, no less.

    Completeness and no-duplicate-state are enforced right here, at
    construction time, so an incomplete or conflicting set can never even be
    built in memory, let alone stored or activated. This is the "no fuzzy or
    LLM-based selection" requirement made structural rather than procedural.
    """

    id: UUID = Field(default_factory=uuid4)
    version: str
    is_active: bool = False
    assets: list[TiAsset]
    notes: str | None = None
    created_at: datetime

    @model_validator(mode="after")
    def _check_invariants(self) -> "TiAssetSet":
        non_blank(self.version, "version")
        if self.created_at.tzinfo is None:
            raise ValueError("created_at must be timezone-aware")
        if not self.assets:
            raise ValueError("a TiAssetSet must contain at least one TiAsset")

        for asset in self.assets:
            if asset.asset_set_id != self.id:
                raise ValueError(
                    f"TiAsset {asset.id} has asset_set_id {asset.asset_set_id} "
                    f"but belongs to TiAssetSet {self.id}"
                )

        states = [asset.state for asset in self.assets]
        seen: set[TiState] = set()
        duplicates: set[TiState] = set()
        for state in states:
            if state in seen:
                duplicates.add(state)
            seen.add(state)
        if duplicates:
            raise ValueError(
                "duplicate TiState(s) in TiAssetSet: "
                + ", ".join(sorted(s.value for s in duplicates))
            )

        missing = REQUIRED_TI_STATES - seen
        if missing:
            raise ValueError(
                "TiAssetSet is incomplete, missing TiState(s): "
                + ", ".join(sorted(s.value for s in missing))
            )

        return self
