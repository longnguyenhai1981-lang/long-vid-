"""Individual, independent, deterministic QC checks (Phase 32).

Every function here is pure given its already-measured inputs (a
MediaProbeResult, a list of sampled luminances/differences, a pair of
dBFS values, parsed SRT cues) -- none of them touch a subprocess or the
filesystem themselves (app/media_qc/probes.py and
app/media_qc/inspector.py own that). Each returns exactly one
QCCheckResult with a stable `check_id` (requirement #22) -- never raises
for an ordinary media defect (a defect IS the expected FAIL result), and
never invents a numeric "quality score."

Stable rule ids used across this module:

    QC_VIDEO_EXISTS, QC_VIDEO_NONZERO, QC_VIDEO_DECODE,
    QC_VIDEO_STREAM_PRESENT, QC_AUDIO_PRESENT,
    QC_VIDEO_CODEC, QC_AUDIO_CODEC, QC_VIDEO_PIXFMT,
    QC_VIDEO_FPS, QC_VIDEO_CANVAS, QC_VIDEO_DURATION,
    QC_VIDEO_STREAM_DURATION_SANITY,
    QC_VIDEO_BLACK, QC_VIDEO_FROZEN,
    QC_AUDIO_SILENCE, QC_AUDIO_CLIPPING,
    QC_SUBTITLE_EXISTS, QC_SUBTITLE_UTF8,
    QC_SUBTITLE_CUE_COUNT, QC_SUBTITLE_TIMESTAMPS_VALID,
    QC_SUBTITLE_TIMELINE_BOUNDS, QC_SUBTITLE_TEXT_NONEMPTY,
    QC_SUBTITLE_MATCH
"""

from __future__ import annotations

from pathlib import Path

from app.captions.srt import ParsedSrtCue
from app.media_qc.models import QCCheckResult, QCStatus
from app.media_qc.probes import MediaProbeResult

# ---------------------------------------------------------------------------
# File integrity (requirements #4, #18)
# ---------------------------------------------------------------------------


def check_video_file_exists(video_path: Path) -> QCCheckResult:
    exists = video_path.is_file()
    return QCCheckResult(
        check_id="QC_VIDEO_EXISTS",
        status=QCStatus.PASS if exists else QCStatus.FAIL,
        message="Video file exists" if exists else f"Video file not found: {video_path}",
        measured_value=str(exists),
        expected_value="True",
    )


def check_video_file_nonzero(video_path: Path) -> QCCheckResult:
    size = video_path.stat().st_size if video_path.is_file() else 0
    ok = size > 0
    return QCCheckResult(
        check_id="QC_VIDEO_NONZERO",
        status=QCStatus.PASS if ok else QCStatus.FAIL,
        message="Video file is non-empty" if ok else "Video file is zero bytes",
        measured_value=str(size),
        expected_value="> 0",
    )


def check_video_decodable(probe: MediaProbeResult | None, error_message: str | None) -> QCCheckResult:
    ok = probe is not None
    return QCCheckResult(
        check_id="QC_VIDEO_DECODE",
        status=QCStatus.PASS if ok else QCStatus.FAIL,
        message="ffprobe could read the container" if ok else f"ffprobe could not read the file: {error_message}",
        measured_value="readable" if ok else "unreadable",
        expected_value="readable",
    )


def check_video_stream_present(probe: MediaProbeResult) -> QCCheckResult:
    ok = probe.video_stream_count >= 1
    return QCCheckResult(
        check_id="QC_VIDEO_STREAM_PRESENT",
        status=QCStatus.PASS if ok else QCStatus.FAIL,
        message="Video stream present" if ok else "No video stream found in container",
        measured_value=str(probe.video_stream_count),
        expected_value=">= 1",
    )


def check_audio_stream_present(probe: MediaProbeResult) -> QCCheckResult:
    ok = probe.audio_stream_count >= 1
    return QCCheckResult(
        check_id="QC_AUDIO_PRESENT",
        status=QCStatus.PASS if ok else QCStatus.FAIL,
        message="Audio stream present" if ok else "No audio stream found in container",
        measured_value=str(probe.audio_stream_count),
        expected_value=">= 1",
    )


# ---------------------------------------------------------------------------
# Codec / format / geometry (requirements #6-9)
# ---------------------------------------------------------------------------


def check_video_codec(probe: MediaProbeResult, expected_codec: str) -> QCCheckResult:
    ok = probe.video_codec == expected_codec
    return QCCheckResult(
        check_id="QC_VIDEO_CODEC",
        status=QCStatus.PASS if ok else QCStatus.FAIL,
        message="Video codec matches" if ok else "Video codec mismatch",
        measured_value=str(probe.video_codec), expected_value=expected_codec,
    )


