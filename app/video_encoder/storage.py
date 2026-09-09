"""Filesystem root for encoded video files.

Mirrors app/visual/storage.py's VisualFileStore/app/audio/storage.py's
AudioFileStore in shape, but carries no `write()` method: unlike a
provider response's in-memory bytes, an encoded MP4 is written directly
to disk by the `ffmpeg` subprocess itself (see app/video_encoder/
encoder.py) -- there is no byte blob this store would ever need to accept
and write on this package's behalf. Only a relative file path is ever
recorded, in an EncodedVideoAsset (app/models/video.py); this class owns
the one place that path is resolved to an absolute filesystem location.
"""

from __future__ import annotations

from pathlib import Path


class VideoFileStore:
    def __init__(self, root: Path | str):
        self._root = Path(root)

    @property
    def root(self) -> Path:
        return self._root
