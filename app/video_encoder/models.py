"""Typed contracts for the Phase 28/29/30 deterministic local video
encoder.

VideoEncodingSettings/VideoNarrationClip/VideoSegmentInput/
VideoEncodeRequest/VideoEncodeResult are plain, dependency-free data
contracts -- no subprocess, no Pillow, no filesystem I/O beyond what
pydantic's own Path type implies (none of these open or read a file).
app/video_encoder/encoder.py is the only module that acts on them.

Every segment here carries a concrete, already-resolved image_path/
narration file_path -- this package never queries a database, a
VisualRenderManifest/VoiceRenderManifest, a TimelineManifest, or a
provider to figure out what file a segment means. Turning a
TimelineSegment into a VideoSegmentInput is the caller's job (see
app/renderers/video/renderer.py).

Phase 29 adds VideoEncodingSettings.crossfade_duration_ms (one global
default for every CROSSFADE join in a request -- no per-segment override;
see app/video_encoder/encoder.py's module docstring for why) and
VideoSegmentInput.motion (VisualMotionType, default STATIC, carried
through unchanged from TimelineSegment.visual_motion).

Phase 30 adds music/SFX mix execution: AudioAssetBindings (the explicit,
never-inferred mapping from a music/SFX cue to a concrete local file),
five VideoEncodingSettings gain/ramp defaults, VideoEncodeRequest.cues
(the TimelineManifest cues this request should execute as audio, default
empty -- an empty list means exactly Phase 29's audio behavior,
regardless of how the caller resolved bindings), and
VideoEncodeResult.has_music/sfx_event_count/music_cue_count. See
app/video_encoder/audio_mix.py for how cues+bindings become a concrete
mix plan, and app/video_encoder/encoder.py for how that plan becomes a
real ffmpeg filter graph.
"""

from __future__ import annotations

from pathlib import Path

from pydantic import Field, model_validator

from app.models.common import MotilyModel, non_blank
from app.models.timeline import TimelineCue, TimelineTransitionType, VisualMotionType

_SUPPORTED_OUTPUT_SUFFIXES = frozenset({".mp4"})
_DEFAULT_CROSSFADE_DURATION_MS = 300
_SUPPORTED_AUDIO_ASSET_SUFFIXES = frozenset({".wav", ".mp3", ".m4a", ".aac"})
_GAIN_DB_FLOOR = -60.0
_GAIN_DB_CEILING = 0.0


class VideoEncodingSettings(MotilyModel):
    """Encoder-wide, request-independent configuration. width/height
    together encode Phase 28's canvas policy (requirement #4) -- the
    caller (app/renderers/video/renderer.py) resolves them from the
    timeline's first visual before constructing a request; this model
    just carries the already-decided values through."""

    fps: int = 30
    width: int
    height: int
    video_codec: str = "libx264"
    audio_codec: str = "aac"
    pixel_format: str = "yuv420p"
    crossfade_duration_ms: int = _DEFAULT_CROSSFADE_DURATION_MS
    """Phase 29: one global default duration for EVERY CROSSFADE join in a
    request -- deliberately not a per-segment override. TimelineManifest
    has no clean per-segment place to author a duration without a broader
    redesign (AssemblySegment/TimelineSegment carry no numeric transition-
    duration field at all today), so this stays a single encoder-wide
    setting until a future phase demonstrates a real need for per-segment
    control."""
    ffmpeg_path: Path | None = None
    """None resolves 'ffmpeg' from PATH at encode time."""
    ffprobe_path: Path | None = None
    """None resolves 'ffprobe' from PATH at encode time."""
    music_bed_gain_db: float = -24.0
    """Phase 30: fixed gain applied while MUSIC_BED_START's own state is
    active (before any DUCK/LIFT). Never dynamically normalized, never
    inspects narration loudness -- the cue itself is what changes gain."""
    music_duck_gain_db: float = -32.0
    """Phase 30: fixed gain applied while MUSIC_DUCK's own state is
    active."""
    music_lift_gain_db: float = -20.0
    """Phase 30: fixed gain applied while MUSIC_LIFT's own state is
    active."""
    sfx_gain_db: float = -10.0
    """Phase 30: one global default gain for every SFX_TRIGGER playback --
    no per-cue override today (TimelineCue carries no gain field to read
    one from)."""
    music_gain_ramp_ms: int = 80
    """Phase 30: a deterministic linear crossfade duration (via ffmpeg's
    `acrossfade`) between every adjacent pair of music gain regions --
    avoids an audible click at BED_START/DUCK/LIFT/BED_END boundaries.
    Every produced music region must be at least this long (see
    app/video_encoder/audio_mix.py's InvalidMusicCueSequenceError)."""

    @model_validator(mode="after")
    def _check_invariants(self) -> "VideoEncodingSettings":
        if self.fps <= 0:
            raise ValueError(f"fps must be > 0; got {self.fps}")
        if self.width <= 0 or self.height <= 0:
            raise ValueError(f"width/height must be > 0; got {self.width}x{self.height}")
        if self.crossfade_duration_ms <= 0:
            raise ValueError(f"crossfade_duration_ms must be > 0; got {self.crossfade_duration_ms}")
        non_blank(self.video_codec, "video_codec")
        non_blank(self.audio_codec, "audio_codec")
        non_blank(self.pixel_format, "pixel_format")
        for field_name in ("music_bed_gain_db", "music_duck_gain_db", "music_lift_gain_db", "sfx_gain_db"):
            value = getattr(self, field_name)
            if not (_GAIN_DB_FLOOR <= value <= _GAIN_DB_CEILING):
                raise ValueError(
                    f"{field_name} must be within [{_GAIN_DB_FLOOR}, {_GAIN_DB_CEILING}] dB; got {value}"
                )
        if self.music_gain_ramp_ms <= 0:
            raise ValueError(f"music_gain_ramp_ms must be > 0; got {self.music_gain_ramp_ms}")
        return self