def check_audio_codec(probe: MediaProbeResult, expected_codec: str) -> QCCheckResult:
    ok = probe.audio_codec == expected_codec
    return QCCheckResult(
        check_id="QC_AUDIO_CODEC",
        status=QCStatus.PASS if ok else QCStatus.FAIL,
        message="Audio codec matches" if ok else "Audio codec mismatch",
        measured_value=str(probe.audio_codec), expected_value=expected_codec,
    )


def check_pixel_format(probe: MediaProbeResult, expected_pixel_format: str) -> QCCheckResult:
    """Requirement #9's own preferred policy: an unexpected pixel format
    is FAIL, not WARN -- device playback compatibility is part of the
    production contract, not merely a review-worthy oddity."""
    ok = probe.pixel_format == expected_pixel_format
    return QCCheckResult(
        check_id="QC_VIDEO_PIXFMT",
        status=QCStatus.PASS if ok else QCStatus.FAIL,
        message="Pixel format matches" if ok else "Pixel format mismatch",
        measured_value=str(probe.pixel_format), expected_value=expected_pixel_format,
    )


def check_fps(probe: MediaProbeResult, expected_fps: float, tolerance: float) -> QCCheckResult:
    """Compares the exact rational value ffprobe reported (already
    converted from e.g. "30000/1001" via true division in
    app/media_qc/probes.py) against the expected fps within a small
    absolute tolerance -- never a raw string comparison."""
    ok = abs(probe.fps - expected_fps) <= tolerance
    return QCCheckResult(
        check_id="QC_VIDEO_FPS",
        status=QCStatus.PASS if ok else QCStatus.FAIL,
        message="FPS matches within tolerance" if ok else "FPS mismatch",
        measured_value=f"{probe.fps:.4f}", expected_value=f"{expected_fps:.4f} (tolerance {tolerance})",
    )


def check_canvas(probe: MediaProbeResult, expected_width: int, expected_height: int) -> QCCheckResult:
    """Requirement #7: width/height must exactly equal the expected
    canvas, both must be positive, and (since this project always
    encodes yuv420p/libx264, which require even chroma-plane dimensions)
    both must be even."""
    problems = []
    if probe.width <= 0:
        problems.append("width is not positive")
    if probe.height <= 0:
        problems.append("height is not positive")
    if probe.width % 2 != 0:
        problems.append("width is odd (yuv420p/libx264 requires even width)")
    if probe.height % 2 != 0:
        problems.append("height is odd (yuv420p/libx264 requires even height)")
    if (probe.width, probe.height) != (expected_width, expected_height):
        problems.append("canvas does not match the expected resolution")

    ok = not problems
    return QCCheckResult(
        check_id="QC_VIDEO_CANVAS",
        status=QCStatus.PASS if ok else QCStatus.FAIL,
        message="Canvas matches" if ok else "; ".join(problems),
        measured_value=f"{probe.width}x{probe.height}", expected_value=f"{expected_width}x{expected_height}",
    )


# ---------------------------------------------------------------------------
# Duration (requirement #5)
# ---------------------------------------------------------------------------


def check_duration(probe: MediaProbeResult, expected_ms: int, tolerance_ms: int) -> QCCheckResult:
    """Requirement #5's own chosen, simpler policy: outside tolerance is
    FAIL outright -- no intermediate WARN band."""
    difference_ms = abs(probe.duration_ms - expected_ms)
    ok = difference_ms <= tolerance_ms
    return QCCheckResult(
        check_id="QC_VIDEO_DURATION",
        status=QCStatus.PASS if ok else QCStatus.FAIL,
        message="Duration matches TimelineManifest within tolerance" if ok else "Duration mismatch",
        measured_value=f"{probe.duration_ms}ms",
        expected_value=f"{expected_ms}ms (tolerance {tolerance_ms}ms)",
        details=f"difference={difference_ms}ms",
    )


