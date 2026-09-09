"""Typed contracts for Phase 32's deterministic media QC layer.

QCCheckResult/MediaQCReport/MediaQCSettings are plain, dependency-free
data contracts -- no subprocess, no filesystem I/O. app/media_qc/
inspector.py is the only module that produces a real MediaQCReport;
app/media_qc/rules.py produces the individual QCCheckResults it is built
from.

QC never alters media, never fixes a defect, never calls an LLM, and
never performs semantic/aesthetic judgment -- every check here is a
narrow, deterministic technical measurement (file existence, codec/
pixel-format/fps/canvas/duration match, gross black/frozen-frame
detection, gross audio silence/clipping detection, subtitle text/timing
identity) with an explicit PASS/WARN/FAIL severity policy (requirement
#3): FAIL means a technical defect likely makes the deliverable invalid/
unusable; WARN means the media remains playable but deserves human
review; PASS means the check was satisfied. There is no numeric
"quality score" anywhere in this model.
"""

from __future__ import annotations

from datetime import datetime
from enum import Enum
from pathlib import Path
from uuid import UUID, uuid4

from pydantic import Field, model_validator

from app.models.caption import CaptionManifest
from app.models.common import MotilyModel, non_blank

_DEFAULT_BLACK_LUMINANCE_THRESHOLD = 16.0
_DEFAULT_BLACK_FRAME_FRACTION_THRESHOLD = 1.0
_DEFAULT_FROZEN_FRAME_DIFFERENCE_THRESHOLD = 2.0
_DEFAULT_SILENCE_THRESHOLD_DB = -60.0
_DEFAULT_CLIPPING_THRESHOLD_DB = 0.0


class QCStatus(str, Enum):
    """Severity, ordered FAIL > WARN > PASS (requirement #3) -- never a
    subjective quality score."""

    PASS = "PASS"
    WARN = "WARN"
    FAIL = "FAIL"


class QCCheckResult(MotilyModel):
    """One independent, deterministic technical check's own outcome.
    `measured_value`/`expected_value` are always plain strings (never a
    numeric union type) -- the caller formats whatever value it measured/
    expected, keeping this model uniform across every check kind (a
    codec name, a resolution, a duration in ms, a boolean, ...)."""

    check_id: str
    status: QCStatus
    message: str
    measured_value: str | None = None
    expected_value: str | None = None
    details: str | None = None

    @model_validator(mode="after")
    def _check_invariants(self) -> "QCCheckResult":
        non_blank(self.check_id, "check_id")
        non_blank(self.message, "message")
        return self


class MediaQCSettings(MotilyModel):
    """Deliberately few knobs (requirement #21) -- every threshold here
    was chosen and empirically validated against real ffmpeg/Pillow
    measurements during this phase's own development (see
    docs/TECHNICAL_SPEC_v0.1.md's Phase 32 section)."""

    duration_tolerance_ms: int = 100
    """Requirement #5's own preferred policy: outside tolerance = FAIL,
    no intermediate WARN tier -- a duration mismatch beyond a small
    tolerance means the deliverable's own timing contract was violated,
    which is never merely "deserves review"."""
    fps_tolerance: float = 0.01
    black_luminance_threshold: float = _DEFAULT_BLACK_LUMINANCE_THRESHOLD
    """Mean 0-255 grayscale luminance below which one sampled frame
    counts as "black". Empirically: a genuinely black frame measures
    0.0; this project's own real still-frame content measures in the
    70s-80s range even for a fairly dark background color."""
    black_frame_fraction_threshold: float = _DEFAULT_BLACK_FRAME_FRACTION_THRESHOLD
    """Fraction (0.0-1.0) of sampled frames that must be black to FAIL
    QC_VIDEO_BLACK -- the default of 1.0 means ALL sampled frames must be
    black (requirement #10: "individual intentionally-dark frame must
    not fail entire video")."""
    frozen_frame_difference_threshold: float = _DEFAULT_FROZEN_FRAME_DIFFERENCE_THRESHOLD
    """Mean absolute grayscale pixel difference below which two sampled
    frames count as "identical" for QC_VIDEO_FROZEN -- empirically,
    two byte-identical frames measure 0.0; any real color/content change
    measures far higher."""
    sample_frame_count: int = 5
    """Deterministic positions are evenly spaced between 10% and 90% of
    total_duration_ms (matching this phase's own preferred 10/30/50/70/90
    example exactly when sample_frame_count=5) -- never a full per-frame
    scan."""
    silence_threshold_db: float = _DEFAULT_SILENCE_THRESHOLD_DB
    """ffmpeg `volumedetect`'s own mean_volume, in dBFS, below which the
    entire program counts as effectively silent (QC_AUDIO_SILENCE)."""
    clipping_threshold_db: float = _DEFAULT_CLIPPING_THRESHOLD_DB
    """ffmpeg `volumedetect`'s own max_volume, in dBFS: at or above this
    (i.e. >= 0 dBFS, a technically impossible/clipped peak) is
    QC_AUDIO_CLIPPING FAIL."""

    @model_validator(mode="after")
    def _check_invariants(self) -> "MediaQCSettings":
        if self.duration_tolerance_ms < 0:
            raise ValueError(f"duration_tolerance_ms must be >= 0; got {self.duration_tolerance_ms}")
        if self.fps_tolerance <= 0:
            raise ValueError(f"fps_tolerance must be > 0; got {self.fps_tolerance}")
        if self.black_luminance_threshold < 0 or self.black_luminance_threshold > 255:
            raise ValueError(
                f"black_luminance_threshold must be within [0, 255]; got "
                f"{self.black_luminance_threshold}"
            )
        if not (0.0 <= self.black_frame_fraction_threshold <= 1.0):
            raise ValueError(
                f"black_frame_fraction_threshold must be within [0.0, 1.0]; got "
                f"{self.black_frame_fraction_threshold}"
            )
        if self.frozen_frame_difference_threshold < 0:
            raise ValueError(
                f"frozen_frame_difference_threshold must be >= 0; got "
                f"{self.frozen_frame_difference_threshold}"
            )
        if self.sample_frame_count < 1:
            raise ValueError(f"sample_frame_count must be >= 1; got {self.sample_frame_count}")
        return self


