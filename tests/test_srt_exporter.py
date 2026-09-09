"""Phase 31 focused tests: deterministic UTF-8 SRT serialization
(app/captions/srt.py).

Exact string comparisons throughout -- SRT output must be byte-for-byte
deterministic.
"""

from __future__ import annotations

from datetime import datetime, timezone
from uuid import uuid4

from app.captions.srt import render_srt
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
        voice_plan_id=uuid4(), voice_render_manifest_id=uuid4(),
        total_duration_ms=max(cue.end_ms for cue in cues), cues=cues,
        created_at=datetime.now(timezone.utc),
    )
    fields.update(overrides)
    return CaptionManifest(**fields)


def test_one_cue_exact_output():
    manifest = _manifest([_cue(start_ms=0, end_ms=2500, duration_ms=2500, text="Xin chào.")])
    expected = "1\n00:00:00,000 --> 00:00:02,500\nXin chào.\n"
    assert render_srt(manifest) == expected


def test_several_cues_exact_output():
    cues = [
        _cue(start_ms=0, end_ms=1000, duration_ms=1000, text="Câu một."),
        _cue(start_ms=1000, end_ms=2500, duration_ms=1500, text="Câu hai."),
        _cue(start_ms=2500, end_ms=4000, duration_ms=1500, text="Câu ba."),
    ]
    manifest = _manifest(cues)
    expected = (
        "1\n00:00:00,000 --> 00:00:01,000\nCâu một.\n"
        "\n"
        "2\n00:00:01,000 --> 00:00:02,500\nCâu hai.\n"
        "\n"
        "3\n00:00:02,500 --> 00:00:04,000\nCâu ba.\n"
    )
    assert render_srt(manifest) == expected


def test_millisecond_formatting():
    manifest = _manifest([_cue(start_ms=1, end_ms=999, duration_ms=998, text="x")])
    assert "00:00:00,001 --> 00:00:00,999" in render_srt(manifest)


def test_timestamp_beyond_one_hour():
    start_ms = 3_661_234  # 1h 1m 1.234s
    end_ms = start_ms + 2000
    manifest = _manifest([_cue(start_ms=start_ms, end_ms=end_ms, duration_ms=2000, text="x")])
    assert "01:01:01,234 --> 01:01:03,234" in render_srt(manifest)


def test_timestamp_beyond_ten_hours():
    start_ms = 36_000_000  # exactly 10 hours
    end_ms = start_ms + 1000
    manifest = _manifest([_cue(start_ms=start_ms, end_ms=end_ms, duration_ms=1000, text="x")])
    assert "10:00:00,000 --> 10:00:01,000" in render_srt(manifest)


def test_vietnamese_unicode_exact():
    text = "Đây là một câu tiếng Việt đầy đủ dấu: ệ ị ọ ữ ẫ ẳ ố ơ."
    manifest = _manifest([_cue(start_ms=0, end_ms=1000, duration_ms=1000, text=text)])
    assert text in render_srt(manifest)
    assert render_srt(manifest) == f"1\n00:00:00,000 --> 00:00:01,000\n{text}\n"


def test_punctuation_preserved():
    text = "Tại sao? Vì... \"lý do\" — thật kỳ lạ!"
    manifest = _manifest([_cue(start_ms=0, end_ms=1000, duration_ms=1000, text=text)])
    assert text in render_srt(manifest)


def test_multiline_authored_text_preserved():
    text = "Dòng một.\nDòng hai."
    manifest = _manifest([_cue(start_ms=0, end_ms=1000, duration_ms=1000, text=text)])
    assert render_srt(manifest) == f"1\n00:00:00,000 --> 00:00:01,000\n{text}\n"


def test_stable_numbering_starts_at_one_regardless_of_cue_ids():
    cues = [
        _cue(start_ms=0, end_ms=1000, duration_ms=1000, text="a"),
        _cue(start_ms=1000, end_ms=2000, duration_ms=1000, text="b"),
    ]
    output = render_srt(_manifest(cues))
    lines = output.split("\n")
    assert lines[0] == "1"
    # second block's number line is after the blank separator
    assert "2" in lines


def test_deterministic_repeated_output():
    cues = [_cue(start_ms=0, end_ms=1000, duration_ms=1000, text="Xin chào.")]
    manifest = _manifest(cues)
    assert render_srt(manifest) == render_srt(manifest)


def test_final_newline_behavior_no_trailing_blank_line():
    manifest = _manifest([_cue(start_ms=0, end_ms=1000, duration_ms=1000, text="a")])
    output = render_srt(manifest)
    assert output.endswith("a\n")
    assert not output.endswith("a\n\n")


def test_final_newline_behavior_multi_cue():
    cues = [
        _cue(start_ms=0, end_ms=1000, duration_ms=1000, text="a"),
        _cue(start_ms=1000, end_ms=2000, duration_ms=1000, text="b"),
    ]
    output = render_srt(_manifest(cues))
    assert output.endswith("b\n")
    assert not output.endswith("b\n\n")
    # exactly one blank line between blocks
    assert "\n\n" in output
    assert "\n\n\n" not in output
