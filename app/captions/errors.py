"""Domain errors for the standalone app/captions/ package (Phase 31).

Two categories, both package-local, no DB/artifact concerns (those live
in app/renderers/caption/errors.py and app/renderers/subtitle/errors.py,
mirroring how app/video_encoder/errors.py stays free of the same
concerns app/renderers/video/errors.py owns):

- pure caption-content/timing errors (app/captions/builder.py)
- burn-in execution errors (app/captions/burn_in.py)
"""

from __future__ import annotations


class CaptionError(Exception):
    """Base class for all app/captions/ errors."""


class UnknownCaptionChunkReferenceError(CaptionError):
    """Raised when a TimelineAudioRef.chunk_id has no matching VoiceChunk
    in the current VoicePlan. Should be unreachable in practice (the
    TimelineManifest that produced this ref was itself built from the
    same VoicePlan by TimelineBuilder), kept as a defensive guard --
    never a fuzzy match."""


class MissingCaptionSourceTextError(CaptionError):
    """Raised when a VoiceChunk's own line_ids reference a script_line_id
    with no matching ScriptLine in the current ScriptPlan. Caption text
    is never inferred or left blank when the authored source is
    missing."""


class CaptionTimingOverflowError(CaptionError):
    """Raised when a segment's own narration refs' cumulative duration
    does not exactly equal the segment's own duration_ms. Should be
    unreachable (TimelineBuilder itself computed segment timing as this
    exact sum), kept as a defensive guard against a caption-layer
    construction bug."""


class InvalidSubtitlePathError(CaptionError):
    """Raised by the burn-in command builder when a subtitle file path
    cannot be safely expressed inside an ffmpeg filtergraph expression --
    confirmed empirically that this local ffmpeg/libass build cannot
    reliably preserve a literal single-quote character within a
    `subtitles` filter's own filename argument (it silently drops the
    quote and, depending on position, corrupts the rest of the filter
    string), so a path containing one is rejected explicitly here rather
    than risking a silently broken or misparsed filter graph."""


class SubtitleFilterUnavailableError(CaptionError):
    """Raised when the resolved local ffmpeg build does not report the
    `subtitles` filter this request needs, checked via `ffmpeg -filters`
    before the real command is built. Never downloads or substitutes a
    different ffmpeg build."""


class CaptionBurnInProcessError(CaptionError):
    """Raised when the ffmpeg subprocess used for caption burn-in exits
    with a non-zero return code."""


class CaptionedVideoOutputError(CaptionError):
    """Raised when ffmpeg exits 0 but no (or a zero-byte, or
    ffprobe-unverifiable) output file resulted from caption burn-in."""


class InputVideoNotFoundError(CaptionError):
    """Raised when CaptionBurnInRequest.input_video_path does not exist
    on disk."""


class SubtitleFileNotFoundError(CaptionError):
    """Raised when CaptionBurnInRequest.subtitle_path does not exist on
    disk."""


class FFmpegNotFoundError(CaptionError):
    """Raised when no usable ffmpeg executable can be resolved for
    caption burn-in -- mirrors app/video_encoder/errors.py's own class of
    the same name. Never downloads or installs a binary automatically."""


class FFprobeNotFoundError(CaptionError):
    """Raised when no usable ffprobe executable can be resolved for
    caption burn-in's own post-encode verification."""
