"""Video-encoder-layer domain errors (Phase 28; CROSSFADE/motion-lite
errors added Phase 29; music/SFX mix errors added Phase 30).

Deliberately narrow, mirroring app/layer_compositor/errors.py's shape:
structural/shape validation of a VideoEncodeRequest itself is already
exhaustively enforced by app/video_encoder/models.py's own pydantic model
validators (empty segment list, non-positive fps/width/height/duration,
non-.mp4 output extension) -- these classes cover what can still go wrong
once a request is already known-valid: missing/unsupported/mismatched
input files, an unsupported transition, the ffmpeg/ffprobe executables
themselves, and the encoding process/output.
"""

from __future__ import annotations


class VideoEncoderError(Exception):
    """Base class for all video-encoder-layer errors."""


class FFmpegNotFoundError(VideoEncoderError):
    """Raised when no usable ffmpeg executable can be resolved: the
    configured VideoEncodingSettings.ffmpeg_path does not exist, or no
    explicit path was given and 'ffmpeg' is not found on PATH. Never
    downloads or installs a binary automatically."""


class FFprobeNotFoundError(VideoEncoderError):
    """Raised when no usable ffprobe executable can be resolved -- same
    resolution rule as FFmpegNotFoundError, for 'ffprobe'. Used only to
    verify the encoder's own output after ffmpeg exits (see
    VideoEncoder._verify_output_with_ffprobe); input narration duration is
    validated via stdlib `wave` instead, so ffprobe is never needed before
    ffmpeg actually runs."""


class OutputPathError(VideoEncoderError):
    """Raised when the requested output path's parent directory cannot be
    created or is not writable."""


class MissingVisualFileError(VideoEncoderError):
    """Raised when a VideoSegmentInput.image_path does not exist on disk."""


class UnsupportedVisualFormatError(VideoEncoderError):
    """Raised when a segment's image file has an unsupported suffix (only
    .png/.jpg/.jpeg are supported), or its dimensions cannot be read at
    all (corrupt/undecodable image)."""


class VideoDimensionMismatchError(VideoEncoderError):
    """Raised when a segment's image dimensions do not exactly match
    VideoEncodingSettings.width/height -- the output canvas policy
    (Phase 28 requirement #4): the first timeline visual establishes the
    canvas, every later visual must match exactly. Never silently
    resized/upscaled/downscaled in this phase."""


class MissingNarrationFileError(VideoEncoderError):
    """Raised when a VideoNarrationClip.file_path does not exist on disk."""


class UnsupportedNarrationFormatError(VideoEncoderError):
    """Raised when a narration clip's file has an unsupported suffix (only
    .wav is supported in Phase 28, matching Phase 27's own TimelineBuilder
    convention), or its duration cannot be read from its WAV header at
    all (corrupt file)."""


class NarrationDurationOverflowError(VideoEncoderError):
    """Raised when a segment's narration clips' real, re-measured total
    duration exceeds that segment's authored duration_ms. Audio is never
    truncated to fit -- this fails explicitly instead, exactly per Phase
    28's "narration would extend beyond the segment/timeline timing: fail
    explicitly" instruction. The reverse case (real audio shorter than
    duration_ms) is not an error: the remaining region is silence-padded
    by the encoder."""


class UnsupportedVideoTransitionError(VideoEncoderError):
    """Phase 28 raised this for CROSSFADE specifically; Phase 29 executes
    CROSSFADE for real (see InvalidCrossfadeDurationError for its own
    validation failures instead) and this class is retired for that case.
    It is kept, unreachable today, purely as a defensive guard for a
    hypothetical future TimelineTransitionType member this encoder does
    not yet know how to execute -- never a fallback to CUT for an
    explicitly-authored transition it does not recognize."""


class InvalidCrossfadeDurationError(VideoEncoderError):
    """Raised when a CROSSFADE join's crossfade_duration_ms is not
    strictly shorter than BOTH adjacent segments' own duration_ms (Phase
    29 requirement #3). Never silently shortened to fit -- the caller
    must either lower VideoEncodingSettings.crossfade_duration_ms or
    lengthen the segment(s)."""


