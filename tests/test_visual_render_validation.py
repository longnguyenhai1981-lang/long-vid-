from __future__ import annotations

from datetime import datetime, timezone
from uuid import uuid4

from app.models.script import ScriptBeat, ScriptLine, ScriptPlan
from app.models.visual import VisualBeat, VisualPlan
from app.models.visual_render import RenderedVisualAsset, VisualRenderManifest, VisualRenderRequirement
from app.models.voice import VoiceChunk, VoicePlan
from app.renderers.visual.validation import validate_visual_render_manifest


def _line(line_id) -> ScriptLine:
    return ScriptLine(line_id=line_id, text=f"Line {line_id}.", function="INFORM")


def _script_plan(line_ids=("L1", "L2")) -> ScriptPlan:
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


def _chunk(chunk_id="C001", line_ids=("L1", "L2")) -> VoiceChunk:
    return VoiceChunk(
        chunk_id=chunk_id,
        line_ids=list(line_ids),
        voice_state="NEUTRAL",
        pace="NORMAL",
        energy="MEDIUM",
        take_count=1,
        music_state="BED",
    )


def _voice_plan(script_plan_id) -> VoicePlan:
    return VoicePlan(script_plan_id=script_plan_id, chunks=[_chunk()])


def _visual_beat(beat_id, media_type="GENERATED_STILL", **overrides) -> VisualBeat:
    fields = dict(
        beat_id=beat_id,
        script_line_ids=["L1"],
        narrative_node="Q0",
        visual_level="L1_ESTABLISH",
        visual_function="STORY",
        media_type=media_type,
        complexity="C1",
        concept="A bridge deck twisting in the wind",
        primary_focus="The bridge deck",
    )
    fields.update(overrides)
    return VisualBeat(**fields)


def _visual_plan(beats, script_plan_id, voice_plan_id) -> VisualPlan:
    return VisualPlan(script_plan_id=script_plan_id, voice_plan_id=voice_plan_id, beats=beats)


def _asset(beat_id, render_job_id=None, file_path=None, media_type="GENERATED_STILL") -> RenderedVisualAsset:
    job_id = render_job_id or f"{beat_id}_R1"
    return RenderedVisualAsset(
        render_job_id=job_id,
        beat_id=beat_id,
        media_type=media_type,
        file_path=file_path or f"proj/plan/{job_id}.png",
    )


def _requirement(beat_id, media_type, status, reference=None) -> VisualRenderRequirement:
    return VisualRenderRequirement(beat_id=beat_id, media_type=media_type, status=status, reference=reference)


def _manifest(script_plan_id, voice_plan_id, visual_plan_id, assets, requirements) -> VisualRenderManifest:
    return VisualRenderManifest(
        script_plan_id=script_plan_id,
        voice_plan_id=voice_plan_id,
        visual_plan_id=visual_plan_id,
        provider="fake-visual",
        output_format="PNG",
        assets=assets,
        requirements=requirements,
        created_at=datetime.now(timezone.utc),
    )


# ---------------------------------------------------------------------------
# Valid baseline
# ---------------------------------------------------------------------------


def test_valid_manifest_has_no_issues():
    script = _script_plan()
    voice_plan = _voice_plan(script.id)
    beats = [
        _visual_beat("V001", media_type="GENERATED_STILL"),
        _visual_beat("V002", media_type="ASSET_REUSE", reuse_key="key-001"),
    ]
    visual_plan = _visual_plan(beats, script.id, voice_plan.id)
    assets = [_asset("V001")]
    requirements = [_requirement("V002", "ASSET_REUSE", "REUSE_ONLY", reference="key-001")]
    manifest = _manifest(script.id, voice_plan.id, visual_plan.id, assets, requirements)
    assert validate_visual_render_manifest(manifest, script, voice_plan, visual_plan) == []


# ---------------------------------------------------------------------------
# Stale ids
# ---------------------------------------------------------------------------


