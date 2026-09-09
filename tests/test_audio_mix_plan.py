"""Phase 30 focused tests: the pure AudioMixPlan planner
(app/video_encoder/audio_mix.py).

No filesystem access, no subprocess, no ffmpeg -- these test only the
music state-machine and SFX-resolution logic in isolation.
"""

from __future__ import annotations

import pytest

from app.models.common import MusicState
from app.models.timeline import TimelineCue, TimelineCueType
from app.video_encoder.audio_mix import build_audio_mix_plan, count_music_cues
from app.video_encoder.errors import (
    AudioMixPlanningError,
    InvalidMusicCueSequenceError,
    UnknownSFXReferenceError,
)
from app.video_encoder.models import AudioAssetBindings, VideoEncodingSettings


def _settings(**overrides) -> VideoEncodingSettings:
    fields = dict(width=1920, height=1080)
    fields.update(overrides)
    return VideoEncodingSettings(**fields)


def _cue(timestamp_ms, cue_type, reference=None) -> TimelineCue:
    return TimelineCue(timestamp_ms=timestamp_ms, cue_type=cue_type, reference=reference)


def _plan(cues, bindings=None, total_duration_ms=10_000, settings=None):
    return build_audio_mix_plan(cues, bindings or AudioAssetBindings(), total_duration_ms, settings or _settings())


# ---------------------------------------------------------------------------
# Music: START -> END
# ---------------------------------------------------------------------------


def test_start_then_end_produces_one_region():
    cues = [
        _cue(0, TimelineCueType.MUSIC_BED_START),
        _cue(5000, TimelineCueType.MUSIC_BED_END),
    ]
    plan = _plan(cues, total_duration_ms=5000)
    assert plan.has_music
    assert len(plan.music_regions) == 1
    region = plan.music_regions[0]
    assert (region.start_ms, region.end_ms, region.state) == (0, 5000, MusicState.BED)
    assert region.gain_db == _settings().music_bed_gain_db


def test_start_duck_lift_end_produces_three_regions():
    cues = [
        _cue(0, TimelineCueType.MUSIC_BED_START),
        _cue(1000, TimelineCueType.MUSIC_DUCK),
        _cue(3000, TimelineCueType.MUSIC_LIFT),
        _cue(5000, TimelineCueType.MUSIC_BED_END),
    ]
    plan = _plan(cues, total_duration_ms=5000)
    assert [(r.start_ms, r.end_ms, r.state) for r in plan.music_regions] == [
        (0, 1000, MusicState.BED),
        (1000, 3000, MusicState.DUCK),
        (3000, 5000, MusicState.LIFT),
    ]
    settings = _settings()
    assert plan.music_regions[0].gain_db == settings.music_bed_gain_db
    assert plan.music_regions[1].gain_db == settings.music_duck_gain_db
    assert plan.music_regions[2].gain_db == settings.music_lift_gain_db


def test_start_without_end_extends_to_timeline_end():
    cues = [_cue(0, TimelineCueType.MUSIC_BED_START)]
    plan = _plan(cues, total_duration_ms=7500)
    assert len(plan.music_regions) == 1
    assert (plan.music_regions[0].start_ms, plan.music_regions[0].end_ms) == (0, 7500)


def test_start_duck_without_end_extends_duck_region_to_timeline_end():
    cues = [
        _cue(0, TimelineCueType.MUSIC_BED_START),
        _cue(2000, TimelineCueType.MUSIC_DUCK),
    ]
    plan = _plan(cues, total_duration_ms=6000)
    assert [(r.start_ms, r.end_ms, r.state) for r in plan.music_regions] == [
        (0, 2000, MusicState.BED),
        (2000, 6000, MusicState.DUCK),
    ]


def test_duplicate_start_fails():
    cues = [
        _cue(0, TimelineCueType.MUSIC_BED_START),
        _cue(1000, TimelineCueType.MUSIC_BED_START),
    ]
    with pytest.raises(InvalidMusicCueSequenceError):
        _plan(cues)


def test_end_without_start_fails():
    cues = [_cue(1000, TimelineCueType.MUSIC_BED_END)]
    with pytest.raises(InvalidMusicCueSequenceError):
        _plan(cues)


def test_duck_without_start_fails():
    cues = [_cue(1000, TimelineCueType.MUSIC_DUCK)]
    with pytest.raises(InvalidMusicCueSequenceError):
        _plan(cues)


def test_lift_without_start_fails():
    cues = [_cue(1000, TimelineCueType.MUSIC_LIFT)]
    with pytest.raises(InvalidMusicCueSequenceError):
        _plan(cues)


def test_out_of_order_cues_fail():
    cues = [
        _cue(2000, TimelineCueType.MUSIC_BED_START),
        _cue(1000, TimelineCueType.MUSIC_DUCK),
    ]
    with pytest.raises(InvalidMusicCueSequenceError):
        _plan(cues)


def test_duplicate_timestamp_music_cues_fail():
    cues = [
        _cue(1000, TimelineCueType.MUSIC_BED_START),
        _cue(1000, TimelineCueType.MUSIC_DUCK),
    ]
    with pytest.raises(InvalidMusicCueSequenceError):
        _plan(cues)


def test_music_cue_timestamp_beyond_timeline_fails():
    cues = [_cue(9999, TimelineCueType.MUSIC_BED_START)]
    with pytest.raises(AudioMixPlanningError):
        _plan(cues, total_duration_ms=5000)


