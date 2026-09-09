"""MediaQCInspector: orchestrates every app/media_qc/rules.py check into
one MediaQCReport (Phase 32).

QC is diagnostic, never fail-fast (requirement #23): `inspect()` always
returns a complete MediaQCReport rather than raising for an ordinary
media defect -- a missing file, a corrupt container, a codec/duration/
canvas mismatch, silence, or a stale-looking sample are all represented
as FAIL/WARN QCCheckResults, never exceptions. The only things that DO
raise out of `inspect()` are genuine infrastructure failures (no usable
ffmpeg/ffprobe at all, a temp directory that cannot be created) -- see
app/media_qc/errors.py's own module docstring for the exact boundary.

Checks that depend on a successful ffprobe (codec/fps/canvas/duration/
etc.) are simply skipped once the container itself could not be read --
there is nothing more to determine, and re-reporting the same "unreadable"
fact under a dozen different check_ids would be noise, not diagnosis.
Visual sampling and audio analysis are each skipped independently when
their own required stream (video/audio) is absent, since Phase 32
requirement #4's own QC_VIDEO_STREAM_PRESENT/QC_AUDIO_PRESENT checks
already report that absence directly.
"""

from __future__ import annotations

from datetime import datetime, timezone

from app.captions.srt import SrtParseError, parse_srt, render_srt
from app.media_qc.errors import MediaProbeError
from app.media_qc.models import MediaQCReport, MediaQCRequest, MediaQCSettings, QCCheckResult, QCStatus
from app.media_qc.probes import AudioAnalyzer, FFprobeClient, FrameSampler
from app.media_qc.rules import (
    check_audio_clipping,
    check_audio_codec,
    check_audio_silence,
    check_audio_stream_present,
    check_black_frames,
    check_canvas,
    check_duration,
    check_frozen_frames,
    check_fps,
    check_pixel_format,
    check_stream_duration_sanity,
    check_subtitle_cue_count_match,
    check_subtitle_exact_match,
    check_subtitle_file_exists,
    check_subtitle_text_nonempty,
    check_subtitle_timeline_bounds,
    check_subtitle_timestamps_valid,
    check_subtitle_utf8_readable,
    check_video_codec,
    check_video_decodable,
    check_video_file_exists,
    check_video_file_nonzero,
    check_video_stream_present,
)


