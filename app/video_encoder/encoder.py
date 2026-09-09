"""VideoEncoder: deterministic local MP4 encoding from a fully-resolved
VideoEncodeRequest (Phase 28; CROSSFADE execution + motion-lite added
Phase 29).

Sits ABOVE nothing and BELOW app/renderers/video/renderer.py: this
package never queries a database, a TimelineManifest, a manifest, or a
provider -- every file it touches is a concrete path already resolved by
its caller. It never generates visual or audio content of any kind; it
only invokes the system `ffmpeg`/`ffprobe` executables via `subprocess`
with explicit argument lists (never `shell=True`, never a raw interpolated
command string) to combine already-existing raster images and WAV
narration files into one MP4, exactly per TimelineManifest's own integer-
millisecond timing.

FFmpeg dependency strategy: the system/local `ffmpeg`/`ffprobe`
executables only, resolved from `VideoEncodingSettings.ffmpeg_path`/
`ffprobe_path` when given, else from PATH (`shutil.which`). No
`ffmpeg-python`, no `moviepy`, no OpenCV, no other media framework --
Pillow (already a project dependency since Phase 22) is used only to read
a static image's own pixel dimensions for the canvas-dimension check;
Python's stdlib `wave` module (already used by Phase 27's TimelineBuilder
for the identical purpose) is used to re-measure each narration clip's
real duration deterministically, without spawning `ffprobe` for that.
`ffprobe` itself is used exactly once per encode, AFTER `ffmpeg` exits, to
confirm the produced file actually contains a video and an audio stream
(see `_verify_output_with_ffprobe`) -- never before, and never to measure
duration (duration_ms is always `VideoEncodeRequest.total_duration_ms`,
the timeline's own authoritative value; see VideoEncodeResult's
docstring for why a container-measured duration is deliberately never
substituted).

Command construction (`FFmpegCommandBuilder.build`) is a pure function of
its `VideoEncodeRequest` and resolved `ffmpeg` executable path -- no
randomness, no wall-clock, no environment-dependent branching beyond the
executable path itself -- so identical inputs always produce an
identical argv list. It needs no temporary manifest/concat-list files at
all: every segment's image is fed as its own `-loop 1 -t <duration> -i
<path>` input, every narration clip as its own `-i <path>` input, and
ffmpeg's `concat`/`xfade` FILTERS (inside one `-filter_complex` graph) do
all joining in-memory -- the alternative `concat` DEMUXER (which needs an
on-disk list file) is deliberately not used, so there is no temp-file
directory or cleanup policy to document.

Canvas/timing policy: every segment's image must already be exactly
`settings.width`x`settings.height` (checked via Pillow before ffmpeg ever
runs -- `VideoDimensionMismatchError` if not; this encoder never
upscales/downscales/crops the SOURCE to compensate for a mismatch --
motion-lite's own internal crop/scale use, described below, is a
completely separate concern operating on an already-correctly-sized
frame). Each segment's image is held for exactly `duration_ms`; each
segment's narration clips play back-to-back from the segment's start,
silence-padded (via `apad=whole_dur=...`) to fill the remainder of
`duration_ms` if the real audio is shorter, and rejected outright
(`NarrationDurationOverflowError`, before ffmpeg ever runs) if the real
audio is longer -- audio is never truncated or time-stretched, and
CROSSFADE never touches audio at all (see below).

--- Phase 29: CROSSFADE execution ---

CROSSFADE now executes a real, deterministic dissolve via ffmpeg's
`xfade` filter (`transition=fade`) instead of being rejected. The hard
constraint (requirement #4) is that the FINAL encoded duration must still
equal `VideoEncodeRequest.total_duration_ms` exactly -- a naive `xfade`
between two `duration_ms`-long clips would SHORTEN the combined output by
the crossfade's own duration (the two clips overlap during the blend), so
this encoder compensates by EXTENDING the earlier segment's own `-loop`
duration by `crossfade_duration_ms` before the fade ever runs
(`_effective_segment_duration_ms`): the fade then "spends" exactly that
extension consuming the earlier segment's extended tail, netting zero
duration change. Concretely, for a CROSSFADE from segment A (duration Da)
into segment B (duration Db), with crossfade duration X:

    A's own `-loop`/`-i` input is held for (Da + X) ms, not Da ms.
    xfade(A, B, offset=Da/1000 seconds, duration=X/1000 seconds)
    combined length = Da/1000 + Db/1000 seconds -- exactly Da + Db,
    unchanged from a plain CUT join.

This generalizes cleanly across a chain of N segments with any mix of
CUT/HOLD/CROSSFADE joins: the offset for the join between the running
combined stream and segment i is always the SUM of the ORIGINAL (never
extended) `duration_ms` of segments `0..i-1` -- i.e. exactly where
segment i would start in a plain, no-crossfade concatenation, regardless
of how many crossfades happened earlier in the chain. Only the segment
whose OWN `transition_out` is CROSSFADE is ever extended, and the LAST
segment is never extended (there is no next segment for its tail to
blend into, even if its own `transition_out` happened to be CROSSFADE --
that value is simply unused for the last position, exactly as the FIRST
segment's `transition_in` is unused: neither has an adjacent partner to
transition with, and Phase 27's TimelineManifest defines no semantics for
"transitioning in from nothing" or "out to nothing"). `transition_in` is
never consulted for join decisions anywhere -- only `transition_out`
governs whether/how a segment joins the NEXT one; this is a deliberate,
documented single-source-of-truth choice, not an oversight.

CROSSFADE is VISUAL ONLY: the audio filter graph (per-segment narration
concat + `apad=whole_dur=<segment's own ORIGINAL duration_ms>`, then a
plain `concat` across every segment) is completely untouched by Phase 29
-- narration is never crossfaded, never time-shifted, never overlapped.

Validation (`InvalidCrossfadeDurationError`, raised before ffmpeg ever
runs): `crossfade_duration_ms` must be strictly less than BOTH adjacent
segments' own `duration_ms` for every CROSSFADE join -- never silently
shortened to fit.

--- Phase 29: motion-lite ---

A deliberately tiny, deterministic per-segment camera-motion vocabulary
(`VisualMotionType`, app/models/timeline.py) executes entirely within
each segment's own normalization filter chain -- it never changes segment
timing, never touches audio, never reopens a LayerCompositionSpec/
DiagramSpec, and operates only on the FINAL resolved raster for that
segment (never an individual composited layer). STATIC applies no motion
filter at all -- its chain is identical to Phase 28's.

SLOW_ZOOM_IN/OUT use `zoompan`, not `crop`: a `crop` filter's `w`/`h`
expressions are evaluated exactly ONCE, at filter-graph configuration
time (confirmed empirically -- a `t`-based `w`/`h` expression fails
immediately with "Error when evaluating the expression", before any
frame is processed), so a time-varying crop SIZE cannot be built from
`crop` alone (only its `x`/`y` are genuinely re-evaluated every frame,
which is what motion-lite's PAN uses below). `zoompan` is the filter
ffmpeg actually re-evaluates its zoom expression against per output
frame -- but its usual `zoom+=increment` self-referencing pattern is the
well-documented source of accumulation/reset quirks. This encoder avoids
that pattern entirely: the zoom expression is a pure function of `on`
(zoompan's own monotonic output-frame index), never referencing its own
previous value, so there is nothing to accumulate or drift. Pairing
`-framerate <fps>` on the segment's looped image input (deterministic
input frame count) with `d=1` (one zoompan output frame per input frame,
since the loop already supplies a distinct frame per timestamp) makes
`on` range exactly `0..total_frames-1` across the segment's own duration.
Range: 1.00 -> 1.06 for SLOW_ZOOM_IN, 1.06 -> 1.00 for SLOW_ZOOM_OUT
(Phase 29's own preferred default, deliberately small); `zoompan`'s own
`s=` option both performs the zoom crop AND rescales back to the fixed
canvas in one step, so no separate `scale` call is needed for zoom.

PAN_LEFT/PAN_RIGHT pre-scale the source 5% wider (`round(width*1.05)`)
than the canvas, then `crop` a canvas-sized window whose `x` position
moves linearly (via a `t`-based expression, clamped with `min`/`max`)
across the available horizontal travel over the segment's own duration --
PAN_RIGHT moves left-to-right (revealing more of the source's right
side), PAN_LEFT the reverse. No vertical pan (kept out of MVP scope
exactly as this phase's requirements prefer). If the configured canvas
width is small enough that the 5% pre-scale rounds to zero added pixels,
this fails explicitly (`MotionSourceTooSmallError`) rather than silently
producing a zero-travel "pan."

Motion is always explicitly authored (VideoSegmentInput.motion, carried
through unchanged from TimelineSegment.visual_motion) -- there is no
content analysis, no saliency, no LLM inference anywhere in this module;
a segment with no motion instruction is STATIC, always.

Required-filter check (`_check_ffmpeg_filters_available`, requirement
#14): before building the real command, this encoder queries the
resolved `ffmpeg` executable's own `-filters` output once and confirms
`xfade` (if any CROSSFADE join exists), `zoompan` (if any SLOW_ZOOM_IN/
SLOW_ZOOM_OUT segment exists), and `crop`/`scale` (if any PAN_LEFT/
PAN_RIGHT segment exists) are actually present in this local build --
`FFmpegFilterUnavailableError` if not, never a silent fallback and never
an automatic download of a different build.

--- Phase 30: music/SFX mix execution ---

`TimelineManifest.cues` (MUSIC_BED_START/MUSIC_DUCK/MUSIC_LIFT/
MUSIC_BED_END/SFX_TRIGGER) become a real mixed audio track, layered
underneath the narration that remains Phase 28/29's exact, unchanged
timing authority. `app/video_encoder/audio_mix.py`'s `build_audio_mix_plan`
is a pure planning step (no filesystem, no subprocess) that turns
`VideoEncodeRequest.cues` + `VideoEncodeRequest.audio_bindings` into a
flat `AudioMixPlan` (contiguous music gain regions, independent SFX
events) -- see that module's own docstring for the music state-machine
rules and the "unterminated bed extends to total_duration_ms" policy.
`VideoEncodeRequest.cues` defaults to empty, in which case NOTHING in
this section engages at all -- the audio filter graph is byte-for-byte
Phase 29's own, regardless of what `audio_bindings` carries. This is a
deliberate opt-in: `app/renderers/video/renderer.py` only forwards a
`TimelineManifest`'s (always-present, per `MusicState` having no "none"
member) music cues when its own caller explicitly supplies
`VideoRendererInput.audio_bindings`, so every pre-Phase-30 caller keeps
Phase 29's exact behavior with zero code changes.

Music playback: exactly one background bed (`AudioAssetBindings.
music_bed_path`), input via `-stream_loop -1` (loops indefinitely at the
demuxer level, so this encoder never needs to measure the source file's
own duration) and cut to size per region via `atrim` -- naturally
handling BOTH "shorter than needed" (loops) and "longer than needed"
(trimmed) with the same mechanism. Each contiguous gain region (BED/DUCK/
LIFT, per `VideoEncodingSettings.music_bed_gain_db`/`music_duck_gain_db`/
`music_lift_gain_db`) becomes its own `atrim`+`volume` slice of the same
looped source (via `asplit` when more than one region exists), and
adjacent regions are joined with `acrossfade` over `music_gain_ramp_ms`
-- the exact same duration-neutral extend-then-overlap trick this
module's CROSSFADE section already uses for video, applied to audio: the
earlier region's own trimmed length is extended by `music_gain_ramp_ms`
before the crossfade "spends" that extension, so the combined bed length
still equals the naive sum of every region's own authored duration. A
single-region bed (no DUCK/LIFT ever happened) skips `asplit`/
`acrossfade` entirely -- there is nothing to crossfade into. The finished
bed is positioned in the overall timeline via `adelay=<bed_start_ms>:
all=1` (channel-count-agnostic).

SFX: each `SFX_TRIGGER` cue's own `reference` field is already a stable
id (Phase 27's `TimelineBuilder` populates it from
`RenderedVoiceTake.sfx_opportunity`) resolved via
`AudioAssetBindings.sfx_by_id`. Each event gets its own `-i` input (never
deduplicated by file, exactly like this module's own reused-visual
policy), trimmed to `atrim=0:<seconds until total_duration_ms>` (a
ceiling, not a padding instruction -- a naturally shorter clip is
unaffected; a clip that would otherwise run past the timeline's own end
is cut off there) and delayed into place via
`adelay=<cue.timestamp_ms>:all=1`, at one fixed
`VideoEncodingSettings.sfx_gain_db`.

Final mix: `[anarr]` (narration, exactly Phase 29's own final label
renamed only when mixing is engaged) plus `[amusicdelayed]` (if any) plus
every `[asfxN]`, combined via `amix=inputs=N:duration=longest:
normalize=0` -- `normalize=0` is essential: `amix`'s own default silently
attenuates every input by `1/N`, which would defeat every gain level this
module deliberately chose. The outer command's own final `-t
<total_duration_ms>` (unchanged since Phase 28) remains the authoritative
hard bound regardless of any internal filter-graph rounding.

Asset validation happens in two passes, mirroring this module's existing
visual/narration validation split: `_validate_audio_assets` (file exists,
supported suffix -- `MissingMusicAssetError`/`MissingSFXAssetError`/
`UnsupportedAudioAssetFormatError`) runs during `_validate`, before any
executable is resolved; `_check_audio_assets_readable` (an `ffprobe`
stream-presence check, once per DISTINCT bound file this request actually
uses) runs alongside `_check_ffmpeg_filters_available`, after executables
are resolved but before the real command is built.
"""

