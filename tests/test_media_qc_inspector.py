"""Phase 32 focused tests: MediaQCInspector orchestration
(app/media_qc/inspector.py).

Uses fake/injected collaborator objects (FFprobeClient/FrameSampler/
AudioAnalyzer substitutes) throughout -- real ffmpeg is never invoked in
this file (see tests/test_media_qc_integration.py for the opt-in
real-ffmpeg tests). Exercises the "diagnostic, never fail-fast"
orchestration logic: skip downstream checks once a prerequisite is
unavailable, but always return a complete report, never raise for an
ordinary media defect.
"""

from __future__ import annotations

from datetime import datetime, timezone
from uuid import uuid4

from app.captions.srt import render_srt
from app.media_qc.errors import MediaProbeError
from app.media_qc.inspector import MediaQCInspector
from app.media_qc.models import MediaQCRequest, QCStatus
from app.media_qc.probes import MediaProbeResult
from app.models.caption import CaptionCue, CaptionManifest


class _FakeFFprobeClient:
    def __init__(self, result=None, error=None):
        self._result = result
        self._error = error
        self.calls = 0

    def probe(self, media_path):
        self.calls += 1
        if self._error is not None:
            raise self._error
        return self._result


class _FakeFrameSampler:
    def __init__(self, result=None, error=None):
        self._result = result
        self._error = error
        self.calls = 0

    def sample_luminances_and_differences(self, video_path, duration_ms, sample_count):
        self.calls += 1
        if self._error is not None:
            raise self._error
        return self._result


class _FakeAudioAnalyzer:
    def __init__(self, result=None, error=None):
        self._result = result
        self._error = error
        self.calls = 0

    def analyze(self, media_path):
        self.calls += 1
        if self._error is not None:
            raise self._error
        return self._result


def _probe(**overrides) -> MediaProbeResult:
    fields = dict(
        duration_ms=6000, width=640, height=360, fps=30.0, video_codec="h264",
        audio_codec="aac", pixel_format="yuv420p", video_stream_count=1, audio_stream_count=1,
    )
    fields.update(overrides)
    return MediaProbeResult(**fields)


def _request(tmp_path, **overrides) -> MediaQCRequest:
    if "video_path" not in overrides:
        video_path = tmp_path / "video.mp4"
        video_path.write_bytes(b"x" * 100)
        overrides = {**overrides, "video_path": video_path}
    fields = dict(
        project_id=uuid4(), source_video_asset_id=uuid4(), timeline_manifest_id=uuid4(),
        expected_video_codec="h264", expected_audio_codec="aac",
        expected_pixel_format="yuv420p", expected_fps=30.0, expected_width=640,
        expected_height=360, expected_duration_ms=6000,
    )
    fields.update(overrides)
    return MediaQCRequest(**fields)


def _healthy_inspector() -> MediaQCInspector:
    return MediaQCInspector(
        ffprobe_client=_FakeFFprobeClient(result=_probe()),
        frame_sampler=_FakeFrameSampler(result=([80.0, 82.0, 60.0, 90.0, 70.0], [2.0, 20.0, 30.0, 20.0])),
        audio_analyzer=_FakeAudioAnalyzer(result=(-21.0, -18.0)),
    )


def _cue(**overrides) -> CaptionCue:
    fields = dict(
        start_ms=0, end_ms=1000, duration_ms=1000, text="Xin chào.",
        script_line_ids=["L001"], voice_chunk_id="C001", render_job_id="C001_T1",
    )
    fields.update(overrides)
    return CaptionCue(**fields)


def _caption_manifest(**overrides) -> CaptionManifest:
    fields = dict(
        project_id=uuid4(), timeline_manifest_id=uuid4(), script_plan_id=uuid4(),
        voice_plan_id=uuid4(), voice_render_manifest_id=uuid4(), total_duration_ms=6000,
        cues=[_cue()], created_at=datetime.now(timezone.utc),
    )
    fields.update(overrides)
    return CaptionManifest(**fields)


# ---------------------------------------------------------------------------
# Full happy path
# ---------------------------------------------------------------------------


def test_healthy_request_produces_pass_report(tmp_path):
    request = _request(tmp_path)
    report = _healthy_inspector().inspect(request)
    assert report.overall_status is QCStatus.PASS
    assert report.ready_for_human_review is True
    check_ids = {check.check_id for check in report.checks}
    assert {
        "QC_VIDEO_EXISTS", "QC_VIDEO_NONZERO", "QC_VIDEO_DECODE", "QC_VIDEO_STREAM_PRESENT",
        "QC_AUDIO_PRESENT", "QC_VIDEO_CODEC", "QC_AUDIO_CODEC", "QC_VIDEO_PIXFMT",
        "QC_VIDEO_FPS", "QC_VIDEO_CANVAS", "QC_VIDEO_DURATION", "QC_VIDEO_STREAM_DURATION_SANITY",
        "QC_VIDEO_BLACK", "QC_VIDEO_FROZEN", "QC_AUDIO_SILENCE", "QC_AUDIO_CLIPPING",
    }.issubset(check_ids)


