"""Phase 25 focused tests: VisualRenderer <-> DiagramRenderer integration.

Reuses tests/test_visual_renderer.py's project-graph builders (a full
MVP_COMPLETE project with ScriptPlan/VoicePlan/VisualPlan already saved)
rather than re-deriving ~150 lines of unrelated fixture setup -- this file
only adds the DIAGRAM-specific scenarios Phase 25 introduces. See that
file for the full non-DIAGRAM routing suite (GENERATED_STILL/ASSET_REUSE/
TI_STATE/LIMITED_MOTION/EVIDENCE_MEDIA/AI_HERO_VIDEO), left unmodified in
behavior by this phase except where DIAGRAM previously shared a routing
set with GENERATED_STILL (updated in place there, not here).
"""

from __future__ import annotations

import pytest

from app.diagram_renderer.errors import DiagramRenderError
from app.models.common import ModuleRunStatus
from app.renderers.visual.errors import MissingDiagramSpecError, UnexpectedDiagramSpecError
from app.renderers.visual.models import TiStateRenderMode, TiStateSource, VisualRendererInput
from app.renderers.visual.renderer import VisualRenderer
from app.storage.module_runs import list_module_runs_for_project
from app.visual.fake import FakeVisualProvider
from app.visual.storage import VisualFileStore
from tests.test_visual_renderer import (
    _create_project_ready_for_visual_rendering,
    _diagram_spec,
    _settings,
    _ti_compositor,
    _visual_beat,
)


def _diagram_beat(beat_id="V1", **overrides):
    return _visual_beat(beat_id, media_type="DIAGRAM", **overrides)


# ---------------------------------------------------------------------------
# Basic routing: zero provider calls, a real RenderedVisualAsset
# ---------------------------------------------------------------------------


def test_diagram_beat_produces_rendered_visual_asset(engine, tmp_path):
    beats = [_diagram_beat("V1")]
    project_id, *_ = _create_project_ready_for_visual_rendering(engine, beats=beats)
    provider = FakeVisualProvider([])
    store = VisualFileStore(tmp_path / "visuals")
    renderer = VisualRenderer(engine, provider, _settings(), store)

    result = renderer.run(VisualRendererInput(project_id=project_id))

    assert len(result.manifest.assets) == 1
    asset = result.manifest.assets[0]
    assert asset.beat_id == "V1"
    assert asset.media_type.value == "DIAGRAM"
    assert asset.file_path.endswith(".png")
    written_path = store.root / asset.file_path
    assert written_path.is_file()


def test_diagram_beat_makes_zero_provider_calls(engine, tmp_path):
    beats = [_diagram_beat("V1")]
    project_id, *_ = _create_project_ready_for_visual_rendering(engine, beats=beats)
    provider = FakeVisualProvider([])  # any call at all raises "exhausted"
    store = VisualFileStore(tmp_path / "visuals")
    renderer = VisualRenderer(engine, provider, _settings(), store)

    result = renderer.run(VisualRendererInput(project_id=project_id))

    assert provider.call_count == 0
    assert result.provider_call_count == 0


def test_non_diagram_provider_behavior_unchanged(engine, tmp_path):
    beats = [_visual_beat("V1", media_type="GENERATED_STILL")]
    project_id, *_ = _create_project_ready_for_visual_rendering(engine, beats=beats)
    from tests.test_visual_renderer import _visual_response

    provider = FakeVisualProvider([_visual_response()])
    store = VisualFileStore(tmp_path / "visuals")
    renderer = VisualRenderer(engine, provider, _settings(), store)

    result = renderer.run(VisualRendererInput(project_id=project_id))

    assert provider.call_count == 1
    assert result.manifest.assets[0].beat_id == "V1"


def test_manifest_contains_diagram_asset_alongside_others(engine, tmp_path):
    from tests.test_visual_renderer import _visual_response

    beats = [
        _visual_beat("V1", media_type="GENERATED_STILL"),
        _diagram_beat("V2"),
        _visual_beat("V3", media_type="ASSET_REUSE", reuse_key="k"),
    ]
    project_id, *_ = _create_project_ready_for_visual_rendering(engine, beats=beats)
    provider = FakeVisualProvider([_visual_response()])
    store = VisualFileStore(tmp_path / "visuals")
    renderer = VisualRenderer(engine, provider, _settings(), store)

    result = renderer.run(VisualRendererInput(project_id=project_id))

    asset_beat_ids = {a.beat_id for a in result.manifest.assets}
    assert asset_beat_ids == {"V1", "V2"}
    assert result.manifest.requirements[0].beat_id == "V3"