class MediaQCInspector:
    def __init__(
        self,
        *,
        ffprobe_client: FFprobeClient | None = None,
        frame_sampler: FrameSampler | None = None,
        audio_analyzer: AudioAnalyzer | None = None,
        settings: MediaQCSettings | None = None,
    ):
        self._ffprobe_client = ffprobe_client if ffprobe_client is not None else FFprobeClient()
        self._frame_sampler = frame_sampler if frame_sampler is not None else FrameSampler()
        self._audio_analyzer = audio_analyzer if audio_analyzer is not None else AudioAnalyzer()
        self._settings = settings if settings is not None else MediaQCSettings()

    def inspect(self, request: MediaQCRequest) -> MediaQCReport:
        checks: list[QCCheckResult] = []

        exists_check = check_video_file_exists(request.video_path)
        checks.append(exists_check)
        if exists_check.status is QCStatus.FAIL:
            return self._finalize(request, checks)

        nonzero_check = check_video_file_nonzero(request.video_path)
        checks.append(nonzero_check)
        if nonzero_check.status is QCStatus.FAIL:
            return self._finalize(request, checks)

        try:
            probe = self._ffprobe_client.probe(request.video_path)
        except MediaProbeError as exc:
            checks.append(check_video_decodable(None, str(exc)))
            return self._finalize(request, checks)
        checks.append(check_video_decodable(probe, None))

        checks.append(check_video_stream_present(probe))
        checks.append(check_audio_stream_present(probe))
        checks.append(check_video_codec(probe, request.expected_video_codec))
        checks.append(check_audio_codec(probe, request.expected_audio_codec))
        checks.append(check_pixel_format(probe, request.expected_pixel_format))
        checks.append(check_fps(probe, request.expected_fps, self._settings.fps_tolerance))
        checks.append(check_canvas(probe, request.expected_width, request.expected_height))
        checks.append(check_duration(probe, request.expected_duration_ms, self._settings.duration_tolerance_ms))
        checks.append(check_stream_duration_sanity(probe, request.expected_duration_ms))

        if probe.video_stream_count >= 1:
            checks.extend(self._run_visual_sampling(request, probe.duration_ms))

        if probe.audio_stream_count >= 1:
            checks.extend(self._run_audio_analysis(request))

        if request.subtitle_path is not None and request.caption_manifest is not None:
            checks.extend(self._run_subtitle_checks(request))

        return self._finalize(request, checks)

    def _run_visual_sampling(self, request: MediaQCRequest, probed_duration_ms: int) -> list[QCCheckResult]:
        duration_ms = probed_duration_ms if probed_duration_ms > 0 else request.expected_duration_ms
        try:
            luminances, differences = self._frame_sampler.sample_luminances_and_differences(
                request.video_path, duration_ms, self._settings.sample_frame_count
            )
        except MediaProbeError as exc:
            message = f"Could not sample frames for visual inspection: {exc}"
            return [
                QCCheckResult(check_id="QC_VIDEO_BLACK", status=QCStatus.FAIL, message=message),
                QCCheckResult(check_id="QC_VIDEO_FROZEN", status=QCStatus.FAIL, message=message),
            ]
        return [
            check_black_frames(
                luminances, self._settings.black_luminance_threshold, self._settings.black_frame_fraction_threshold
            ),
            check_frozen_frames(differences, self._settings.frozen_frame_difference_threshold),
        ]

    def _run_audio_analysis(self, request: MediaQCRequest) -> list[QCCheckResult]:
        try:
            mean_volume_db, max_volume_db = self._audio_analyzer.analyze(request.video_path)
        except MediaProbeError as exc:
            message = f"Could not analyze audio: {exc}"
            return [QCCheckResult(check_id="QC_AUDIO_SILENCE", status=QCStatus.FAIL, message=message)]
        return [
            check_audio_silence(mean_volume_db, self._settings.silence_threshold_db),
            check_audio_clipping(max_volume_db, self._settings.clipping_threshold_db),
        ]

    @staticmethod
    def _run_subtitle_checks(request: MediaQCRequest) -> list[QCCheckResult]:
        checks: list[QCCheckResult] = []
        srt_path = request.subtitle_path
        caption_manifest = request.caption_manifest

        exists_check = check_subtitle_file_exists(srt_path)
        checks.append(exists_check)
        if exists_check.status is QCStatus.FAIL:
            return checks

        utf8_check, text = check_subtitle_utf8_readable(srt_path)
        checks.append(utf8_check)
        if text is None:
            return checks

        try:
            parsed_cues = parse_srt(text)
        except SrtParseError as exc:
            checks.append(
                QCCheckResult(
                    check_id="QC_SUBTITLE_TIMESTAMPS_VALID", status=QCStatus.FAIL,
                    message=f"SRT could not be parsed: {exc}",
                )
            )
            return checks

        checks.append(check_subtitle_cue_count_match(parsed_cues, len(caption_manifest.cues)))
        checks.append(check_subtitle_timestamps_valid(parsed_cues))
        checks.append(check_subtitle_timeline_bounds(parsed_cues, caption_manifest.total_duration_ms))
        checks.append(check_subtitle_text_nonempty(parsed_cues))
        checks.append(check_subtitle_exact_match(text, render_srt(caption_manifest)))
        return checks

    @staticmethod
    def _finalize(request: MediaQCRequest, checks: list[QCCheckResult]) -> MediaQCReport:
        return MediaQCReport.build(
            project_id=request.project_id,
            source_video_asset_id=request.source_video_asset_id,
            timeline_manifest_id=request.timeline_manifest_id,
            caption_manifest_id=request.caption_manifest.id if request.caption_manifest is not None else None,
            subtitle_file_asset_id=request.subtitle_file_asset_id,
            checks=checks,
            created_at=datetime.now(timezone.utc),
        )