def test_report_ids_match_request(tmp_path):
    request = _request(tmp_path)
    report = _healthy_inspector().inspect(request)
    assert report.project_id == request.project_id
    assert report.source_video_asset_id == request.source_video_asset_id
    assert report.timeline_manifest_id == request.timeline_manifest_id
    assert report.caption_manifest_id is None
    assert report.subtitle_file_asset_id is None


# ---------------------------------------------------------------------------
# Diagnostic, never fail-fast
# ---------------------------------------------------------------------------


def test_missing_video_produces_fail_report_not_exception(tmp_path):
    request = _request(tmp_path, video_path=tmp_path / "missing.mp4")
    inspector = _healthy_inspector()
    report = inspector.inspect(request)  # must not raise
    assert report.overall_status is QCStatus.FAIL
    assert report.checks[0].check_id == "QC_VIDEO_EXISTS"
    assert len(report.checks) == 1  # no downstream checks attempted


def test_zero_byte_video_stops_before_probe(tmp_path):
    video_path = tmp_path / "video.mp4"
    video_path.write_bytes(b"")
    ffprobe = _FakeFFprobeClient(result=_probe())
    inspector = MediaQCInspector(
        ffprobe_client=ffprobe, frame_sampler=_FakeFrameSampler(), audio_analyzer=_FakeAudioAnalyzer()
    )
    report = inspector.inspect(_request(tmp_path, video_path=video_path))
    assert report.overall_status is QCStatus.FAIL
    assert ffprobe.calls == 0  # never even attempted to probe


def test_probe_failure_produces_fail_report_no_downstream_checks(tmp_path):
    inspector = MediaQCInspector(
        ffprobe_client=_FakeFFprobeClient(error=MediaProbeError("corrupt")),
        frame_sampler=_FakeFrameSampler(), audio_analyzer=_FakeAudioAnalyzer(),
    )
    report = inspector.inspect(_request(tmp_path))
    assert report.overall_status is QCStatus.FAIL
    check_ids = [check.check_id for check in report.checks]
    assert check_ids == ["QC_VIDEO_EXISTS", "QC_VIDEO_NONZERO", "QC_VIDEO_DECODE"]


def test_no_video_stream_skips_visual_sampling(tmp_path):
    frame_sampler = _FakeFrameSampler(result=([80.0] * 5, [10.0] * 4))
    inspector = MediaQCInspector(
        ffprobe_client=_FakeFFprobeClient(result=_probe(video_stream_count=0, video_codec=None)),
        frame_sampler=frame_sampler, audio_analyzer=_FakeAudioAnalyzer(result=(-21.0, -18.0)),
    )
    report = inspector.inspect(_request(tmp_path))
    assert frame_sampler.calls == 0
    check_ids = {check.check_id for check in report.checks}
    assert "QC_VIDEO_BLACK" not in check_ids
    assert "QC_VIDEO_FROZEN" not in check_ids
    assert report.overall_status is QCStatus.FAIL  # QC_VIDEO_STREAM_PRESENT fails


def test_no_audio_stream_skips_audio_analysis(tmp_path):
    audio_analyzer = _FakeAudioAnalyzer(result=(-21.0, -18.0))
    inspector = MediaQCInspector(
        ffprobe_client=_FakeFFprobeClient(result=_probe(audio_stream_count=0, audio_codec=None)),
        frame_sampler=_FakeFrameSampler(result=([80.0] * 5, [10.0] * 4)), audio_analyzer=audio_analyzer,
    )
    report = inspector.inspect(_request(tmp_path))
    assert audio_analyzer.calls == 0
    check_ids = {check.check_id for check in report.checks}
    assert "QC_AUDIO_SILENCE" not in check_ids
    assert "QC_AUDIO_CLIPPING" not in check_ids


def test_frame_sampling_failure_yields_fail_not_exception(tmp_path):
    inspector = MediaQCInspector(
        ffprobe_client=_FakeFFprobeClient(result=_probe()),
        frame_sampler=_FakeFrameSampler(error=MediaProbeError("could not extract frame")),
        audio_analyzer=_FakeAudioAnalyzer(result=(-21.0, -18.0)),
    )
    report = inspector.inspect(_request(tmp_path))  # must not raise
    black_check = next(c for c in report.checks if c.check_id == "QC_VIDEO_BLACK")
    frozen_check = next(c for c in report.checks if c.check_id == "QC_VIDEO_FROZEN")
    assert black_check.status is QCStatus.FAIL
    assert frozen_check.status is QCStatus.FAIL