def test_manifest_script_plan_id_mismatch_flagged():
    script = _script_plan()
    voice_plan = _voice_plan(script.id)
    beats = [_visual_beat("V001")]
    visual_plan = _visual_plan(beats, script.id, voice_plan.id)
    manifest = _manifest(uuid4(), voice_plan.id, visual_plan.id, [_asset("V001")], [])
    issues = validate_visual_render_manifest(manifest, script, voice_plan, visual_plan)
    assert any("script_plan_id" in issue for issue in issues)


def test_manifest_voice_plan_id_mismatch_flagged():
    script = _script_plan()
    voice_plan = _voice_plan(script.id)
    beats = [_visual_beat("V001")]
    visual_plan = _visual_plan(beats, script.id, voice_plan.id)
    manifest = _manifest(script.id, uuid4(), visual_plan.id, [_asset("V001")], [])
    issues = validate_visual_render_manifest(manifest, script, voice_plan, visual_plan)
    assert any("voice_plan_id" in issue for issue in issues)


def test_manifest_visual_plan_id_mismatch_flagged():
    script = _script_plan()
    voice_plan = _voice_plan(script.id)
    beats = [_visual_beat("V001")]
    visual_plan = _visual_plan(beats, script.id, voice_plan.id)
    manifest = _manifest(script.id, voice_plan.id, uuid4(), [_asset("V001")], [])
    issues = validate_visual_render_manifest(manifest, script, voice_plan, visual_plan)
    assert any("visual_plan_id" in issue for issue in issues)


# ---------------------------------------------------------------------------
# Beat coverage
# ---------------------------------------------------------------------------


def test_missing_beat_flagged():
    script = _script_plan()
    voice_plan = _voice_plan(script.id)
    beats = [_visual_beat("V001"), _visual_beat("V002", media_type="ASSET_REUSE", reuse_key="k")]
    visual_plan = _visual_plan(beats, script.id, voice_plan.id)
    manifest = _manifest(script.id, voice_plan.id, visual_plan.id, [_asset("V001")], [])  # V002 uncovered
    issues = validate_visual_render_manifest(manifest, script, voice_plan, visual_plan)
    assert any("missing coverage" in issue and "V002" in issue for issue in issues)


def test_unknown_beat_flagged():
    script = _script_plan()
    voice_plan = _voice_plan(script.id)
    beats = [_visual_beat("V001")]
    visual_plan = _visual_plan(beats, script.id, voice_plan.id)
    manifest = _manifest(
        script.id, voice_plan.id, visual_plan.id, [_asset("V001"), _asset("V999")], []
    )
    issues = validate_visual_render_manifest(manifest, script, voice_plan, visual_plan)
    assert any("unknown VisualPlan beat_ids" in issue and "V999" in issue for issue in issues)


def test_duplicate_beat_id_across_assets_and_requirements_flagged():
    script = _script_plan()
    voice_plan = _voice_plan(script.id)
    beats = [_visual_beat("V001")]
    visual_plan = _visual_plan(beats, script.id, voice_plan.id)
    manifest = _manifest(
        script.id,
        voice_plan.id,
        visual_plan.id,
        [_asset("V001")],
        [_requirement("V001", "ASSET_REUSE", "REUSE_ONLY", reference="k")],
    )
    issues = validate_visual_render_manifest(manifest, script, voice_plan, visual_plan)
    assert any("Duplicate beat_id: V001" in issue for issue in issues)


# ---------------------------------------------------------------------------
# render_job_id / file_path identity
# ---------------------------------------------------------------------------


def test_duplicate_render_job_id_flagged():
    script = _script_plan()
    voice_plan = _voice_plan(script.id)
    beats = [_visual_beat("V001"), _visual_beat("V002")]
    visual_plan = _visual_plan(beats, script.id, voice_plan.id)
    manifest = _manifest(
        script.id,
        voice_plan.id,
        visual_plan.id,
        [
            _asset("V001", render_job_id="SAME", file_path="a.png"),
            _asset("V002", render_job_id="SAME", file_path="b.png"),
        ],
        [],
    )
    issues = validate_visual_render_manifest(manifest, script, voice_plan, visual_plan)
    assert any("Duplicate render_job_id: SAME" in issue for issue in issues)