from __future__ import annotations

import re
import shutil
import subprocess
import wave
from pathlib import Path
from typing import Callable, Protocol

from PIL import Image, UnidentifiedImageError

from app.models.timeline import TimelineTransitionType, VisualMotionType
from app.video_encoder.audio_mix import AudioMixPlan, MusicRegionPlan, SfxEventPlan, build_audio_mix_plan, count_music_cues
from app.video_encoder.errors import (
    AudioAssetUnreadableError,
    FFmpegFilterUnavailableError,
    FFmpegNotFoundError,
    FFprobeNotFoundError,
    InvalidCrossfadeDurationError,
    MissingMusicAssetError,
    MissingNarrationFileError,
    MissingSFXAssetError,
    MissingVisualFileError,
    MotionSourceTooSmallError,
    NarrationDurationOverflowError,
    OutputPathError,
    UnsupportedAudioAssetFormatError,
    UnsupportedMotionTypeError,
    UnsupportedNarrationFormatError,
    UnsupportedVideoTransitionError,
    UnsupportedVisualFormatError,
    VideoDimensionMismatchError,
    VideoEncodingProcessError,
    VideoOutputCorruptError,
    VideoOutputNotProducedError,
)
from app.video_encoder.models import (
    AudioAssetBindings,
    VideoEncodeRequest,
    VideoEncodeResult,
    VideoSegmentInput,
)

