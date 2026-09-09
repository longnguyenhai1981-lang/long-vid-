"""Deterministic local MP4 encoding from an already-resolved
VideoEncodeRequest (Phase 28).

Combines already-existing raster images and WAV narration files into one
MP4 via the system `ffmpeg`/`ffprobe` executables -- no AI generation, no
LLM reasoning, no new media framework dependency. A new, separate
architecture boundary alongside app/layer_compositor/ (Phase 26) and
app/diagram_renderer/ (Phase 25): VideoEncoder depends only on the
VideoEncodeRequest it is given, never on a database, a TimelineManifest,
a manifest, or a provider. As of Phase 28 it IS consumed by
app/renderers/video/renderer.py, through this same public surface.
"""

from __future__ import annotations