def test_audio_analysis_failure_yields_fail_not_exception(tmp_path):
    inspector = MediaQCInspector(
        ffprobe_client=_FakeFFprobeClient(result=_probe()),
        frame_sampler=_FakeFrameSampler(result=([80.0] * 5, [10.0] * 4)),
        audio_analyzer=_FakeAudioAnalyzer(error=MediaProbeError("could not decode audio")),
    )
    report = inspector.inspect(_request(tmp_path))  # must not raise
    silence_check = next(c for c in report.checks if c.check_id == "QC_AUDIO_SILENCE")
    assert silence_check.status is QCStatus.FAIL


# ---------------------------------------------------------------------------
# Subtitle checks
# ---------------------------------------------------------------------------


def test_subtitle_checks_included_when_requested(tmp_path):
    caption_manifest = _caption_manifest()
    srt_path = tmp_path / "captions.srt"
    srt_path.write_text(render_srt(caption_manifest), encoding="utf-8")

    request = _request(
        tmp_path, subtitle_path=srt_path, caption_manifest=caption_manifest,
        subtitle_file_asset_id=uuid4(),
    )
    report = _healthy_inspector().inspect(request)
    check_ids = {check.check_id for check in report.checks}
    assert {
        "QC_SUBTITLE_EXISTS", "QC_SUBTITLE_UTF8", "QC_SUBTITLE_CUE_COUNT",
        "QC_SUBTITLE_TIMESTAMPS_VALID", "QC_SUBTITLE_TIMELINE_BOUNDS",
        "QC_SUBTITLE_TEXT_NONEMPTY", "QC_SUBTITLE_MATCH",
    }.issubset(check_ids)
    assert report.overall_status is QCStatus.PASS
    assert report.caption_manifest_id == caption_manifest.id


def test_subtitle_checks_excluded_when_not_requested(tmp_path):
    request = _request(tmp_path)
    report = _healthy_inspector().inspect(request)
    check_ids = {check.check_id for check in report.checks}
    assert not any(check_id.startswith("QC_SUBTITLE") for check_id in check_ids)


def test_missing_subtitle_file_fails(tmp_path):
    caption_manifest = _caption_manifest()
    request = _request(
        tmp_path, subtitle_path=tmp_path / "missing.srt", caption_manifest=caption_manifest,
    )
    report = _healthy_inspector().inspect(request)
    subtitle_check = next(c for c in report.checks if c.check_id == "QC_SUBTITLE_EXISTS")
    assert subtitle_check.status is QCStatus.FAIL
    assert report.overall_status is QCStatus.FAIL


def test_invalid_utf8_subtitle_fails(tmp_path):
    caption_manifest = _caption_manifest()
    srt_path = tmp_path / "captions.srt"
    srt_path.write_bytes(b"\xff\xfe\x00invalid utf-8 bytes\x80\x81")
    request = _request(tmp_path, subtitle_path=srt_path, caption_manifest=caption_manifest)
    report = _healthy_inspector().inspect(request)
    utf8_check = next(c for c in report.checks if c.check_id == "QC_SUBTITLE_UTF8")
    assert utf8_check.status is QCStatus.FAIL


def test_cue_count_mismatch_fails(tmp_path):
    caption_manifest = _caption_manifest(cues=[_cue(), _cue(start_ms=1000, end_ms=2000, duration_ms=1000)], total_duration_ms=2000)
    srt_path = tmp_path / "captions.srt"
    # Write an SRT with only ONE cue -- deliberately mismatched.
    srt_path.write_text(render_srt(_caption_manifest(cues=[_cue()])), encoding="utf-8")
    request = _request(tmp_path, subtitle_path=srt_path, caption_manifest=caption_manifest)
    report = _healthy_inspector().inspect(request)
    cue_count_check = next(c for c in report.checks if c.check_id == "QC_SUBTITLE_CUE_COUNT")
    assert cue_count_check.status is QCStatus.FAIL


def test_stale_subtitle_produces_match_failure(tmp_path):
    """A stale SRT (not regenerated after CaptionManifest changed) fails
    QC_SUBTITLE_MATCH even if individually each cue still parses fine."""
    caption_manifest = _caption_manifest(cues=[_cue(text="Văn bản mới.")])
    stale_manifest = _caption_manifest(cues=[_cue(text="Văn bản cũ.")])
    srt_path = tmp_path / "captions.srt"
    srt_path.write_text(render_srt(stale_manifest), encoding="utf-8")
    request = _request(tmp_path, subtitle_path=srt_path, caption_manifest=caption_manifest)
    report = _healthy_inspector().inspect(request)
    match_check = next(c for c in report.checks if c.check_id == "QC_SUBTITLE_MATCH")
    assert match_check.status is QCStatus.FAIL
    assert report.overall_status is QCStatus.FAIL