_SUPPORTED_IMAGE_SUFFIXES = frozenset({".png", ".jpg", ".jpeg"})
_SUPPORTED_NARRATION_SUFFIXES = frozenset({".wav"})
_SUPPORTED_AUDIO_ASSET_SUFFIXES = frozenset({".wav", ".mp3", ".m4a", ".aac"})
_STDERR_TAIL_CHARS = 2000

_ZOOM_RANGE_BY_MOTION = {
    VisualMotionType.SLOW_ZOOM_IN: (1.00, 1.06),
    VisualMotionType.SLOW_ZOOM_OUT: (1.06, 1.00),
}
_PAN_PRESCALE_FACTOR = 1.05


class ProcessResult(Protocol):
    returncode: int
    stdout: str
    stderr: str


ProcessRunner = Callable[[list[str]], ProcessResult]


def _default_runner(command: list[str]) -> subprocess.CompletedProcess:
    """The real subprocess runner -- argument list only, never
    `shell=True`, never a raw interpolated command string."""
    return subprocess.run(command, capture_output=True, text=True, timeout=600)


class VideoEncoder:
    """Stateless and dependency-free at construction time -- unlike
    TiCompositor, it takes no required constructor argument; `runner` is
    injectable purely so tests can exercise process-failure/success
    behavior without actually invoking ffmpeg."""

    def __init__(self, *, runner: ProcessRunner = _default_runner):
        self._runner = runner

    def encode(self, request: VideoEncodeRequest) -> VideoEncodeResult:
        self._validate(request)

        audio_mix_plan = build_audio_mix_plan(
            request.cues, request.audio_bindings, request.total_duration_ms, request.settings
        )
        self._validate_audio_assets(audio_mix_plan, request.audio_bindings)

        ffmpeg_executable = _resolve_executable(
            request.settings.ffmpeg_path, "ffmpeg", FFmpegNotFoundError
        )
        ffprobe_executable = _resolve_executable(
            request.settings.ffprobe_path, "ffprobe", FFprobeNotFoundError
        )
        self._check_ffmpeg_filters_available(
            ffmpeg_executable, _required_filters(request.segments, audio_mix_plan)
        )
        self._check_audio_assets_readable(ffprobe_executable, audio_mix_plan, request.audio_bindings)

        try:
            request.output_path.parent.mkdir(parents=True, exist_ok=True)
        except OSError as exc:
            raise OutputPathError(
                f"Cannot create output directory {request.output_path.parent}: {exc}"
            ) from exc

        command = FFmpegCommandBuilder.build(request, ffmpeg_executable, audio_mix_plan)
        result = self._runner(command)
        if result.returncode != 0:
            raise VideoEncodingProcessError(
                f"ffmpeg exited with code {result.returncode}: {_tail(result.stderr)}"
            )

        if not request.output_path.is_file():
            raise VideoOutputNotProducedError(
                f"ffmpeg exited 0 but no output file exists at {request.output_path}"
            )
        file_size_bytes = request.output_path.stat().st_size
        if file_size_bytes == 0:
            raise VideoOutputCorruptError(f"Output file at {request.output_path} is zero bytes")

        self._verify_output_with_ffprobe(ffprobe_executable, request.output_path)

        return VideoEncodeResult(
            output_path=request.output_path,
            width=request.settings.width,
            height=request.settings.height,
            fps=request.settings.fps,
            duration_ms=request.total_duration_ms,
            file_size_bytes=file_size_bytes,
            video_codec=request.settings.video_codec,
            audio_codec=request.settings.audio_codec,
            ffmpeg_command=command,
            crossfade_count=_crossfade_count(request.segments),
            motion_profile_used=_motion_profile_used(request.segments),
            has_music=audio_mix_plan.has_music,
            sfx_event_count=len(audio_mix_plan.sfx_events),
            music_cue_count=count_music_cues(request.cues),
        )

    def _validate(self, request: VideoEncodeRequest) -> None:
        settings = request.settings

        self._validate_crossfade_durations(request.segments, settings.crossfade_duration_ms)
        for segment in request.segments:
            self._validate_transition(segment)
            self._validate_visual(segment, settings.width, settings.height)
            self._validate_narration(segment)
            self._validate_motion(segment, settings.width)

    @staticmethod
    def _validate_audio_assets(plan: AudioMixPlan, bindings: AudioAssetBindings) -> None:
        if plan.has_music:
            if bindings.music_bed_path is None:
                raise MissingMusicAssetError(
                    "The timeline's cues require a music bed (a MUSIC_BED_START region "
                    "exists) but AudioAssetBindings.music_bed_path was not provided"
                )
            _validate_audio_asset_file(bindings.music_bed_path, MissingMusicAssetError, "Music bed")

        for event in plan.sfx_events:
            _validate_audio_asset_file(
                event.file_path, MissingSFXAssetError, f"SFX {event.sfx_id!r}"
            )

    def _check_audio_assets_readable(
        self, ffprobe_executable: str, plan: AudioMixPlan, bindings: AudioAssetBindings
    ) -> None:
        paths_to_check: list[Path] = []
        if plan.has_music:
            paths_to_check.append(bindings.music_bed_path)
        seen: set[Path] = set()
        for event in plan.sfx_events:
            if event.file_path not in seen:
                seen.add(event.file_path)
                paths_to_check.append(event.file_path)

        for path in paths_to_check:
            command = [
                ffprobe_executable, "-v", "error",
                "-show_entries", "stream=codec_type",
                "-of", "csv=p=0",
                str(path),
            ]
            result = self._runner(command)
            if result.returncode != 0:
                raise AudioAssetUnreadableError(
                    f"ffprobe could not read {path}: {_tail(result.stderr)}"
                )
            stream_types = {line.strip() for line in result.stdout.splitlines() if line.strip()}
            if "audio" not in stream_types:
                raise AudioAssetUnreadableError(f"ffprobe found no audio stream in {path}")

    @staticmethod
    def _validate_crossfade_durations(segments: list[VideoSegmentInput], crossfade_duration_ms: int) -> None:
        for i in range(len(segments) - 1):
            if segments[i].transition_out is not TimelineTransitionType.CROSSFADE:
                continue
            earlier, later = segments[i], segments[i + 1]
            if crossfade_duration_ms >= earlier.duration_ms or crossfade_duration_ms >= later.duration_ms:
                raise InvalidCrossfadeDurationError(
                    f"CROSSFADE between segments {earlier.segment_id!r} ({earlier.duration_ms}ms) "
                    f"and {later.segment_id!r} ({later.duration_ms}ms) requests "
                    f"crossfade_duration_ms={crossfade_duration_ms}, which must be strictly "
                    f"shorter than both -- never silently shortened to fit"
                )

    @staticmethod
    def _validate_transition(segment: VideoSegmentInput) -> None:
        # Defensive only: TimelineTransitionType is a closed 3-member enum
        # and every member is executable as of Phase 29 -- this can never
        # actually fire today. Kept for a hypothetical future member this
        # encoder does not yet know how to execute; never a silent
        # fallback to CUT.
        if segment.transition_in not in TimelineTransitionType or segment.transition_out not in TimelineTransitionType:
            raise UnsupportedVideoTransitionError(
                f"Segment {segment.segment_id!r} uses a transition this encoder does not "
                f"recognize"
            )

    @staticmethod
    def _validate_visual(segment: VideoSegmentInput, canvas_width: int, canvas_height: int) -> None:
        image_path = segment.image_path
        if not image_path.is_file():
            raise MissingVisualFileError(
                f"Segment {segment.segment_id!r} image not found: {image_path}"
            )
        if image_path.suffix.lower() not in _SUPPORTED_IMAGE_SUFFIXES:
            raise UnsupportedVisualFormatError(
                f"Segment {segment.segment_id!r} image {image_path} has unsupported format "
                f"{image_path.suffix!r}; expected one of {sorted(_SUPPORTED_IMAGE_SUFFIXES)}"
            )
        try:
            with Image.open(image_path) as image:
                width, height = image.size
        except (UnidentifiedImageError, OSError) as exc:
            raise UnsupportedVisualFormatError(
                f"Segment {segment.segment_id!r} image {image_path} could not be read: {exc}"
            ) from exc

        if (width, height) != (canvas_width, canvas_height):
            raise VideoDimensionMismatchError(
                f"Segment {segment.segment_id!r} image {image_path} is {width}x{height}, but "
                f"the output canvas is {canvas_width}x{canvas_height} -- every visual must "
                f"match exactly (this encoder never resizes the SOURCE to compensate)"
            )

    @staticmethod
    def _validate_narration(segment: VideoSegmentInput) -> None:
        total_narration_ms = 0
        for clip in segment.narration_clips:
            if not clip.file_path.is_file():
                raise MissingNarrationFileError(
                    f"Segment {segment.segment_id!r} narration file not found: {clip.file_path}"
                )
            if clip.file_path.suffix.lower() not in _SUPPORTED_NARRATION_SUFFIXES:
                raise UnsupportedNarrationFormatError(
                    f"Segment {segment.segment_id!r} narration file {clip.file_path} has "
                    f"unsupported format {clip.file_path.suffix!r}; expected one of "
                    f"{sorted(_SUPPORTED_NARRATION_SUFFIXES)}"
                )
            try:
                total_narration_ms += _measure_wav_duration_ms(clip.file_path)
            except (wave.Error, EOFError, OSError) as exc:
                raise UnsupportedNarrationFormatError(
                    f"Segment {segment.segment_id!r} narration file {clip.file_path} could not "
                    f"be read as WAV: {exc}"
                ) from exc

        if total_narration_ms > segment.duration_ms:
            raise NarrationDurationOverflowError(
                f"Segment {segment.segment_id!r} narration totals {total_narration_ms}ms, "
                f"exceeding its authored duration of {segment.duration_ms}ms -- audio is "
                f"never truncated to fit"
            )

    @staticmethod
    def _validate_motion(segment: VideoSegmentInput, canvas_width: int) -> None:
        if segment.motion not in (VisualMotionType.PAN_LEFT, VisualMotionType.PAN_RIGHT):
            return
        prescale_width = round(canvas_width * _PAN_PRESCALE_FACTOR)
        if prescale_width - canvas_width <= 0:
            raise MotionSourceTooSmallError(
                f"Segment {segment.segment_id!r} requests {segment.motion.value}, but the "
                f"output canvas width ({canvas_width}px) is too small for the "
                f"{_PAN_PRESCALE_FACTOR}x pre-scale to add any whole-pixel travel"
            )

    def _check_ffmpeg_filters_available(self, ffmpeg_executable: str, required_filters: set[str]) -> None:
        if not required_filters:
            return
        command = [ffmpeg_executable, "-hide_banner", "-filters"]
        result = self._runner(command)
        if result.returncode != 0:
            raise FFmpegFilterUnavailableError(
                f"Failed to query ffmpeg's available filters: {_tail(result.stderr)}"
            )
        missing = sorted(name for name in required_filters if not _filter_listed(result.stdout, name))
        if missing:
            raise FFmpegFilterUnavailableError(
                f"The resolved ffmpeg build does not report the following required filter(s) "
                f"in `ffmpeg -filters`: {missing} -- this encoder never downloads or "
                f"substitutes a different ffmpeg build automatically"
            )

    def _verify_output_with_ffprobe(self, ffprobe_executable: str, output_path: Path) -> None:
        command = [
            ffprobe_executable, "-v", "error",
            "-show_entries", "stream=codec_type",
            "-of", "csv=p=0",
            str(output_path),
        ]
        result = self._runner(command)
        if result.returncode != 0:
            raise VideoOutputCorruptError(
                f"ffprobe reported an error inspecting {output_path}: {_tail(result.stderr)}"
            )
        stream_types = {line.strip() for line in result.stdout.splitlines() if line.strip()}
        if "video" not in stream_types or "audio" not in stream_types:
            raise VideoOutputCorruptError(
                f"ffprobe did not find both a video and an audio stream in {output_path}: "
                f"found {sorted(stream_types)}"
            )


