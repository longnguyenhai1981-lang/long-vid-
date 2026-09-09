"""Phase 31 focused tests: CaptionCue/CaptionManifest contract validation
(app/models/caption.py).

Pure pydantic-level tests -- no filesystem I/O, no subprocess.
"""

from __future__ import annotations

from datetime import datetime, timezone
from uuid import uuid4

import pytest
from pydantic import ValidationError

from app.models.caption import CaptionCue, CaptionManifest


def _cue(**overrides) -> CaptionCue:
    fields = dict(
        start_ms=0, end_ms=1000, duration_ms=1000, text="Xin chào.",
        script_line_ids=["L001"], voice_chunk_id="C001", render_job_id="C001_T1",
    )
    fields.update(overrides)
    return CaptionCue(**fields)


def _manifest(cues, **overrides) -> CaptionManifest:
    fields = dict(
        project_id=uuid4(), timeline_manifest_id=uuid4(), script_plan_id=uuid4(),
        voice_plan_id=uuid4(), voice_render_manifest_id=uuid4(), total_duration_ms=5000,
        cues=cues, created_at=datetime.now(timezone.utc),
    )
    fields.update(overrides)
    return CaptionManifest(**fields)


# ---------------------------------------------------------------------------
# CaptionCue
# ---------------------------------------------------------------------------


def test_valid_caption_cue():
    cue = _cue()
    assert cue.text == "Xin chào."
    assert cue.duration_ms == 1000


def test_blank_text_rejected():
    with pytest.raises(ValidationError):
        _cue(text="")
    with pytest.raises(ValidationError):
        _cue(text="   ")


def test_negative_start_ms_rejected():
    with pytest.raises(ValidationError):
        _cue(start_ms=-1, end_ms=1000, duration_ms=1001)


def test_end_ms_not_greater_than_start_ms_rejected():
    with pytest.raises(ValidationError):
        _cue(start_ms=1000, end_ms=1000, duration_ms=0)
    with pytest.raises(ValidationError):
        _cue(start_ms=1000, end_ms=500, duration_ms=-500)


def test_duration_mismatch_rejected():
    with pytest.raises(ValidationError):
        _cue(start_ms=0, end_ms=1000, duration_ms=999)


def test_blank_voice_chunk_id_rejected():
    with pytest.raises(ValidationError):
        _cue(voice_chunk_id="  ")


def test_empty_script_line_ids_rejected():
    with pytest.raises(ValidationError):
        _cue(script_line_ids=[])


def test_vietnamese_text_preserved_exactly():
    text = "Đây là một câu tiếng Việt đầy đủ dấu: ệ ị ọ ữ ẫ ẳ ố ơ."
    cue = _cue(text=text)
    assert cue.text == text


# ---------------------------------------------------------------------------
# CaptionManifest
# ---------------------------------------------------------------------------


def test_valid_manifest_single_cue():
    manifest = _manifest([_cue(start_ms=0, end_ms=1000, duration_ms=1000)], total_duration_ms=1000)
    assert len(manifest.cues) == 1


def test_valid_manifest_chronological_touching_cues():
    cues = [
        _cue(start_ms=0, end_ms=1000, duration_ms=1000),
        _cue(start_ms=1000, end_ms=2500, duration_ms=1500),
    ]
    manifest = _manifest(cues, total_duration_ms=2500)
    assert len(manifest.cues) == 2


def test_manifest_requires_at_least_one_cue():
    with pytest.raises(ValidationError):
        _manifest([])


def test_cue_outside_manifest_duration_rejected():
    cues = [_cue(start_ms=0, end_ms=1000, duration_ms=1000)]
    with pytest.raises(ValidationError):
        _manifest(cues, total_duration_ms=500)


def test_duplicate_cue_id_rejected():
    shared_id = uuid4()
    cues = [
        _cue(id=shared_id, start_ms=0, end_ms=1000, duration_ms=1000),
        _cue(id=shared_id, start_ms=1000, end_ms=2000, duration_ms=1000),
    ]
    with pytest.raises(ValidationError):
        _manifest(cues, total_duration_ms=2000)


def test_out_of_chronological_order_rejected():
    cues = [
        _cue(start_ms=2000, end_ms=3000, duration_ms=1000),
        _cue(start_ms=0, end_ms=1000, duration_ms=1000),
    ]
    with pytest.raises(ValidationError):
        _manifest(cues, total_duration_ms=3000)


def test_overlapping_cues_rejected():
    cues = [
        _cue(start_ms=0, end_ms=1500, duration_ms=1500),
        _cue(start_ms=1000, end_ms=2000, duration_ms=1000),
    ]
    with pytest.raises(ValidationError):
        _manifest(cues, total_duration_ms=2000)


def test_non_timezone_aware_created_at_rejected():
    with pytest.raises(ValidationError):
        _manifest([_cue()], total_duration_ms=1000, created_at=datetime(2024, 1, 1))


def test_non_positive_total_duration_rejected():
    with pytest.raises(ValidationError):
        _manifest([_cue()], total_duration_ms=0)