def test_region_shorter_than_ramp_fails():
    cues = [
        _cue(0, TimelineCueType.MUSIC_BED_START),
        _cue(50, TimelineCueType.MUSIC_DUCK),  # 50ms region, ramp default 80ms
        _cue(5000, TimelineCueType.MUSIC_BED_END),
    ]
    with pytest.raises(InvalidMusicCueSequenceError):
        _plan(cues, total_duration_ms=5000)


def test_single_region_not_subject_to_ramp_fit_check():
    """A lone BED region (no DUCK/LIFT ever happened) is never crossfaded,
    so it is exempt from the ramp-length constraint even if very short."""
    cues = [
        _cue(0, TimelineCueType.MUSIC_BED_START),
        _cue(10, TimelineCueType.MUSIC_BED_END),
    ]
    plan = _plan(cues, total_duration_ms=1000, settings=_settings(music_gain_ramp_ms=80))
    assert len(plan.music_regions) == 1


def test_no_music_cues_produces_empty_plan():
    plan = _plan([])
    assert not plan.has_music
    assert plan.music_regions == []


def test_non_music_non_sfx_cues_are_ignored():
    cues = [_cue(0, TimelineCueType.CUT), _cue(100, TimelineCueType.STATIC_HOLD)]
    plan = _plan(cues)
    assert not plan.has_music
    assert plan.sfx_events == []


# ---------------------------------------------------------------------------
# SFX
# ---------------------------------------------------------------------------


def _sfx_bindings(**mapping) -> AudioAssetBindings:
    return AudioAssetBindings(sfx_by_id={k: __import__("pathlib").Path(v) for k, v in mapping.items()})


def test_one_sfx_trigger():
    cues = [_cue(500, TimelineCueType.SFX_TRIGGER, reference="whoosh")]
    bindings = _sfx_bindings(whoosh="whoosh.wav")
    plan = _plan(cues, bindings=bindings)
    assert len(plan.sfx_events) == 1
    assert plan.sfx_events[0].timestamp_ms == 500
    assert plan.sfx_events[0].sfx_id == "whoosh"


def test_multiple_sfx_triggers():
    cues = [
        _cue(500, TimelineCueType.SFX_TRIGGER, reference="whoosh"),
        _cue(2000, TimelineCueType.SFX_TRIGGER, reference="ding"),
    ]
    bindings = _sfx_bindings(whoosh="whoosh.wav", ding="ding.wav")
    plan = _plan(cues, bindings=bindings)
    assert [e.timestamp_ms for e in plan.sfx_events] == [500, 2000]


def test_same_sfx_id_reused_across_events():
    cues = [
        _cue(500, TimelineCueType.SFX_TRIGGER, reference="whoosh"),
        _cue(2000, TimelineCueType.SFX_TRIGGER, reference="whoosh"),
    ]
    bindings = _sfx_bindings(whoosh="whoosh.wav")
    plan = _plan(cues, bindings=bindings)
    assert len(plan.sfx_events) == 2
    assert all(e.sfx_id == "whoosh" for e in plan.sfx_events)
    assert plan.sfx_events[0].file_path == plan.sfx_events[1].file_path


def test_different_sfx_ids_resolve_different_paths():
    cues = [
        _cue(500, TimelineCueType.SFX_TRIGGER, reference="whoosh"),
        _cue(2000, TimelineCueType.SFX_TRIGGER, reference="ding"),
    ]
    bindings = _sfx_bindings(whoosh="whoosh.wav", ding="ding.wav")
    plan = _plan(cues, bindings=bindings)
    assert plan.sfx_events[0].file_path != plan.sfx_events[1].file_path


def test_trigger_near_timeline_end_allowed():
    cues = [_cue(9999, TimelineCueType.SFX_TRIGGER, reference="whoosh")]
    bindings = _sfx_bindings(whoosh="whoosh.wav")
    plan = _plan(cues, bindings=bindings, total_duration_ms=10_000)
    assert plan.sfx_events[0].timestamp_ms == 9999


def test_sfx_trigger_beyond_timeline_fails():
    cues = [_cue(10_001, TimelineCueType.SFX_TRIGGER, reference="whoosh")]
    bindings = _sfx_bindings(whoosh="whoosh.wav")
    with pytest.raises(AudioMixPlanningError):
        _plan(cues, bindings=bindings, total_duration_ms=10_000)


def test_unknown_sfx_id_fails():
    cues = [_cue(500, TimelineCueType.SFX_TRIGGER, reference="unknown_id")]
    with pytest.raises(UnknownSFXReferenceError):
        _plan(cues, bindings=AudioAssetBindings())


def test_blank_sfx_reference_fails():
    cues = [_cue(500, TimelineCueType.SFX_TRIGGER, reference=None)]
    with pytest.raises(UnknownSFXReferenceError):
        _plan(cues, bindings=AudioAssetBindings())


# ---------------------------------------------------------------------------
# count_music_cues
# ---------------------------------------------------------------------------


def test_count_music_cues():
    cues = [
        _cue(0, TimelineCueType.MUSIC_BED_START),
        _cue(1000, TimelineCueType.MUSIC_DUCK),
        _cue(2000, TimelineCueType.SFX_TRIGGER, reference="whoosh"),
        _cue(3000, TimelineCueType.MUSIC_BED_END),
    ]
    assert count_music_cues(cues) == 3