class FFmpegCommandBuilder:
    """Pure command construction, testable without running ffmpeg.
    `build()` never touches the filesystem or a subprocess -- it only
    assembles an argv list from an already-valid VideoEncodeRequest."""

    @staticmethod
    def build(
        request: VideoEncodeRequest, ffmpeg_executable: str, audio_mix_plan: AudioMixPlan | None = None
    ) -> list[str]:
        settings = request.settings
        segments = request.segments
        plan = audio_mix_plan if audio_mix_plan is not None else AudioMixPlan()

        command: list[str] = [ffmpeg_executable, "-y"]

        for i, segment in enumerate(segments):
            effective_ms = _effective_segment_duration_ms(segments, i, settings.crossfade_duration_ms)
            # -framerate pins each looped image's own native input frame rate
            # to settings.fps deterministically (rather than ffmpeg's default
            # guess) -- SLOW_ZOOM_IN/OUT's zoompan frame-count math below
            # depends on knowing exactly how many input frames a segment
            # produces.
            command += [
                "-loop", "1",
                "-framerate", str(settings.fps),
                "-t", _ms_to_seconds_str(effective_ms),
                "-i", str(segment.image_path),
            ]

        narration_input_indices: list[list[int]] = []
        next_input_index = len(segments)
        for segment in segments:
            indices = []
            for clip in segment.narration_clips:
                command += ["-i", str(clip.file_path)]
                indices.append(next_input_index)
                next_input_index += 1
            narration_input_indices.append(indices)

        music_input_index: int | None = None
        if plan.has_music:
            # -stream_loop -1 loops the source indefinitely at the demuxer
            # level -- this encoder never needs to measure the music
            # file's own duration; atrim (in the filter graph) both loops
            # (by simply having enough looped material to draw from) and
            # trims (by capping at its own `end=`) with one mechanism.
            command += ["-stream_loop", "-1", "-i", str(request.audio_bindings.music_bed_path)]
            music_input_index = next_input_index
            next_input_index += 1

        sfx_input_indices: list[int] = []
        for event in plan.sfx_events:
            command += ["-i", str(event.file_path)]
            sfx_input_indices.append(next_input_index)
            next_input_index += 1

        filter_complex = _build_filter_complex(
            segments, narration_input_indices, settings, plan,
            music_input_index, sfx_input_indices, request.total_duration_ms,
        )

        command += ["-filter_complex", filter_complex]
        command += ["-map", "[vout]", "-map", "[aout]"]
        command += ["-c:v", settings.video_codec, "-pix_fmt", settings.pixel_format, "-r", str(settings.fps)]
        command += ["-c:a", settings.audio_codec]
        command += ["-t", _ms_to_seconds_str(request.total_duration_ms)]
        command += ["-movflags", "+faststart"]
        command.append(str(request.output_path))
        return command


