from __future__ import annotations

from datetime import datetime, timezone
from uuid import uuid4

from app.models.audio import RenderedVoiceTake, VoiceRenderManifest
from app.models.script import ScriptBeat, ScriptLine, ScriptPlan
from app.models.voice import VoiceChunk, VoicePlan
from app.renderers.voice.validation import validate_voice_render_manifest


def _line(line_id) -> ScriptLine:
    return ScriptLine(line_id=line_id, text=f"Line {line_id}.", function="INFORM")


def _script_plan(line_ids=("L1", "L2", "L3", "L4")) -> ScriptPlan:
    return ScriptPlan(
        estimated_duration_seconds=480,
        beats=[
            ScriptBeat(
                beat_id="B001",
                narrative_node="Q0",
                narrative_function="INFORM",
                lines=[_line(lid) for lid in line_ids],
            )
        ],
        qa_status="PASS",
    )


def _chunk(chunk_id, line_ids, take_count=1) -> VoiceChunk:
    return VoiceChunk(
        chunk_id=chunk_id,
        line_ids=list(line_ids),
        voice_state="NEUTRAL",
        pace="NORMAL",
        energy="MEDIUM",
        take_count=take_count,
        music_state="BED",
    )


def _voice_plan(chunks, script_plan_id=None) -> VoicePlan:
    return VoicePlan(script_plan_id=script_plan_id or uuid4(), chunks=chunks)


def _take(chunk_id, take_number, line_ids, render_job_id=None, file_path=None) -> RenderedVoiceTake:
    job_id = render_job_id or f"{chunk_id}_T{take_number}"
    return RenderedVoiceTake(
        render_job_id=job_id,
        chunk_id=chunk_id,
        take_number=take_number,
        line_ids=list(line_ids),
        file_path=file_path or f"{job_id}.wav",
        music_state="BED",
    )


def _manifest(script_plan_id, voice_plan_id, renders) -> VoiceRenderManifest:
    return VoiceRenderManifest(
        script_plan_id=script_plan_id,
        voice_plan_id=voice_plan_id,
        provider="fake-tts",
        voice_id="voice-01",
        output_format="WAV",
        renders=renders,
        created_at=datetime.now(timezone.utc),
    )


# ---------------------------------------------------------------------------
# Valid baseline
# ---------------------------------------------------------------------------


def test_valid_manifest_has_no_issues():
    script = _script_plan(("L1", "L2", "L3", "L4"))
    chunks = [_chunk("C001", ["L1", "L2"]), _chunk("C002", ["L3", "L4"], take_count=2)]
    voice_plan = _voice_plan(chunks, script_plan_id=script.id)
    renders = [
        _take("C001", 1, ["L1", "L2"]),
        _take("C002", 1, ["L3", "L4"]),
        _take("C002", 2, ["L3", "L4"]),
    ]
    manifest = _manifest(script.id, voice_plan.id, renders)
    assert validate_voice_render_manifest(manifest, script, voice_plan) == []


# ---------------------------------------------------------------------------
# Stale ids
# ---------------------------------------------------------------------------


def test_manifest_script_plan_id_mismatch_flagged():
    script = _script_plan(("L1",))
    chunks = [_chunk("C001", ["L1"])]
    voice_plan = _voice_plan(chunks, script_plan_id=script.id)
    renders = [_take("C001", 1, ["L1"])]
    manifest = _manifest(uuid4(), voice_plan.id, renders)
    issues = validate_voice_render_manifest(manifest, script, voice_plan)
    assert any("script_plan_id" in issue for issue in issues)


def test_manifest_voice_plan_id_mismatch_flagged():
    script = _script_plan(("L1",))
    chunks = [_chunk("C001", ["L1"])]
    voice_plan = _voice_plan(chunks, script_plan_id=script.id)
    renders = [_take("C001", 1, ["L1"])]
    manifest = _manifest(script.id, uuid4(), renders)
    issues = validate_voice_render_manifest(manifest, script, voice_plan)
    assert any("voice_plan_id" in issue for issue in issues)


# ---------------------------------------------------------------------------
# Chunk coverage
# ---------------------------------------------------------------------------


def test_missing_chunk_flagged():
    script = _script_plan(("L1", "L2"))
    chunks = [_chunk("C001", ["L1"]), _chunk("C002", ["L2"])]
    voice_plan = _voice_plan(chunks, script_plan_id=script.id)
    renders = [_take("C001", 1, ["L1"])]  # C002 never rendered
    manifest = _manifest(script.id, voice_plan.id, renders)
    issues = validate_voice_render_manifest(manifest, script, voice_plan)
    assert any("missing renders" in issue and "C002" in issue for issue in issues)


def test_unknown_chunk_flagged():
    script = _script_plan(("L1",))
    chunks = [_chunk("C001", ["L1"])]
    voice_plan = _voice_plan(chunks, script_plan_id=script.id)
    renders = [_take("C001", 1, ["L1"]), _take("C999", 1, ["L1"])]
    manifest = _manifest(script.id, voice_plan.id, renders)
    issues = validate_voice_render_manifest(manifest, script, voice_plan)
    assert any("unknown VoicePlan chunk_ids" in issue and "C999" in issue for issue in issues)


# ---------------------------------------------------------------------------
# Take-count coverage
# ---------------------------------------------------------------------------


def test_too_few_takes_flagged():
    script = _script_plan(("L1",))
    chunks = [_chunk("C001", ["L1"], take_count=2)]
    voice_plan = _voice_plan(chunks, script_plan_id=script.id)
    renders = [_take("C001", 1, ["L1"])]  # take 2 missing
    manifest = _manifest(script.id, voice_plan.id, renders)
    issues = validate_voice_render_manifest(manifest, script, voice_plan)
    assert any("expected take numbers" in issue and "C001" in issue for issue in issues)