def check_stream_duration_sanity(probe: MediaProbeResult, expected_ms: int) -> QCCheckResult:
    """A coarser safety net distinct from check_duration's own precise
    tolerance: catches a container whose own reported duration is zero/
    negative, or wildly (>10x) larger than expected -- e.g. a stray or
    unrelated file mistakenly probed."""
    ceiling_ms = max(expected_ms * 10, expected_ms + 60_000)
    if probe.duration_ms <= 0:
        return QCCheckResult(
            check_id="QC_VIDEO_STREAM_DURATION_SANITY", status=QCStatus.FAIL,
            message="Reported duration is not positive",
            measured_value=f"{probe.duration_ms}ms", expected_value="> 0ms",
        )
    if probe.duration_ms > ceiling_ms:
        return QCCheckResult(
            check_id="QC_VIDEO_STREAM_DURATION_SANITY", status=QCStatus.FAIL,
            message="Reported duration is implausibly larger than expected",
            measured_value=f"{probe.duration_ms}ms", expected_value=f"<= {ceiling_ms}ms",
        )
    return QCCheckResult(
        check_id="QC_VIDEO_STREAM_DURATION_SANITY", status=QCStatus.PASS,
        message="Reported duration is within a sane range",
        measured_value=f"{probe.duration_ms}ms", expected_value=f"0 < duration <= {ceiling_ms}ms",
    )


# ---------------------------------------------------------------------------
# Visual sampling (requirements #10-11)
# ---------------------------------------------------------------------------


def check_black_frames(luminances: list[float], threshold: float, fraction_threshold: float) -> QCCheckResult:
    """FAIL only when the FRACTION of sampled frames below
    `threshold` meets or exceeds `fraction_threshold` (default 1.0 --
    ALL sampled frames). A single intentionally-dark frame must never
    fail the whole video (requirement #10)."""
    black_count = sum(1 for value in luminances if value <= threshold)
    fraction = black_count / len(luminances) if luminances else 0.0
    ok = fraction < fraction_threshold
    return QCCheckResult(
        check_id="QC_VIDEO_BLACK",
        status=QCStatus.PASS if ok else QCStatus.FAIL,
        message="Sampled frames are not all black" if ok else "All sampled frames are effectively black",
        measured_value=f"{black_count}/{len(luminances)} black (mean luminances: "
        f"{[round(v, 1) for v in luminances]})",
        expected_value=f"< {fraction_threshold * 100:.0f}% black",
    )


def check_frozen_frames(differences: list[float], threshold: float) -> QCCheckResult:
    """WARN (never FAIL) when every consecutive sampled-frame pair is
    effectively identical -- this channel intentionally uses limited-
    motion/static content, so whole-program sameness deserves a human
    look, not an automatic rejection (requirements #10-11)."""
    if not differences:
        return QCCheckResult(
            check_id="QC_VIDEO_FROZEN", status=QCStatus.PASS,
            message="Not enough samples to compare (single-frame sample)",
            measured_value="n/a", expected_value=f"< {threshold}",
        )
    all_identical = all(value <= threshold for value in differences)
    return QCCheckResult(
        check_id="QC_VIDEO_FROZEN",
        status=QCStatus.WARN if all_identical else QCStatus.PASS,
        message=(
            "Every sampled frame is effectively identical -- confirm this static "
            "presentation is intentional" if all_identical else "Sampled frames show visual change"
        ),
        measured_value=f"differences={[round(v, 1) for v in differences]}",
        expected_value=f">= {threshold} for at least one adjacent pair",
    )


# ---------------------------------------------------------------------------
# Audio (requirements #12-13)
# ---------------------------------------------------------------------------


def check_audio_silence(mean_volume_db: float, threshold_db: float) -> QCCheckResult:
    ok = mean_volume_db > threshold_db
    return QCCheckResult(
        check_id="QC_AUDIO_SILENCE",
        status=QCStatus.PASS if ok else QCStatus.FAIL,
        message="Audio is not effectively silent" if ok else "Audio is effectively silent for the entire program",
        measured_value=f"{mean_volume_db:.1f} dB", expected_value=f"> {threshold_db} dB",
    )


def check_audio_clipping(max_volume_db: float, threshold_db: float) -> QCCheckResult:
    ok = max_volume_db < threshold_db
    return QCCheckResult(
        check_id="QC_AUDIO_CLIPPING",
        status=QCStatus.PASS if ok else QCStatus.FAIL,
        message="No clipping detected" if ok else "Peak volume at or above 0 dBFS -- likely clipped",
        measured_value=f"{max_volume_db:.1f} dB", expected_value=f"< {threshold_db} dB",
    )


# ---------------------------------------------------------------------------
# Subtitles (requirements #14-15)
# ---------------------------------------------------------------------------


def check_subtitle_file_exists(srt_path: Path) -> QCCheckResult:
    exists = srt_path.is_file()
    return QCCheckResult(
        check_id="QC_SUBTITLE_EXISTS",
        status=QCStatus.PASS if exists else QCStatus.FAIL,
        message="Subtitle file exists" if exists else f"Subtitle file not found: {srt_path}",
        measured_value=str(exists), expected_value="True",
    )