def _effective_segment_duration_ms(
    segments: list[VideoSegmentInput], index: int, crossfade_duration_ms: int
) -> int:
    """A segment's own `-loop` duration: extended by crossfade_duration_ms
    only when it CROSSFADEs into a next segment (never the last segment,
    which has none to extend into) -- see this module's docstring for the
    full derivation of why this exactly preserves total timeline
    duration."""
    segment = segments[index]
    is_last = index == len(segments) - 1
    if not is_last and segment.transition_out is TimelineTransitionType.CROSSFADE:
        return segment.duration_ms + crossfade_duration_ms
    return segment.duration_ms


def _build_filter_complex(
    segments: list[VideoSegmentInput],
    narration_input_indices: list[list[int]],
    settings,
    audio_mix_plan: AudioMixPlan,
    music_input_index: int | None,
    sfx_input_indices: list[int],
    total_duration_ms: int,
) -> str:
    filter_parts = _build_video_filter_parts(segments, settings)

    mixing_needed = audio_mix_plan.has_music or bool(audio_mix_plan.sfx_events)
    narration_label = "anarr" if mixing_needed else "aout"
    filter_parts += _build_audio_filter_parts(segments, narration_input_indices, final_label=narration_label)

    if mixing_needed:
        mix_labels = [f"[{narration_label}]"]
        if audio_mix_plan.has_music:
            music_parts, music_label = _build_music_filter_parts(
                music_input_index, audio_mix_plan.music_regions, settings
            )
            filter_parts += music_parts
            mix_labels.append(music_label)
        for i, (sfx_input_index, event) in enumerate(zip(sfx_input_indices, audio_mix_plan.sfx_events)):
            sfx_parts, sfx_label = _build_sfx_filter_parts(
                i, sfx_input_index, event, settings, total_duration_ms
            )
            filter_parts += sfx_parts
            mix_labels.append(sfx_label)
        # normalize=0 is essential: amix's own default silently scales
        # every input down by 1/N, which would defeat every gain level
        # this module deliberately chose.
        filter_parts.append(
            f"{''.join(mix_labels)}amix=inputs={len(mix_labels)}:duration=longest:normalize=0[aout]"
        )

    return ";".join(filter_parts)