def test_duplicate_file_path_flagged():
    script = _script_plan()
    voice_plan = _voice_plan(script.id)
    beats = [_visual_beat("V001"), _visual_beat("V002")]
    visual_plan = _visual_plan(beats, script.id, voice_plan.id)
    manifest = _manifest(
        script.id,
        voice_plan.id,
        visual_plan.id,
        [_asset("V001", file_path="same.png"), _asset("V002", file_path="same.png")],
        [],
    )
    issues = validate_visual_render_manifest(manifest, script, voice_plan, visual_plan)
    assert any("Duplicate file_path: same.png" in issue for issue in issues)


# ---------------------------------------------------------------------------
# Path safety (defense in depth on top of VisualFileStore's own check)
# ---------------------------------------------------------------------------


def test_absolute_file_path_flagged():
    script = _script_plan()
    voice_plan = _voice_plan(script.id)
    beats = [_visual_beat("V001")]
    visual_plan = _visual_plan(beats, script.id, voice_plan.id)
    manifest = _manifest(
        script.id, voice_plan.id, visual_plan.id, [_asset("V001", file_path="/etc/passwd")], []
    )
    issues = validate_visual_render_manifest(manifest, script, voice_plan, visual_plan)
    assert any("must not be absolute" in issue for issue in issues)


def test_traversal_file_path_flagged():
    script = _script_plan()
    voice_plan = _voice_plan(script.id)
    beats = [_visual_beat("V001")]
    visual_plan = _visual_plan(beats, script.id, voice_plan.id)
    manifest = _manifest(
        script.id, voice_plan.id, visual_plan.id, [_asset("V001", file_path="../escape.png")], []
    )
    issues = validate_visual_render_manifest(manifest, script, voice_plan, visual_plan)
    assert any("must not contain '..'" in issue for issue in issues)


# ---------------------------------------------------------------------------
# Status/media-type pairing
# ---------------------------------------------------------------------------


def test_asset_with_non_renderable_media_type_flagged():
    script = _script_plan()
    voice_plan = _voice_plan(script.id)
    beats = [_visual_beat("V001", media_type="ASSET_REUSE", reuse_key="k")]
    visual_plan = _visual_plan(beats, script.id, voice_plan.id)
    manifest = _manifest(
        script.id, voice_plan.id, visual_plan.id, [_asset("V001", media_type="ASSET_REUSE")], []
    )
    issues = validate_visual_render_manifest(manifest, script, voice_plan, visual_plan)
    assert any("not provider-renderable" in issue for issue in issues)


def test_reuse_only_requirement_with_wrong_media_type_flagged():
    script = _script_plan()
    voice_plan = _voice_plan(script.id)
    beats = [_visual_beat("V001", media_type="LIMITED_MOTION")]
    visual_plan = _visual_plan(beats, script.id, voice_plan.id)
    manifest = _manifest(
        script.id,
        voice_plan.id,
        visual_plan.id,
        [],
        [_requirement("V001", "LIMITED_MOTION", "REUSE_ONLY")],
    )
    issues = validate_visual_render_manifest(manifest, script, voice_plan, visual_plan)
    assert any("is REUSE_ONLY but has media_type LIMITED_MOTION" in issue for issue in issues)


def test_ti_state_asset_from_composite_mode_is_not_flagged():
    """A TI_STATE beat composited onto a background (Phase 23) produces a
    real RenderedVisualAsset -- allowed even though TI_STATE never goes
    through a VisualProvider."""
    script = _script_plan()
    voice_plan = _voice_plan(script.id)
    beats = [_visual_beat("V001", media_type="TI_STATE", ti_state="NEUTRAL")]
    visual_plan = _visual_plan(beats, script.id, voice_plan.id)
    manifest = _manifest(
        script.id, voice_plan.id, visual_plan.id, [_asset("V001", media_type="TI_STATE")], []
    )
    issues = validate_visual_render_manifest(manifest, script, voice_plan, visual_plan)
    assert issues == []


