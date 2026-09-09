from __future__ import annotations

import pytest
from pydantic import ValidationError

from app.models.common import Energy, MusicState, Pace, VoiceState
from app.models.voice import VoiceChunk, VoicePlan


def _chunk(chunk_id="C001", line_ids=("L001",), take_count=1) -> VoiceChunk:
    return VoiceChunk(
        chunk_id=chunk_id,
        line_ids=list(line_ids),
        voice_state="NEUTRAL",
        pace="NORMAL",
        energy="MEDIUM",
        take_count=take_count,
        music_state="BED",
    )


# ---------------------------------------------------------------------------
# Enums
# ---------------------------------------------------------------------------


def test_voice_state_has_exactly_eight_values():
    assert {member.value for member in VoiceState} == {
        "NEUTRAL", "CURIOUS", "SKEPTICAL", "EXCITED", "SERIOUS", "DEADPAN", "PANIC", "LOW_ENERGY",
    }


def test_pace_has_exactly_three_values():
    assert {member.value for member in Pace} == {"SLOW", "NORMAL", "FAST"}


def test_energy_has_exactly_three_values():
    assert {member.value for member in Energy} == {"LOW", "MEDIUM", "HIGH"}


def test_music_state_has_exactly_three_values():
    assert {member.value for member in MusicState} == {"BED", "DUCK", "LIFT"}


# ---------------------------------------------------------------------------
# VoiceChunk
# ---------------------------------------------------------------------------


def test_voice_chunk_constructs_with_required_fields():
    chunk = _chunk()
    assert chunk.chunk_id == "C001"
    assert chunk.line_ids == ["L001"]
    assert chunk.sfx_opportunity is None
    assert chunk.notes is None


def test_voice_chunk_rejects_blank_chunk_id():
    with pytest.raises(ValidationError):
        _chunk(chunk_id="   ")


def test_voice_chunk_rejects_empty_line_ids():
    with pytest.raises(ValidationError):
        VoiceChunk(
            chunk_id="C001",
            line_ids=[],
            voice_state="NEUTRAL",
            pace="NORMAL",
            energy="MEDIUM",
            take_count=1,
            music_state="BED",
        )


@pytest.mark.parametrize("take_count", [1, 2, 3])
def test_voice_chunk_accepts_valid_take_counts(take_count):
    assert _chunk(take_count=take_count).take_count == take_count


@pytest.mark.parametrize("take_count", [0, 4, -1])
def test_voice_chunk_rejects_invalid_take_counts(take_count):
    with pytest.raises(ValidationError):
        _chunk(take_count=take_count)


def test_voice_chunk_rejects_unknown_fields():
    with pytest.raises(ValidationError):
        VoiceChunk(
            chunk_id="C001",
            line_ids=["L001"],
            voice_state="NEUTRAL",
            pace="NORMAL",
            energy="MEDIUM",
            take_count=1,
            music_state="BED",
            unexpected_field="nope",
        )


def test_voice_chunk_accepts_optional_sfx_and_notes():
    chunk = VoiceChunk(
        chunk_id="C001",
        line_ids=["L001"],
        voice_state="EXCITED",
        pace="FAST",
        energy="HIGH",
        take_count=2,
        music_state="LIFT",
        sfx_opportunity="tonal hit",
        notes="Big reveal moment",
    )
    assert chunk.sfx_opportunity == "tonal hit"
    assert chunk.notes == "Big reveal moment"


# ---------------------------------------------------------------------------
# VoicePlan
# ---------------------------------------------------------------------------


def test_voice_plan_constructs_with_chunks():
    from uuid import uuid4

    plan = VoicePlan(script_plan_id=uuid4(), chunks=[_chunk()])
    assert plan.id is not None
    assert len(plan.chunks) == 1


def test_voice_plan_rejects_empty_chunks():
    from uuid import uuid4

    with pytest.raises(ValidationError):
        VoicePlan(script_plan_id=uuid4(), chunks=[])


def test_voice_plan_rejects_unknown_fields():
    from uuid import uuid4

    with pytest.raises(ValidationError):
        VoicePlan(script_plan_id=uuid4(), chunks=[_chunk()], extra_field="nope")
