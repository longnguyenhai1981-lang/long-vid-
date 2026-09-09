"""Phase 32 focused tests: QCCheckResult/MediaQCReport/MediaQCSettings
contract validation (app/media_qc/models.py).

Pure pydantic-level tests -- no filesystem I/O, no subprocess.
"""

from __future__ import annotations

from datetime import datetime, timezone
from uuid import uuid4

import pytest
from pydantic import ValidationError

from app.media_qc.models import MediaQCReport, MediaQCSettings, QCCheckResult, QCStatus


def _check(**overrides) -> QCCheckResult:
    fields = dict(check_id="QC_TEST", status=QCStatus.PASS, message="ok")
    fields.update(overrides)
    return QCCheckResult(**fields)


def _report(checks, **overrides) -> MediaQCReport:
    fields = dict(
        project_id=uuid4(), source_video_asset_id=uuid4(), timeline_manifest_id=uuid4(),
        checks=checks, created_at=datetime.now(timezone.utc),
    )
    fields.update(overrides)
    if "overall_status" not in fields:
        return MediaQCReport.build(**fields)
    return MediaQCReport(**fields)


# ---------------------------------------------------------------------------
# QCCheckResult
# ---------------------------------------------------------------------------


def test_check_result_pass():
    check = _check(status=QCStatus.PASS)
    assert check.status is QCStatus.PASS


def test_check_result_warn():
    check = _check(status=QCStatus.WARN)
    assert check.status is QCStatus.WARN


def test_check_result_fail():
    check = _check(status=QCStatus.FAIL)
    assert check.status is QCStatus.FAIL


def test_check_result_blank_check_id_rejected():
    with pytest.raises(ValidationError):
        _check(check_id="  ")


def test_check_result_blank_message_rejected():
    with pytest.raises(ValidationError):
        _check(message="")


def test_check_result_optional_fields_default_none():
    check = _check()
    assert check.measured_value is None
    assert check.expected_value is None
    assert check.details is None


# ---------------------------------------------------------------------------
# MediaQCReport: overall_status derivation
# ---------------------------------------------------------------------------


def test_pass_only_report_derives_pass():
    report = _report([_check(check_id="QC_A", status=QCStatus.PASS), _check(check_id="QC_B", status=QCStatus.PASS)])
    assert report.overall_status is QCStatus.PASS


def test_warn_dominates_pass():
    report = _report([_check(check_id="QC_A", status=QCStatus.PASS), _check(check_id="QC_B", status=QCStatus.WARN)])
    assert report.overall_status is QCStatus.WARN


def test_fail_dominates_warn_and_pass():
    report = _report(
        [
            _check(check_id="QC_A", status=QCStatus.PASS),
            _check(check_id="QC_B", status=QCStatus.WARN),
            _check(check_id="QC_C", status=QCStatus.FAIL),
        ]
    )
    assert report.overall_status is QCStatus.FAIL


def test_single_fail_check_yields_fail_report():
    report = _report([_check(check_id="QC_A", status=QCStatus.FAIL)])
    assert report.overall_status is QCStatus.FAIL


def test_caller_cannot_set_inconsistent_overall_status():
    with pytest.raises(ValidationError):
        _report(
            [_check(check_id="QC_A", status=QCStatus.FAIL)],
            overall_status=QCStatus.PASS,
        )


def test_report_requires_at_least_one_check():
    with pytest.raises(ValidationError):
        _report([])


def test_report_non_timezone_aware_created_at_rejected():
    with pytest.raises(ValidationError):
        _report([_check()], created_at=datetime(2024, 1, 1))


# ---------------------------------------------------------------------------
# ready_for_human_review policy (requirement #26)
# ---------------------------------------------------------------------------


def test_ready_for_human_review_true_on_pass():
    report = _report([_check(status=QCStatus.PASS)])
    assert report.ready_for_human_review is True


def test_ready_for_human_review_true_on_warn():
    report = _report([_check(status=QCStatus.WARN)])
    assert report.ready_for_human_review is True


def test_ready_for_human_review_false_on_fail():
    report = _report([_check(status=QCStatus.FAIL)])
    assert report.ready_for_human_review is False


# ---------------------------------------------------------------------------
# Stable rule ids (requirement #22) -- a lightweight smoke check that the
# documented ids are exactly what app/media_qc/rules.py produces.
# ---------------------------------------------------------------------------


def test_stable_rule_ids_used_by_rules_module():
    import app.media_qc.rules as rules_module

    expected_ids = {
        "QC_VIDEO_EXISTS", "QC_VIDEO_NONZERO", "QC_VIDEO_DECODE", "QC_VIDEO_STREAM_PRESENT",
        "QC_AUDIO_PRESENT", "QC_VIDEO_CODEC", "QC_AUDIO_CODEC", "QC_VIDEO_PIXFMT",
        "QC_VIDEO_FPS", "QC_VIDEO_CANVAS", "QC_VIDEO_DURATION", "QC_VIDEO_STREAM_DURATION_SANITY",
        "QC_VIDEO_BLACK", "QC_VIDEO_FROZEN", "QC_AUDIO_SILENCE", "QC_AUDIO_CLIPPING",
        "QC_SUBTITLE_EXISTS", "QC_SUBTITLE_UTF8", "QC_SUBTITLE_CUE_COUNT",
        "QC_SUBTITLE_TIMESTAMPS_VALID", "QC_SUBTITLE_TIMELINE_BOUNDS", "QC_SUBTITLE_TEXT_NONEMPTY",
        "QC_SUBTITLE_MATCH",
    }
    with open(rules_module.__file__, encoding="utf-8") as f:
        source = f.read()
    for rule_id in expected_ids:
        assert f'"{rule_id}"' in source, f"{rule_id} not found in rules.py"


# ---------------------------------------------------------------------------
# MediaQCSettings validation
# ---------------------------------------------------------------------------


def test_settings_defaults():
    settings = MediaQCSettings()
    assert settings.duration_tolerance_ms == 100
    assert settings.fps_tolerance == 0.01
    assert settings.sample_frame_count == 5


def test_settings_negative_duration_tolerance_rejected():
    with pytest.raises(ValidationError):
        MediaQCSettings(duration_tolerance_ms=-1)


def test_settings_non_positive_fps_tolerance_rejected():
    with pytest.raises(ValidationError):
        MediaQCSettings(fps_tolerance=0)


def test_settings_black_luminance_threshold_out_of_range_rejected():
    with pytest.raises(ValidationError):
        MediaQCSettings(black_luminance_threshold=-1)
    with pytest.raises(ValidationError):
        MediaQCSettings(black_luminance_threshold=256)


def test_settings_black_frame_fraction_threshold_out_of_range_rejected():
    with pytest.raises(ValidationError):
        MediaQCSettings(black_frame_fraction_threshold=-0.1)
    with pytest.raises(ValidationError):
        MediaQCSettings(black_frame_fraction_threshold=1.1)


def test_settings_negative_frozen_threshold_rejected():
    with pytest.raises(ValidationError):
        MediaQCSettings(frozen_frame_difference_threshold=-1)


def test_settings_non_positive_sample_count_rejected():
    with pytest.raises(ValidationError):
        MediaQCSettings(sample_frame_count=0)


def test_settings_custom_values_accepted():
    settings = MediaQCSettings(duration_tolerance_ms=50, fps_tolerance=0.5, sample_frame_count=3)
    assert settings.duration_tolerance_ms == 50
    assert settings.sample_frame_count == 3
