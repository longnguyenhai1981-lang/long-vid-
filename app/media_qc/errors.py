"""Domain errors for the standalone app/media_qc/ package (Phase 32).

Two entirely different categories, deliberately kept apart per this
phase's own requirement #24:

- Infrastructure errors (FFmpegNotFoundError/FFprobeNotFoundError/
  QCTempDirError) mean the inspector itself cannot run at all -- these
  ARE allowed to propagate/crash the caller, because no check of any
  kind can be performed without a working ffmpeg/ffprobe or a writable
  temp directory.
- MediaProbeError means one specific artifact could not be read/decoded
  -- this is an ORDINARY, expected outcome for a broken deliverable
  (a missing file, a corrupt MP4, a zero-stream container), and
  app/media_qc/inspector.py always catches it and turns it into a FAIL
  QCCheckResult. It is never allowed to propagate out of
  MediaQCInspector.inspect() -- QC is diagnostic, not fail-fast
  validation (requirement #23).
"""

from __future__ import annotations


class MediaQCError(Exception):
    """Base class for all app/media_qc/ errors."""


class FFmpegNotFoundError(MediaQCError):
    """Infrastructure error: no usable ffmpeg executable can be resolved.
    Never downloads or installs a binary automatically."""


class FFprobeNotFoundError(MediaQCError):
    """Infrastructure error: no usable ffprobe executable can be
    resolved."""


class QCTempDirError(MediaQCError):
    """Infrastructure error: the dedicated temp directory frame sampling
    needs could not be created."""


class MediaProbeError(MediaQCError):
    """NOT an infrastructure error -- raised by FFprobeClient/FrameSampler/
    AudioAnalyzer when a SPECIFIC media file cannot be read/decoded
    (missing, zero-byte, corrupt, unparseable ffprobe output, or no
    frame/audio could be extracted). Always caught by
    app/media_qc/inspector.py and converted into a FAIL QCCheckResult --
    an ordinary, expected outcome for a broken deliverable, never
    propagated to the caller."""