class UnsupportedMotionTypeError(VideoEncoderError):
    """Raised if a VideoSegmentInput.motion value has no known FFmpeg
    filter mapping. Unreachable today (VisualMotionType is a closed
    5-member enum and every member maps to a filter), kept as a
    defensive guard for a hypothetical future member, exactly mirroring
    UnsupportedVideoTransitionError's own role."""


class MotionSourceTooSmallError(VideoEncoderError):
    """Raised when the output canvas is too small for a motion effect to
    produce a non-degenerate result -- e.g. PAN_LEFT/PAN_RIGHT's required
    pre-scale would add zero whole pixels of travel at the configured
    canvas width. Never silently applied as STATIC instead."""


class FFmpegFilterUnavailableError(VideoEncoderError):
    """Raised when the resolved local `ffmpeg` build does not report a
    filter this request needs (`xfade` for any CROSSFADE join; `zoompan`
    for any SLOW_ZOOM_IN/SLOW_ZOOM_OUT segment; `crop`/`scale` for any
    PAN_LEFT/PAN_RIGHT segment) in its own `ffmpeg -filters` output,
    checked once per encode before the real command is built. Never
    downloads or substitutes a different ffmpeg build automatically."""


class MissingMusicAssetError(VideoEncoderError):
    """Raised when the timeline's cues need a music bed (at least one
    MUSIC_BED_START exists) but AudioAssetBindings.music_bed_path is
    None, or is set but does not exist on disk (Phase 30). Never a silent
    skip -- an authored music cue with no bound asset is a configuration
    error, not "no music requested"."""


class MissingSFXAssetError(VideoEncoderError):
    """Raised when an SFX_TRIGGER cue's id resolves to a real entry in
    AudioAssetBindings.sfx_by_id, but that entry's file does not exist on
    disk. Distinct from UnknownSFXReferenceError, which covers an id with
    no binding entry at all."""


class UnknownSFXReferenceError(VideoEncoderError):
    """Raised when an SFX_TRIGGER cue's own reference/id is blank, or is
    not a key in AudioAssetBindings.sfx_by_id. No fuzzy matching, no
    default/random SFX substitution."""


class InvalidMusicCueSequenceError(VideoEncoderError):
    """Raised by the pure AudioMixPlan music state machine
    (app/video_encoder/audio_mix.py) for any cue-sequence violation:
    MUSIC_BED_START while already active, MUSIC_DUCK/MUSIC_LIFT/
    MUSIC_BED_END while inactive, cues out of nondecreasing timestamp
    order, two music cues sharing one timestamp, or a music region too
    short to accommodate music_gain_ramp_ms's own crossfade. Never
    silently reordered, merged, or clamped."""


class UnsupportedAudioAssetFormatError(VideoEncoderError):
    """Raised when a bound music or SFX file has an unsupported suffix
    (Phase 30 supports .wav/.mp3/.m4a/.aac for both) -- the same
    fail-explicitly policy as UnsupportedVisualFormatError/
    UnsupportedNarrationFormatError."""


class AudioAssetUnreadableError(VideoEncoderError):
    """Raised when a bound music or SFX file exists and has a supported
    suffix, but ffprobe cannot find a real audio stream in it -- checked
    once per distinct bound asset actually used by this request, before
    the real ffmpeg encode ever runs."""


class AudioMixPlanningError(VideoEncoderError):
    """Raised by the pure AudioMixPlan planner for a cue-timing violation
    that is not specifically a music-sequence-state error -- currently:
    a music or SFX cue whose own timestamp_ms falls beyond the
    timeline's own total_duration_ms."""


class VideoEncodingProcessError(VideoEncoderError):
    """Raised when the ffmpeg subprocess exits with a non-zero return
    code. Never retried automatically -- an encoding failure is not a
    transient condition this encoder knows how to recover from."""


class VideoOutputNotProducedError(VideoEncoderError):
    """Raised when ffmpeg exits 0 but no file exists at the requested
    output path."""


class VideoOutputCorruptError(VideoEncoderError):
    """Raised when the produced output file is zero bytes, or ffprobe
    cannot confirm it contains both a video and an audio stream."""