def _build_video_filter_parts(segments: list[VideoSegmentInput], settings) -> list[str]:
    filter_parts: list[str] = []
    normalized_labels: list[str] = []
    for i, segment in enumerate(segments):
        motion_chain = _motion_filter_chain(segment, settings)
        label = f"vnorm{i}"
        # settb=AVTB puts every segment's stream on the SAME arbitrary
        # timebase before it ever reaches a concat/xfade node -- without
        # it, zoompan's own internal fixed-fps timebase does not match the
        # generic timebase a plain scale/crop chain carries, and xfade
        # refuses to join two streams whose timebases differ (confirmed
        # empirically: "First input link main timebase ... do not match
        # the corresponding second input link xfade timebase").
        filter_parts.append(
            f"[{i}:v]{motion_chain},fps={settings.fps},format={settings.pixel_format},"
            f"setsar=1,settb=AVTB[{label}]"
        )
        normalized_labels.append(label)

    n = len(segments)
    has_crossfade = any(
        segments[i].transition_out is TimelineTransitionType.CROSSFADE for i in range(n - 1)
    )
    if not has_crossfade:
        # Unchanged from Phase 28: one flat concat across every segment.
        video_refs = "".join(f"[{label}]" for label in normalized_labels)
        filter_parts.append(f"{video_refs}concat=n={n}:v=1:a=0[vout]")
        return filter_parts

    # Phase 29: a left-to-right pairwise chain, alternating xfade/concat
    # per join so CUT/HOLD and CROSSFADE joins can freely mix in any
    # order. The running offset is always the sum of the ORIGINAL
    # (never-extended) durations of every segment processed so far --
    # exactly where the next segment would start in a plain concat,
    # regardless of how many crossfades already happened in the chain.
    cur_label = normalized_labels[0]
    cumulative_ms = segments[0].duration_ms
    for i in range(1, n):
        out_label = "vout" if i == n - 1 else f"vjoin{i}"
        if segments[i - 1].transition_out is TimelineTransitionType.CROSSFADE:
            offset_str = _ms_to_seconds_str(cumulative_ms)
            duration_str = _ms_to_seconds_str(settings.crossfade_duration_ms)
            filter_parts.append(
                f"[{cur_label}][{normalized_labels[i]}]xfade=transition=fade:"
                f"duration={duration_str}:offset={offset_str}[{out_label}]"
            )
        else:
            filter_parts.append(f"[{cur_label}][{normalized_labels[i]}]concat=n=2:v=1:a=0[{out_label}]")
        cur_label = out_label
        cumulative_ms += segments[i].duration_ms

    return filter_parts