# ---------------------------------------------------------------------------
# Structural validation: MissingDiagramSpecError / UnexpectedDiagramSpecError
# ---------------------------------------------------------------------------


def test_diagram_beat_missing_diagram_spec_fails_explicitly(engine, tmp_path):
    beats = [_visual_beat("V1", media_type="DIAGRAM", diagram_spec=None)]
    project_id, *_ = _create_project_ready_for_visual_rendering(engine, beats=beats)
    provider = FakeVisualProvider([])
    store = VisualFileStore(tmp_path / "visuals")
    renderer = VisualRenderer(engine, provider, _settings(), store)

    with pytest.raises(MissingDiagramSpecError):
        renderer.run(VisualRendererInput(project_id=project_id))

    assert provider.call_count == 0


def test_non_diagram_beat_with_diagram_spec_fails_explicitly(engine, tmp_path):
    beats = [
        _visual_beat("V1", media_type="GENERATED_STILL", diagram_spec=_diagram_spec())
    ]
    project_id, *_ = _create_project_ready_for_visual_rendering(engine, beats=beats)
    provider = FakeVisualProvider([])
    store = VisualFileStore(tmp_path / "visuals")
    renderer = VisualRenderer(engine, provider, _settings(), store)

    with pytest.raises(UnexpectedDiagramSpecError):
        renderer.run(VisualRendererInput(project_id=project_id))

    assert provider.call_count == 0


def test_validation_happens_before_any_rendering(engine, tmp_path):
    """A later beat's structural violation is still caught before an
    earlier, otherwise-valid beat renders anything."""
    from tests.test_visual_renderer import _visual_response

    beats = [
        _visual_beat("V1", media_type="GENERATED_STILL"),
        _visual_beat("V2", media_type="DIAGRAM", diagram_spec=None),
    ]
    project_id, *_ = _create_project_ready_for_visual_rendering(engine, beats=beats)
    provider = FakeVisualProvider([_visual_response()])
    store = VisualFileStore(tmp_path / "visuals")
    renderer = VisualRenderer(engine, provider, _settings(), store)

    with pytest.raises(MissingDiagramSpecError):
        renderer.run(VisualRendererInput(project_id=project_id))

    assert provider.call_count == 0
    assert list((tmp_path / "visuals").glob("**/*")) == []


# ---------------------------------------------------------------------------
# Render failure: no AI fallback, no substitution
# ---------------------------------------------------------------------------


def test_diagram_render_failure_propagates_and_records_failed_run(engine, tmp_path):
    beats = [_diagram_beat("V1")]
    project_id, script_plan, voice_plan, visual_plan = _create_project_ready_for_visual_rendering(
        engine, beats=beats
    )
    provider = FakeVisualProvider([])
    store = VisualFileStore(tmp_path / "visuals")

    # Sabotage: a directory sits exactly where DiagramRenderer must write
    # its output file, using the same deterministic path convention every
    # other rendered asset uses.
    blocking_path = store.root / str(project_id) / str(visual_plan.id) / "V1_R1.png"
    blocking_path.mkdir(parents=True)

    renderer = VisualRenderer(engine, provider, _settings(), store)

    with pytest.raises(DiagramRenderError):
        renderer.run(VisualRendererInput(project_id=project_id))

    assert provider.call_count == 0
    runs = list_module_runs_for_project(engine, project_id)
    render_runs = [run for run in runs if run.module == "visual_renderer"]
    assert len(render_runs) == 1
    assert render_runs[0].status == ModuleRunStatus.FAILED


# ---------------------------------------------------------------------------
# Lifecycle: ModuleRun / freshness preserved
# ---------------------------------------------------------------------------


def test_module_run_success_recorded_for_diagram_render(engine, tmp_path):
    beats = [_diagram_beat("V1")]
    project_id, *_ = _create_project_ready_for_visual_rendering(engine, beats=beats)
    provider = FakeVisualProvider([])
    store = VisualFileStore(tmp_path / "visuals")
    renderer = VisualRenderer(engine, provider, _settings(), store)

    result = renderer.run(VisualRendererInput(project_id=project_id))

    runs = list_module_runs_for_project(engine, project_id)
    render_runs = [run for run in runs if run.module == "visual_renderer"]
    assert len(render_runs) == 1
    assert render_runs[0].status == ModuleRunStatus.SUCCESS
    assert render_runs[0].output_id == str(result.manifest.id)