def _derive_overall_status(checks: list[QCCheckResult]) -> QCStatus:
    """FAIL dominates WARN dominates PASS (requirement #2) -- the only
    place this dominance rule is implemented."""
    if any(check.status is QCStatus.FAIL for check in checks):
        return QCStatus.FAIL
    if any(check.status is QCStatus.WARN for check in checks):
        return QCStatus.WARN
    return QCStatus.PASS


class MediaQCReport(MotilyModel):
    """One inspection's complete, deterministic result. `overall_status`
    is never independently settable in a way that could disagree with
    `checks` -- its own validator recomputes the dominance rule and
    rejects any mismatch (requirement #2's own "do not allow caller to
    arbitrarily set overall_status inconsistent with checks"); use
    `build()` below to avoid ever needing to compute it by hand."""

    id: UUID = Field(default_factory=uuid4)
    project_id: UUID
    source_video_asset_id: UUID
    timeline_manifest_id: UUID
    caption_manifest_id: UUID | None = None
    subtitle_file_asset_id: UUID | None = None
    overall_status: QCStatus
    checks: list[QCCheckResult] = Field(min_length=1)
    created_at: datetime

    @model_validator(mode="after")
    def _check_invariants(self) -> "MediaQCReport":
        if self.created_at.tzinfo is None:
            raise ValueError("created_at must be timezone-aware")
        derived = _derive_overall_status(self.checks)
        if self.overall_status is not derived:
            raise ValueError(
                f"overall_status ({self.overall_status.value}) does not match the status "
                f"derived from checks ({derived.value}) -- FAIL dominates WARN dominates "
                f"PASS; use MediaQCReport.build() to derive this automatically"
            )
        return self

    @property
    def ready_for_human_review(self) -> bool:
        """Requirement #26's own policy: PASS/WARN -> True, FAIL ->
        False. Always derived from overall_status, never independently
        stored or settable -- this is a gate for human review only,
        never an auto-publish signal. A plain (non-serialized) property,
        deliberately NOT a pydantic `computed_field`: a computed field
        would round-trip into `model_dump_json()`'s own output, which
        `get_artifact`'s `model_validate_json()` re-hydration would then
        reject outright under this model's own `extra="forbid"` policy
        (confirmed empirically) -- callers that need this value in a
        serialized form (e.g. the Phase 32 evaluation script's own JSON
        export) add it explicitly to their own output dict instead."""
        return self.overall_status is not QCStatus.FAIL

    @classmethod
    def build(
        cls,
        *,
        project_id: UUID,
        source_video_asset_id: UUID,
        timeline_manifest_id: UUID,
        caption_manifest_id: UUID | None = None,
        subtitle_file_asset_id: UUID | None = None,
        checks: list[QCCheckResult],
        created_at: datetime,
    ) -> "MediaQCReport":
        """Preferred construction path -- derives overall_status from
        `checks` automatically rather than requiring the caller to
        compute the dominance rule by hand."""
        return cls(
            project_id=project_id,
            source_video_asset_id=source_video_asset_id,
            timeline_manifest_id=timeline_manifest_id,
            caption_manifest_id=caption_manifest_id,
            subtitle_file_asset_id=subtitle_file_asset_id,
            overall_status=_derive_overall_status(checks),
            checks=checks,
            created_at=created_at,
        )


class MediaQCRequest(MotilyModel):
    """One inspection job -- every path/expected value here must already
    be resolved by the caller (mirrors VideoEncodeRequest's own "every
    file referenced must already exist" boundary). MediaQCInspector never
    queries a database or a provider; app/renderers/media_qc/ is the only
    layer that resolves artifacts into this contract."""

    project_id: UUID
    source_video_asset_id: UUID
    timeline_manifest_id: UUID
    video_path: Path
    expected_video_codec: str
    expected_audio_codec: str
    expected_pixel_format: str
    expected_fps: float
    expected_width: int
    expected_height: int
    expected_duration_ms: int
    subtitle_path: Path | None = None
    caption_manifest: CaptionManifest | None = None
    subtitle_file_asset_id: UUID | None = None

    @model_validator(mode="after")
    def _check_invariants(self) -> "MediaQCRequest":
        non_blank(str(self.video_path), "video_path")
        non_blank(self.expected_video_codec, "expected_video_codec")
        non_blank(self.expected_audio_codec, "expected_audio_codec")
        non_blank(self.expected_pixel_format, "expected_pixel_format")
        if self.expected_fps <= 0:
            raise ValueError(f"expected_fps must be > 0; got {self.expected_fps}")
        if self.expected_width <= 0 or self.expected_height <= 0:
            raise ValueError("expected_width/expected_height must be > 0")
        if self.expected_duration_ms <= 0:
            raise ValueError("expected_duration_ms must be > 0")
        if (self.subtitle_path is None) != (self.caption_manifest is None):
            raise ValueError(
                "subtitle_path and caption_manifest must be provided together, or not at "
                "all -- subtitle checks always compare the SRT against its own manifest"
            )
        return self