def test_too_many_takes_flagged():
    script = _script_plan(("L1",))
    chunks = [_chunk("C001", ["L1"], take_count=1)]
    voice_plan = _voice_plan(chunks, script_plan_id=script.id)
    renders = [
        _take("C001", 1, ["L1"]),
        RenderedVoiceTake(
            render_job_id="C001_T2",
            chunk_id="C001",
            take_number=2,
            line_ids=["L1"],
            file_path="C001_T2.wav",
            music_state="BED",
        ),
    ]
    manifest = _manifest(script.id, voice_plan.id, renders)
    issues = validate_voice_render_manifest(manifest, script, voice_plan)
    assert any("expected take numbers" in issue and "C001" in issue for issue in issues)


def test_duplicate_take_number_flagged_even_when_set_matches():
    # take_count=2 expects {1, 2}; actual takes are [1, 1] -- the set
    # collapses to {1}, so this must be caught by the duplicate check, not
    # the set-equality check alone.
    script = _script_plan(("L1",))
    chunks = [_chunk("C001", ["L1"], take_count=2)]
    voice_plan = _voice_plan(chunks, script_plan_id=script.id)
    renders = [
        _take("C001", 1, ["L1"], render_job_id="C001_T1a", file_path="C001_T1a.wav"),
        _take("C001", 1, ["L1"], render_job_id="C001_T1b", file_path="C001_T1b.wav"),
    ]
    manifest = _manifest(script.id, voice_plan.id, renders)
    issues = validate_voice_render_manifest(manifest, script, voice_plan)
    assert any("Duplicate take_number for chunk 'C001'" in issue for issue in issues)


# ---------------------------------------------------------------------------
# line_ids exactness
# ---------------------------------------------------------------------------


def test_wrong_line_ids_flagged():
    script = _script_plan(("L1", "L2"))
    chunks = [_chunk("C001", ["L1", "L2"])]
    voice_plan = _voice_plan(chunks, script_plan_id=script.id)
    renders = [_take("C001", 1, ["L1"])]  # dropped L2
    manifest = _manifest(script.id, voice_plan.id, renders)
    issues = validate_voice_render_manifest(manifest, script, voice_plan)
    assert any("does not match chunk 'C001' line_ids" in issue for issue in issues)


# ---------------------------------------------------------------------------
# render_job_id / file_path identity
# ---------------------------------------------------------------------------


def test_duplicate_render_job_id_flagged():
    script = _script_plan(("L1",))
    chunks = [_chunk("C001", ["L1"], take_count=2)]
    voice_plan = _voice_plan(chunks, script_plan_id=script.id)
    renders = [
        _take("C001", 1, ["L1"], render_job_id="SAME", file_path="a.wav"),
        _take("C001", 2, ["L1"], render_job_id="SAME", file_path="b.wav"),
    ]
    manifest = _manifest(script.id, voice_plan.id, renders)
    issues = validate_voice_render_manifest(manifest, script, voice_plan)
    assert any("Duplicate render_job_id: SAME" in issue for issue in issues)


def test_duplicate_file_path_flagged():
    script = _script_plan(("L1",))
    chunks = [_chunk("C001", ["L1"], take_count=2)]
    voice_plan = _voice_plan(chunks, script_plan_id=script.id)
    renders = [
        _take("C001", 1, ["L1"], file_path="same.wav"),
        _take("C001", 2, ["L1"], file_path="same.wav"),
    ]
    manifest = _manifest(script.id, voice_plan.id, renders)
    issues = validate_voice_render_manifest(manifest, script, voice_plan)
    assert any("Duplicate file_path: same.wav" in issue for issue in issues)


# ---------------------------------------------------------------------------
# Path safety (defense in depth on top of AudioFileStore's own check)
# ---------------------------------------------------------------------------


def test_absolute_file_path_flagged():
    script = _script_plan(("L1",))
    chunks = [_chunk("C001", ["L1"])]
    voice_plan = _voice_plan(chunks, script_plan_id=script.id)
    renders = [_take("C001", 1, ["L1"], file_path="/etc/passwd")]
    manifest = _manifest(script.id, voice_plan.id, renders)
    issues = validate_voice_render_manifest(manifest, script, voice_plan)
    assert any("must not be absolute" in issue for issue in issues)


def test_traversal_file_path_flagged():
    script = _script_plan(("L1",))
    chunks = [_chunk("C001", ["L1"])]
    voice_plan = _voice_plan(chunks, script_plan_id=script.id)
    renders = [_take("C001", 1, ["L1"], file_path="../escape.wav")]
    manifest = _manifest(script.id, voice_plan.id, renders)
    issues = validate_voice_render_manifest(manifest, script, voice_plan)
    assert any("must not contain '..'" in issue for issue in issues)


# ---------------------------------------------------------------------------
# Multiple issues
# ---------------------------------------------------------------------------


def test_multiple_issues_all_reported_together():
    script = _script_plan(("L1", "L2"))
    chunks = [_chunk("C001", ["L1"]), _chunk("C002", ["L2"])]
    voice_plan = _voice_plan(chunks, script_plan_id=script.id)
    renders = [_take("C001", 1, ["L999"])]  # wrong line_ids, and C002 missing
    manifest = _manifest(script.id, voice_plan.id, renders)
    issues = validate_voice_render_manifest(manifest, script, voice_plan)
    assert len(issues) >= 2