def test_canonical_asset_ready_requirement_with_ti_state_is_not_flagged():
    script = _script_plan()
    voice_plan = _voice_plan(script.id)
    beats = [_visual_beat("V001", media_type="TI_STATE", ti_state="NEUTRAL")]
    visual_plan = _visual_plan(beats, script.id, voice_plan.id)
    manifest = _manifest(
        script.id,
        voice_plan.id,
        visual_plan.id,
        [],
        [_requirement("V001", "TI_STATE", "CANONICAL_ASSET_READY", reference="asset-set/v1/NEUTRAL.png")],
    )
    issues = validate_visual_render_manifest(manifest, script, voice_plan, visual_plan)
    assert issues == []


def test_canonical_asset_ready_requirement_with_wrong_media_type_flagged():
    script = _script_plan()
    voice_plan = _voice_plan(script.id)
    beats = [_visual_beat("V001", media_type="ASSET_REUSE", reuse_key="k")]
    visual_plan = _visual_plan(beats, script.id, voice_plan.id)
    manifest = _manifest(
        script.id,
        voice_plan.id,
        visual_plan.id,
        [],
        [_requirement("V001", "ASSET_REUSE", "CANONICAL_ASSET_READY")],
    )
    issues = validate_visual_render_manifest(manifest, script, voice_plan, visual_plan)
    assert any("is CANONICAL_ASSET_READY but has media_type ASSET_REUSE" in issue for issue in issues)


def test_reuse_only_requirement_with_ti_state_media_type_now_flagged():
    """As of Phase 23, TI_STATE is no longer a valid media_type for a
    REUSE_ONLY requirement -- it produces CANONICAL_ASSET_READY instead."""
    script = _script_plan()
    voice_plan = _voice_plan(script.id)
    beats = [_visual_beat("V001", media_type="TI_STATE", ti_state="NEUTRAL")]
    visual_plan = _visual_plan(beats, script.id, voice_plan.id)
    manifest = _manifest(
        script.id,
        voice_plan.id,
        visual_plan.id,
        [],
        [_requirement("V001", "TI_STATE", "REUSE_ONLY", reference="ti_state:NEUTRAL")],
    )
    issues = validate_visual_render_manifest(manifest, script, voice_plan, visual_plan)
    assert any("is REUSE_ONLY but has media_type TI_STATE" in issue for issue in issues)


def test_external_required_requirement_with_wrong_media_type_flagged():
    script = _script_plan()
    voice_plan = _voice_plan(script.id)
    beats = [_visual_beat("V001", media_type="ASSET_REUSE", reuse_key="k")]
    visual_plan = _visual_plan(beats, script.id, voice_plan.id)
    manifest = _manifest(
        script.id,
        voice_plan.id,
        visual_plan.id,
        [],
        [_requirement("V001", "ASSET_REUSE", "EXTERNAL_REQUIRED")],
    )
    issues = validate_visual_render_manifest(manifest, script, voice_plan, visual_plan)
    assert any("is EXTERNAL_REQUIRED but has media_type ASSET_REUSE" in issue for issue in issues)


# ---------------------------------------------------------------------------
# Multiple issues
# ---------------------------------------------------------------------------


def test_multiple_issues_all_reported_together():
    script = _script_plan()
    voice_plan = _voice_plan(script.id)
    beats = [_visual_beat("V001"), _visual_beat("V002", media_type="ASSET_REUSE", reuse_key="k")]
    visual_plan = _visual_plan(beats, script.id, voice_plan.id)
    manifest = _manifest(
        script.id, voice_plan.id, visual_plan.id, [_asset("V001", file_path="/abs.png")], []
    )  # V002 missing, and V001's path is absolute
    issues = validate_visual_render_manifest(manifest, script, voice_plan, visual_plan)
    assert len(issues) >= 2