def check_subtitle_utf8_readable(srt_path: Path) -> tuple[QCCheckResult, str | None]:
    """Returns (check, decoded_text_or_None) -- the decoded text is
    reused by the caller for the remaining subtitle checks rather than
    re-reading the file from disk repeatedly."""
    try:
        text = srt_path.read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError) as exc:
        return (
            QCCheckResult(
                check_id="QC_SUBTITLE_UTF8", status=QCStatus.FAIL,
                message=f"Subtitle file could not be read as UTF-8: {exc}",
                measured_value="unreadable", expected_value="valid UTF-8",
            ),
            None,
        )
    return (
        QCCheckResult(
            check_id="QC_SUBTITLE_UTF8", status=QCStatus.PASS, message="Subtitle file is valid UTF-8",
            measured_value="valid UTF-8", expected_value="valid UTF-8",
        ),
        text,
    )


def check_subtitle_cue_count_match(parsed_cues: list[ParsedSrtCue], expected_count: int) -> QCCheckResult:
    ok = len(parsed_cues) == expected_count
    return QCCheckResult(
        check_id="QC_SUBTITLE_CUE_COUNT",
        status=QCStatus.PASS if ok else QCStatus.FAIL,
        message="Cue count matches CaptionManifest" if ok else "Cue count mismatch",
        measured_value=str(len(parsed_cues)), expected_value=str(expected_count),
    )


def check_subtitle_timestamps_valid(parsed_cues: list[ParsedSrtCue]) -> QCCheckResult:
    problems = []
    previous_end_ms = None
    for cue in parsed_cues:
        if cue.start_ms < 0:
            problems.append(f"cue {cue.index}: negative start_ms")
        if cue.end_ms <= cue.start_ms:
            problems.append(f"cue {cue.index}: end_ms not greater than start_ms")
        if previous_end_ms is not None and cue.start_ms < previous_end_ms:
            problems.append(f"cue {cue.index}: starts before the previous cue ends")
        previous_end_ms = cue.end_ms

    ok = not problems
    return QCCheckResult(
        check_id="QC_SUBTITLE_TIMESTAMPS_VALID",
        status=QCStatus.PASS if ok else QCStatus.FAIL,
        message="All cue timestamps are valid and chronological" if ok else "; ".join(problems),
        measured_value=f"{len(parsed_cues)} cues checked", expected_value="chronological, non-overlapping",
    )


def check_subtitle_timeline_bounds(parsed_cues: list[ParsedSrtCue], total_duration_ms: int) -> QCCheckResult:
    out_of_bounds = [cue.index for cue in parsed_cues if cue.end_ms > total_duration_ms]
    ok = not out_of_bounds
    return QCCheckResult(
        check_id="QC_SUBTITLE_TIMELINE_BOUNDS",
        status=QCStatus.PASS if ok else QCStatus.FAIL,
        message="All cues stay within the timeline duration" if ok else f"Cue(s) {out_of_bounds} end beyond total_duration_ms",
        measured_value=f"total_duration_ms={total_duration_ms}", expected_value="every cue.end_ms <= total_duration_ms",
    )


def check_subtitle_text_nonempty(parsed_cues: list[ParsedSrtCue]) -> QCCheckResult:
    empty = [cue.index for cue in parsed_cues if not cue.text.strip()]
    ok = not empty
    return QCCheckResult(
        check_id="QC_SUBTITLE_TEXT_NONEMPTY",
        status=QCStatus.PASS if ok else QCStatus.FAIL,
        message="No empty cue text" if ok else f"Cue(s) {empty} have empty/blank text",
        measured_value=f"{len(empty)} empty", expected_value="0 empty",
    )


def check_subtitle_exact_match(actual_text: str, expected_text: str) -> QCCheckResult:
    """Requirement #15: the on-disk SRT must be byte-for-byte the same
    text `render_srt(caption_manifest)` would produce right now -- this
    catches a stale/wrong subtitle artifact directly, independent of the
    more granular structural checks above."""
    ok = actual_text == expected_text
    return QCCheckResult(
        check_id="QC_SUBTITLE_MATCH",
        status=QCStatus.PASS if ok else QCStatus.FAIL,
        message="SRT content exactly matches the current CaptionManifest" if ok else "SRT content does not match the current CaptionManifest",
        measured_value=f"{len(actual_text)} chars on disk", expected_value=f"{len(expected_text)} chars expected",
    )
