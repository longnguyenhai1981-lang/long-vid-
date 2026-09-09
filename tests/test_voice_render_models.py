from __future__ import annotations

from datetime import datetime, timezone
from uuid import uuid4

import pytest
from pydantic import ValidationError

from app.models.audio import RenderedVoiceTake, VoiceRenderManifest
from app.models.common import AudioFormat


def test_audio_format_has_exactly_two_values():
    assert {member.value for member in AudioFormat} == {"WAV", "MP3"}


def _take(**overrides) -> RenderedVoiceTake:
    fields = dict(
        render_job_id="C001_T1",
        chunk_id="C001",
        take_number=1,
        line_ids=["L001", "L002"],
        file_path="C001_T1.wav",
        music_state="BED",
    )
    fields.update(overrides)
    return RenderedVoiceTake(**fields)


# ---------------------------------------------------------------------------
# RenderedVoiceTake
# ---------------------------------------------------------------------------


def test_rendered_voice_take_constructs_with_required_fields():
    take = _take()
    assert take.duration_seconds is None
    assert take.provider_request_id is None
    assert take.sfx_opportunity is None


def test_rendered_voice_take_rejects_blank_render_job_id():
    with pytest.raises(ValidationError):
        _take(render_job_id="   ")


def test_rendered_voice_take_rejects_blank_chunk_id():
    with pytest.raises(ValidationError):
        _take(chunk_id="   ")


def test_rendered_voice_take_rejects_blank_file_path():
    with pytest.raises(ValidationError):
        _take(file_path="   ")


def test_rendered_voice_take_rejects_empty_line_ids():
    with pytest.raises(ValidationError):
        _take(line_ids=[])


@pytest.mark.parametrize("take_number", [1, 2, 3])
def test_rendered_voice_take_accepts_valid_take_numbers(take_number):
    assert _take(take_number=take_number).take_number == take_number


@pytest.mark.parametrize("take_number", [0, 4, -1])
def test_rendered_voice_take_rejects_invalid_take_numbers(take_number):
    with pytest.raises(ValidationError):
        _take(take_number=take_number)


def test_rendered_voice_take_rejects_non_positive_duration():
    with pytest.raises(ValidationError):
        _take(duration_seconds=0)
    with pytest.raises(ValidationError):
        _take(duration_seconds=-2.0)


def test_rendered_voice_take_accepts_positive_duration():
    assert _take(duration_seconds=4.5).duration_seconds == 4.5


def test_rendered_voice_take_rejects_unknown_field():
    with pytest.raises(ValidationError):
        _take(not_a_real_field="oops")


# ---------------------------------------------------------------------------
# VoiceRenderManifest
# ---------------------------------------------------------------------------


def _manifest(**overrides) -> VoiceRenderManifest:
    fields = dict(
        script_plan_id=uuid4(),
        voice_plan_id=uuid4(),
        provider="fake-tts",
        voice_id="voice-01",
        output_format="WAV",
        renders=[_take()],
        created_at=datetime.now(timezone.utc),
    )
    fields.update(overrides)
    return VoiceRenderManifest(**fields)


def test_voice_render_manifest_constructs_with_renders():
    manifest = _manifest()
    assert manifest.id is not None
    assert len(manifest.renders) == 1


def test_voice_render_manifest_rejects_empty_renders():
    with pytest.raises(ValidationError):
        _manifest(renders=[])


def test_voice_render_manifest_rejects_blank_provider():
    with pytest.raises(ValidationError):
        _manifest(provider="   ")


def test_voice_render_manifest_rejects_blank_voice_id():
    with pytest.raises(ValidationError):
        _manifest(voice_id="   ")


def test_voice_render_manifest_rejects_naive_created_at():
    with pytest.raises(ValidationError):
        _manifest(created_at=datetime(2026, 1, 1))


def test_voice_render_manifest_rejects_unknown_field():
    with pytest.raises(ValidationError):
        _manifest(not_a_real_field="oops")
