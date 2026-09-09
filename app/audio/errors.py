"""Audio-layer domain errors.

Two deliberately separate hierarchies, mirroring the LLM layer's
provider/validation split (see app/llm/errors.py) but for audio: a TTS
provider call failing is a different kind of problem from a filesystem
write failing, and callers must be able to tell them apart (e.g. retry a
provider error, but never retry a write error). TTSError and
AudioStorageError share no common base for this reason.
"""

from __future__ import annotations


class TTSError(Exception):
    """Base class for all TTS-provider-layer errors."""


class TTSProviderError(TTSError):
    """The provider invocation itself failed (network/API/provider-side
    error). Retryable -- the renderer may retry a request that fails with
    this error, up to its configured bound."""


class TTSOutputError(TTSError):
    """The provider returned a response that failed to satisfy the
    TTSResponse contract (e.g. malformed/empty audio). Not retried."""


class AudioStorageError(Exception):
    """Base class for all audio-filesystem-layer errors. Deliberately NOT a
    TTSError subclass -- a storage failure is never a provider failure."""


class AudioPathError(AudioStorageError):
    """Raised when a requested relative path is blank, absolute, or escapes
    the audio-output root (e.g. via '..')."""


class AudioWriteError(AudioStorageError):
    """Raised when writing audio bytes to disk fails."""