def _build_audio_filter_parts(
    segments: list[VideoSegmentInput], narration_input_indices: list[list[int]], final_label: str = "aout"
) -> list[str]:
    """Unchanged from Phase 28/29 -- CROSSFADE is visual only, so
    narration is always concatenated/silence-padded against each
    segment's own ORIGINAL duration_ms, never its (possibly video-side-
    extended) effective duration. `final_label` is "aout" (Phase 28/29's
    own name) whenever no music/SFX mixing is engaged; Phase 30 renames
    it to "anarr" only when a further amix stage will consume it."""
    filter_parts: list[str] = []
    audio_labels: list[str] = []
    for i, segment in enumerate(segments):
        indices = narration_input_indices[i]
        clip_labels = [f"[{idx}:a]" for idx in indices]
        raw_label = f"aseg{i}raw"
        if len(clip_labels) == 1:
            filter_parts.append(f"{clip_labels[0]}anull[{raw_label}]")
        else:
            filter_parts.append(f"{''.join(clip_labels)}concat=n={len(clip_labels)}:v=0:a=1[{raw_label}]")

        padded_label = f"aseg{i}"
        filter_parts.append(
            f"[{raw_label}]apad=whole_dur={_ms_to_seconds_str(segment.duration_ms)}[{padded_label}]"
        )
        audio_labels.append(f"[{padded_label}]")
    filter_parts.append(f"{''.join(audio_labels)}concat=n={len(segments)}:v=0:a=1[{final_label}]")
    return filter_parts


def _build_music_filter_parts(
    music_input_index: int, regions: list[MusicRegionPlan], settings
) -> tuple[list[str], str]:
    """One continuous, looped/trimmed music bed, sliced into one
    atrim+volume segment per gain region and (when there is more than
    one) crossfaded together via acrossfade -- see this module's own
    docstring for the full duration-neutral derivation, identical in
    shape to CROSSFADE's own video-side math. Returns (filter parts, the
    final `[label]` reference to mix in)."""
    ramp_ms = settings.music_gain_ramp_ms
    bed_start_ms = regions[0].start_ms
    n = len(regions)
    filter_parts: list[str] = []

    if n == 1:
        region = regions[0]
        duration_ms = region.end_ms - region.start_ms
        filter_parts.append(
            f"[{music_input_index}:a]atrim=start=0.000:end={_ms_to_seconds_str(duration_ms)},"
            f"asetpts=PTS-STARTPTS,volume={region.gain_db}dB[amusicbed]"
        )
    else:
        split_labels = [f"amusicsplit{i}" for i in range(n)]
        filter_parts.append(
            f"[{music_input_index}:a]asplit={n}" + "".join(f"[{label}]" for label in split_labels)
        )
        seg_labels = []
        for i, region in enumerate(regions):
            is_last = i == n - 1
            region_duration_ms = region.end_ms - region.start_ms
            extended_ms = region_duration_ms if is_last else region_duration_ms + ramp_ms
            trim_start_ms = region.start_ms - bed_start_ms
            trim_end_ms = trim_start_ms + extended_ms
            label = f"amusicseg{i}"
            filter_parts.append(
                f"[{split_labels[i]}]atrim=start={_ms_to_seconds_str(trim_start_ms)}:"
                f"end={_ms_to_seconds_str(trim_end_ms)},asetpts=PTS-STARTPTS,"
                f"volume={region.gain_db}dB[{label}]"
            )
            seg_labels.append(label)

        cur_label = seg_labels[0]
        for i in range(1, n):
            out_label = "amusicbed" if i == n - 1 else f"amusicjoin{i}"
            filter_parts.append(
                f"[{cur_label}][{seg_labels[i]}]acrossfade=d={_ms_to_seconds_str(ramp_ms)}:"
                f"c1=tri:c2=tri[{out_label}]"
            )
            cur_label = out_label

    filter_parts.append(f"[amusicbed]adelay={bed_start_ms}:all=1[amusicdelayed]")
    return filter_parts, "[amusicdelayed]"


def _build_sfx_filter_parts(
    index: int, sfx_input_index: int, event: SfxEventPlan, settings, total_duration_ms: int
) -> tuple[list[str], str]:
    """One independent, one-shot SFX playback -- atrim's own `end=` is a
    ceiling (never a padding instruction), so a naturally shorter clip is
    unaffected and a clip that would otherwise run past the timeline's
    own end is cut off there (requirement #9's preferred policy)."""
    remaining_ms = total_duration_ms - event.timestamp_ms
    label = f"asfx{index}"
    filter_parts = [
        f"[{sfx_input_index}:a]atrim=start=0.000:end={_ms_to_seconds_str(remaining_ms)},"
        f"asetpts=PTS-STARTPTS,volume={settings.sfx_gain_db}dB,"
        f"adelay={event.timestamp_ms}:all=1[{label}]"
    ]
    return filter_parts, f"[{label}]"