class AudioAssetBindings(MotilyModel):
    """The explicit, execution-side mapping from a TimelineManifest cue to
    a concrete local audio file (Phase 30) -- VideoEncoder never infers,
    searches, or downloads one. Mirrors VisualRendererInput.
    ti_state_sources/composition_specs's own explicit-binding shape:
    absent/empty means "this asset is not available," never "guess."""

    music_bed_path: Path | None = None
    """The single background-music-bed source file, looped/trimmed to
    exactly cover every MUSIC_BED_START..MUSIC_BED_END span. None if the
    caller has no music asset to bind -- if the timeline's cues still
    need one, encoding fails explicitly (MissingMusicAssetError) rather
    than silently playing no music."""
    sfx_by_id: dict[str, Path] = Field(default_factory=dict)
    """Keyed by the exact TimelineCue.reference an SFX_TRIGGER cue
    carries (Phase 27's own sfx_opportunity string) -- an id with no
    entry here is UnknownSFXReferenceError, never a fuzzy match or a
    default/random SFX."""


class VideoNarrationClip(MotilyModel):
    """One already-rendered narration audio file, played back-to-back with
    any siblings within its owning VideoSegmentInput. Its real duration is
    re-measured from the file itself at encode time (see
    app/video_encoder/encoder.py) -- this contract deliberately does not
    carry a caller-declared duration_ms to trust instead."""

    file_path: Path

    @model_validator(mode="after")
    def _check_invariants(self) -> "VideoNarrationClip":
        non_blank(str(self.file_path), "file_path")
        return self


class VideoSegmentInput(MotilyModel):
    """One segment's fully-resolved encoding inputs -- the video-encoder-
    layer counterpart to TimelineSegment, after its visual/narration
    references have been resolved to concrete files by the caller."""

    segment_id: str
    image_path: Path
    duration_ms: int
    narration_clips: list[VideoNarrationClip] = Field(min_length=1)
    transition_in: TimelineTransitionType
    transition_out: TimelineTransitionType
    motion: VisualMotionType = VisualMotionType.STATIC
    """Phase 29: carried through unchanged from TimelineSegment.visual_motion
    -- see app/video_encoder/encoder.py for the exact FFmpeg filter each
    member maps to. STATIC applies no motion filter at all."""

    @model_validator(mode="after")
    def _check_invariants(self) -> "VideoSegmentInput":
        non_blank(self.segment_id, "segment_id")
        non_blank(str(self.image_path), "image_path")
        if self.duration_ms <= 0:
            raise ValueError(f"duration_ms must be > 0; got {self.duration_ms}")
        return self