def test_freshness_gate_still_checked_before_diagram_rendering(engine, tmp_path):
    from app.engines.script.models import SCRIPT_PLAN_ARTIFACT_TYPE
    from app.renderers.visual.errors import StaleVoicePlanError
    from app.storage.artifacts import save_artifact
    from app.storage.projects import update_artifact_reference
    from tests.test_visual_renderer import _valid_script_plan

    beats = [_diagram_beat("V1")]
    project_id, script_plan_a, voice_plan, visual_plan = _create_project_ready_for_visual_rendering(
        engine, beats=beats
    )

    # Rerun ScriptPlan (a new id) without rerunning Voice/Visual Planning --
    # the stored VoicePlan is now stale against the project's current
    # ScriptPlan, exactly like tests/test_visual_renderer.py's own
    # test_stale_voice_plan_blocks_before_any_side_effect.
    script_plan_b = _valid_script_plan(("L001", "L002", "L003", "L004"))
    save_artifact(engine, project_id, SCRIPT_PLAN_ARTIFACT_TYPE, script_plan_b)
    update_artifact_reference(engine, project_id, "script_plan_id", script_plan_b.id)

    provider = FakeVisualProvider([])
    store = VisualFileStore(tmp_path / "visuals")
    renderer = VisualRenderer(engine, provider, _settings(), store)

    with pytest.raises(StaleVoicePlanError):
        renderer.run(VisualRendererInput(project_id=project_id))

    assert provider.call_count == 0
    assert list((tmp_path / "visuals").glob("**/*")) == []


def test_deterministic_repeated_diagram_output(engine, tmp_path):
    beats = [_diagram_beat("V1")]
    project_id, *_ = _create_project_ready_for_visual_rendering(engine, beats=beats)
    provider = FakeVisualProvider([])
    store = VisualFileStore(tmp_path / "visuals")
    renderer = VisualRenderer(engine, provider, _settings(), store)

    first = renderer.run(VisualRendererInput(project_id=project_id))
    first_bytes = (store.root / first.manifest.assets[0].file_path).read_bytes()
    second = renderer.run(VisualRendererInput(project_id=project_id))
    second_bytes = (store.root / second.manifest.assets[0].file_path).read_bytes()

    assert first_bytes == second_bytes


# ---------------------------------------------------------------------------
# Phase 24 beat-order/dependency behavior remains intact for DIAGRAM
# ---------------------------------------------------------------------------


def test_diagram_output_usable_as_ti_state_background_beat(engine, tmp_path):
    """DIAGRAM now produces a real RenderedVisualAsset unconditionally
    (Phase 25), so it remains a valid Phase 24 background_beat_id source
    -- confirms Phase 24's dependency machinery keeps working with a
    DIAGRAM beat as the dependency."""
    beats = [
        _diagram_beat("V1"),
        _visual_beat("V2", media_type="TI_STATE", ti_state="CURIOUS"),
    ]
    project_id, *_ = _create_project_ready_for_visual_rendering(engine, beats=beats)
    provider = FakeVisualProvider([])
    store = VisualFileStore(tmp_path / "visuals")
    ti_compositor = _ti_compositor(engine, tmp_path)
    renderer = VisualRenderer(engine, provider, _settings(), store, ti_compositor=ti_compositor)

    renderer_input = VisualRendererInput(
        project_id=project_id,
        ti_state_sources={
            "V2": TiStateSource(mode=TiStateRenderMode.COMPOSITE, background_beat_id="V1")
        },
    )

    result = renderer.run(renderer_input)

    assert provider.call_count == 0
    beat_ids = {a.beat_id for a in result.manifest.assets}
    assert beat_ids == {"V1", "V2"}


def test_diagram_beat_order_preserved_when_no_dependency_forces_reorder(engine, tmp_path):
    from tests.test_visual_renderer import _visual_response

    beats = [
        _visual_beat("V1", media_type="GENERATED_STILL"),
        _diagram_beat("V2"),
        _visual_beat("V3", media_type="ASSET_REUSE", reuse_key="k"),
    ]
    project_id, *_ = _create_project_ready_for_visual_rendering(engine, beats=beats)
    provider = FakeVisualProvider([_visual_response()])
    store = VisualFileStore(tmp_path / "visuals")
    renderer = VisualRenderer(engine, provider, _settings(), store)

    result = renderer.run(VisualRendererInput(project_id=project_id))

    assert [a.beat_id for a in result.manifest.assets] == ["V1", "V2"]