def _motion_filter_chain(segment: VideoSegmentInput, settings) -> str:
    """Returns the filter-graph fragment (no leading `[label]`, no
    trailing `,fps=...` normalization -- the caller appends those)
    implementing this segment's own VisualMotionType. Assumes the source
    is already exactly settings.width x settings.height (guaranteed by
    VideoEncoder._validate_visual before this is ever called)."""
    width, height = settings.width, settings.height

    if segment.motion is VisualMotionType.STATIC:
        return f"scale={width}:{height}"

    if segment.motion in _ZOOM_RANGE_BY_MOTION:
        start, end = _ZOOM_RANGE_BY_MOTION[segment.motion]
        # crop's own w/h expressions are evaluated ONCE at filter-graph
        # configuration time (confirmed empirically: a `t`-based w/h
        # expression fails with "Error when evaluating the expression" at
        # startup, before any frame flows) -- only crop's x/y are
        # documented and confirmed to be re-evaluated per frame. A
        # time-varying crop SIZE is therefore not achievable via crop
        # alone, which is why zoom uses zoompan instead. To avoid
        # zoompan's well-known self-referencing `zoom+=increment`
        # accumulation quirk, the zoom expression here is a pure function
        # of `on` (zoompan's own monotonic output-frame counter) rather
        # than referencing its own previous value -- with `-framerate` set
        # on this segment's input (see FFmpegCommandBuilder.build) and
        # `d=1` (one output frame per input frame, since the looped input
        # already supplies one distinct frame per output frame), `on`
        # ranges exactly 0..total_frames-1 across the segment's own
        # duration, deterministically.
        total_frames = max(round(segment.duration_ms / 1000 * settings.fps), 2)
        zoom_expr = f"({start}+({end}-{start})*on/{total_frames - 1})"
        return (
            f"zoompan=z='{zoom_expr}':x='iw/2-(iw/zoom/2)':y='ih/2-(ih/zoom/2)':"
            f"d=1:s={width}x{height}:fps={settings.fps}"
        )

    if segment.motion in (VisualMotionType.PAN_LEFT, VisualMotionType.PAN_RIGHT):
        prescale_width = round(width * _PAN_PRESCALE_FACTOR)
        max_offset = prescale_width - width
        duration_seconds = _ms_to_seconds_str(segment.duration_ms)
        if segment.motion is VisualMotionType.PAN_RIGHT:
            x_expr = f"min({max_offset},max(0,{max_offset}*t/{duration_seconds}))"
        else:
            x_expr = f"min({max_offset},max(0,{max_offset}*(1-t/{duration_seconds})))"
        return f"scale={prescale_width}:{height},crop=w={width}:h={height}:x='{x_expr}':y=0"

    raise UnsupportedMotionTypeError(
        f"Segment {segment.segment_id!r} has no known filter mapping for motion {segment.motion!r}"
    )


def _required_filters(segments: list[VideoSegmentInput], audio_mix_plan: AudioMixPlan) -> set[str]:
    filters: set[str] = set()
    if any(segments[i].transition_out is TimelineTransitionType.CROSSFADE for i in range(len(segments) - 1)):
        filters.add("xfade")
    if any(segment.motion in _ZOOM_RANGE_BY_MOTION for segment in segments):
        filters.add("zoompan")
    if any(
        segment.motion in (VisualMotionType.PAN_LEFT, VisualMotionType.PAN_RIGHT) for segment in segments
    ):
        filters.add("crop")
        filters.add("scale")
    if audio_mix_plan.has_music or audio_mix_plan.sfx_events:
        filters.add("amix")
    if len(audio_mix_plan.music_regions) > 1:
        filters.add("acrossfade")
    return filters


def _validate_audio_asset_file(path: Path, missing_error_cls: type[Exception], label: str) -> None:
    if not path.is_file():
        raise missing_error_cls(f"{label} file not found: {path}")
    if path.suffix.lower() not in _SUPPORTED_AUDIO_ASSET_SUFFIXES:
        raise UnsupportedAudioAssetFormatError(
            f"{label} file {path} has unsupported format {path.suffix!r}; expected one of "
            f"{sorted(_SUPPORTED_AUDIO_ASSET_SUFFIXES)}"
        )


def _filter_listed(ffmpeg_filters_output: str, filter_name: str) -> bool:
    """Matches a `ffmpeg -filters` output line's own filter-name column
    exactly -- avoids a substring false match (e.g. "scale" incorrectly
    matching the unrelated "scale2ref" row)."""
    pattern = re.compile(rf"^\s*\S+\s+{re.escape(filter_name)}\s", re.MULTILINE)
    return pattern.search(ffmpeg_filters_output) is not None


def _crossfade_count(segments: list[VideoSegmentInput]) -> int:
    return sum(
        1 for i in range(len(segments) - 1) if segments[i].transition_out is TimelineTransitionType.CROSSFADE
    )


def _motion_profile_used(segments: list[VideoSegmentInput]) -> list[VisualMotionType]:
    seen: list[VisualMotionType] = []
    for segment in segments:
        if segment.motion is not VisualMotionType.STATIC and segment.motion not in seen:
            seen.append(segment.motion)
    return seen


def _ms_to_seconds_str(duration_ms: int) -> str:
    """Millisecond precision, deterministic string formatting -- always
    3 decimal places, regardless of platform locale."""
    return f"{duration_ms / 1000:.3f}"


def _resolve_executable(explicit_path: Path | None, name: str, error_cls: type[Exception]) -> str:
    if explicit_path is not None:
        if not Path(explicit_path).is_file():
            raise error_cls(f"Configured {name} executable not found at {explicit_path}")
        return str(explicit_path)
    resolved = shutil.which(name)
    if resolved is None:
        raise error_cls(
            f"{name} executable not found on PATH and no explicit path was configured -- "
            f"this encoder never downloads or installs one automatically"
        )
    return resolved


def _measure_wav_duration_ms(path: Path) -> int:
    """Reads only the WAV header (frame count, sample rate) -- never
    decodes audio samples. Identical approach to Phase 27's
    TimelineBuilder, deliberately not shared as a common import (this
    package stays dependency-free of app/renderers/timeline/)."""
    with wave.open(str(path), "rb") as wav_file:
        frame_count = wav_file.getnframes()
        frame_rate = wav_file.getframerate()
    if frame_rate <= 0:
        raise UnsupportedNarrationFormatError(f"WAV file at {path} reports framerate <= 0")
    return round(frame_count / frame_rate * 1000)


def _tail(text: str, max_len: int = _STDERR_TAIL_CHARS) -> str:
    text = text or ""
    if len(text) <= max_len:
        return text
    return "..." + text[-max_len:]