class VideoEncodeRequest(MotilyModel):
    """One encoding job: an ordered, non-empty list of segments (played in
    list order, exactly matching timeline order) and where to write the
    result. Every file referenced anywhere in this request must already
    exist -- this contract never triggers rendering of any kind."""

    segments: list[VideoSegmentInput] = Field(min_length=1)
    settings: VideoEncodingSettings
    output_path: Path
    cues: list[TimelineCue] = Field(default_factory=list)
    """Phase 30: the TimelineManifest cues this request should execute as
    a real audio mix. Empty (the default) means exactly Phase 29's own
    audio behavior -- narration only, no music/SFX filter graph at all --
    regardless of what audio_bindings carries. Only MUSIC_*/SFX_TRIGGER
    cue types are ever consulted; CUT/STATIC_HOLD are inert here (visual-
    only, per this phase's own requirement #18)."""
    audio_bindings: AudioAssetBindings = Field(default_factory=AudioAssetBindings)
    """Phase 30: the explicit music/SFX asset mapping for this request's
    own `cues`. Meaningless (and never consulted) when `cues` is empty."""

    @model_validator(mode="after")
    def _check_invariants(self) -> "VideoEncodeRequest":
        non_blank(str(self.output_path), "output_path")
        output_suffix = self.output_path.suffix.lower()
        if output_suffix not in _SUPPORTED_OUTPUT_SUFFIXES:
            raise ValueError(
                f"unsupported output_path format {output_suffix!r}; "
                f"expected one of {sorted(_SUPPORTED_OUTPUT_SUFFIXES)}"
            )
        return self

    @property
    def total_duration_ms(self) -> int:
        """The authoritative output duration -- the sum of every segment's
        own duration_ms, exactly matching TimelineManifest.total_duration_ms
        when this request was built from one. Never measured from the
        encoded file after the fact (see this package's determinism
        docstring in encoder.py for why)."""
        return sum(segment.duration_ms for segment in self.segments)


class VideoEncodeResult(MotilyModel):
    """What VideoEncoder actually produced, in enough detail to assert on
    in tests without re-invoking ffprobe. duration_ms is the request's own
    authoritative total_duration_ms, not a value measured from the output
    container -- byte-identical containers across ffmpeg versions/
    environments are not guaranteed, but this business timing value is
    always deterministic."""

    output_path: Path
    width: int
    height: int
    fps: int
    duration_ms: int
    file_size_bytes: int
    video_codec: str
    audio_codec: str
    ffmpeg_command: list[str]
    crossfade_count: int = 0
    """Phase 29: how many adjacent-segment joins were executed as a real
    CROSSFADE (never counting CUT/HOLD joins)."""
    motion_profile_used: list[VisualMotionType] = Field(default_factory=list)
    """Phase 29: the distinct non-STATIC VisualMotionType values actually
    applied across this request's segments, in first-seen order."""
    has_music: bool = False
    """Phase 30: whether a real music bed was mixed in (at least one
    MUSIC_BED_START region existed in the resolved AudioMixPlan)."""
    sfx_event_count: int = 0
    """Phase 30: how many SFX_TRIGGER cues were actually mixed in."""
    music_cue_count: int = 0
    """Phase 30: how many MUSIC_* cues (BED_START/DUCK/LIFT/BED_END,
    combined) this request's own `cues` carried."""

    @model_validator(mode="after")
    def _check_invariants(self) -> "VideoEncodeResult":
        if self.width <= 0 or self.height <= 0:
            raise ValueError("width/height must be > 0")
        if self.fps <= 0:
            raise ValueError("fps must be > 0")
        if self.duration_ms <= 0:
            raise ValueError("duration_ms must be > 0")
        if self.file_size_bytes <= 0:
            raise ValueError("file_size_bytes must be > 0")
        if self.crossfade_count < 0:
            raise ValueError("crossfade_count must be >= 0")
        if self.sfx_event_count < 0:
            raise ValueError("sfx_event_count must be >= 0")
        if self.music_cue_count < 0:
            raise ValueError("music_cue_count must be >= 0")
        return self
