"""Phase 26 focused tests: VisualRenderer <-> VisualLayerCompositor
integration (VisualMediaType.COMPOSITION).

Reuses tests/test_visual_renderer.py's project-graph builders (a full
MVP_COMPLETE project with ScriptPlan/VoicePlan/VisualPlan already saved)
rather than re-deriving ~150 lines of unrelated fixture setup -- this file
only adds the COMPOSITION-specific scenarios Phase 26 introduces. See
tests/test_layer_compositor.py for the standalone VisualLayerCompositor
suite, and tests/test_visual_renderer_beat_composition.py /
tests/test_visual_renderer_diagram_integration.py for the Phase 24/25
suites this phase builds on and leaves unmodified in behavior.
"""

from __future__ import annotations

import pytest

from app.layer_compositor.errors import LayerCompositionError
from app.layer_compositor.models import LayerAnchor, LayerScale
from app.models.common import ModuleRunStatus
from app.renderers.visual.errors import (
    BackgroundBeatCycleError,
    CompositionSourceNotRenderedError,
    MissingCompositionSpecError,
    UnknownBackgroundBeatError,
)
from app.renderers.visual.models import (
    BackgroundLayerSource,
    CompositionSpec,
    OverlayLayerSource,
    TiStateRenderMode,
    TiStateSource,
    VisualRendererInput,
)
from app.renderers.visual.renderer import VisualRenderer
from app.storage.module_runs import list_module_runs_for_project
from app.visual.fake import FakeVisualProvider
from app.visual.storage import VisualFileStore
from tests.test_visual_renderer import (
    _create_project_ready_for_visual_rendering,
    _settings,
    _ti_compositor,
    _visual_beat,
)
from tests.test_visual_renderer_beat_composition import _real_still_response


def _composition_beat(beat_id="V4", **overrides):
    return _visual_beat(beat_id, media_type="COMPOSITION", **overrides)


def _background_and_diagram_spec(background_beat_id="V1", diagram_beat_id="V2") -> CompositionSpec:
    return CompositionSpec(
        layers=[
            BackgroundLayerSource(id="bg", source_beat_id=background_beat_id),
            OverlayLayerSource(
                id="diagram",
                source_beat_id=diagram_beat_id,
                anchor=LayerAnchor.TOP_LEFT,
                scale=LayerScale(relative_height=0.3),
            ),
        ]
    )


# ---------------------------------------------------------------------------
# background + DIAGRAM / background + Tí / background + DIAGRAM + Tí
# ---------------------------------------------------------------------------


def test_composition_background_plus_diagram(engine, tmp_path):
    beats = [
        _visual_beat("V1", media_type="GENERATED_STILL"),
        _visual_beat("V2", media_type="DIAGRAM"),
        _composition_beat("V4"),
    ]
    project_id, *_ = _create_project_ready_for_visual_rendering(engine, beats=beats)
    provider = FakeVisualProvider([_real_still_response()])
    store = VisualFileStore(tmp_path / "visuals")
    renderer = VisualRenderer(engine, provider, _settings(), store)

    renderer_input = VisualRendererInput(
        project_id=project_id, composition_specs={"V4": _background_and_diagram_spec()}
    )
    result = renderer.run(renderer_input)

    v4_asset = next(a for a in result.manifest.assets if a.beat_id == "V4")
    assert (store.root / v4_asset.file_path).is_file()
    assert provider.call_count == 1


def test_composition_background_plus_ti_state_standalone(engine, tmp_path):
    beats = [
        _visual_beat("V1", media_type="GENERATED_STILL"),
        _visual_beat("V3", media_type="TI_STATE", ti_state="CURIOUS"),
        _composition_beat("V4"),
    ]
    project_id, *_ = _create_project_ready_for_visual_rendering(engine, beats=beats)
    provider = FakeVisualProvider([_real_still_response()])
    store = VisualFileStore(tmp_path / "visuals")
    ti_compositor = _ti_compositor(engine, tmp_path)
    renderer = VisualRenderer(engine, provider, _settings(), store, ti_compositor=ti_compositor)

    composition_spec = CompositionSpec(
        layers=[
            BackgroundLayerSource(id="bg", source_beat_id="V1"),
            OverlayLayerSource(
                id="ti", source_beat_id="V3", anchor=LayerAnchor.BOTTOM_RIGHT, scale=LayerScale(relative_height=0.3)
            ),
        ]
    )
    renderer_input = VisualRendererInput(project_id=project_id, composition_specs={"V4": composition_spec})
    result = renderer.run(renderer_input)

    v4_asset = next(a for a in result.manifest.assets if a.beat_id == "V4")
    assert (store.root / v4_asset.file_path).is_file()
    # V3 STANDALONE never produces a RenderedVisualAsset -- it stays a
    # CANONICAL_ASSET_READY requirement, resolved directly by the
    # composition beat.
    assert {r.beat_id for r in result.manifest.requirements} == {"V3"}
    assert provider.call_count == 1


def test_composition_background_plus_diagram_plus_ti_state(engine, tmp_path):
    beats = [
        _visual_beat("V1", media_type="GENERATED_STILL"),
        _visual_beat("V2", media_type="DIAGRAM"),
        _visual_beat("V3", media_type="TI_STATE", ti_state="CURIOUS"),
        _composition_beat("V4"),
    ]
    project_id, *_ = _create_project_ready_for_visual_rendering(engine, beats=beats)
    provider = FakeVisualProvider([_real_still_response()])
    store = VisualFileStore(tmp_path / "visuals")
    ti_compositor = _ti_compositor(engine, tmp_path)
    renderer = VisualRenderer(engine, provider, _settings(), store, ti_compositor=ti_compositor)

    composition_spec = CompositionSpec(
        layers=[
            BackgroundLayerSource(id="bg", source_beat_id="V1"),
            OverlayLayerSource(
                id="diagram", source_beat_id="V2", z_index=1,
                anchor=LayerAnchor.TOP_LEFT, scale=LayerScale(relative_height=0.3),
            ),
            OverlayLayerSource(
                id="ti", source_beat_id="V3", z_index=2,
                anchor=LayerAnchor.BOTTOM_RIGHT, scale=LayerScale(relative_height=0.3),
            ),
        ]
    )
    renderer_input = VisualRendererInput(project_id=project_id, composition_specs={"V4": composition_spec})
    result = renderer.run(renderer_input)

    asset_beat_ids = {a.beat_id for a in result.manifest.assets}
    assert asset_beat_ids == {"V1", "V2", "V4"}
    assert {r.beat_id for r in result.manifest.requirements} == {"V3"}
    assert provider.call_count == 1
    assert result.provider_call_count == 1


# ---------------------------------------------------------------------------
# Authored order / topological ordering
# ---------------------------------------------------------------------------


def test_referenced_beats_may_appear_after_composition_beat_in_authored_order(engine, tmp_path):
    beats = [
        _composition_beat("V4"),
        _visual_beat("V3", media_type="TI_STATE", ti_state="CURIOUS"),
        _visual_beat("V2", media_type="DIAGRAM"),
        _visual_beat("V1", media_type="GENERATED_STILL"),
    ]
    project_id, *_ = _create_project_ready_for_visual_rendering(engine, beats=beats)
    provider = FakeVisualProvider([_real_still_response()])
    store = VisualFileStore(tmp_path / "visuals")
    ti_compositor = _ti_compositor(engine, tmp_path)
    renderer = VisualRenderer(engine, provider, _settings(), store, ti_compositor=ti_compositor)

    composition_spec = CompositionSpec(
        layers=[
            BackgroundLayerSource(id="bg", source_beat_id="V1"),
            OverlayLayerSource(id="diagram", source_beat_id="V2", scale=LayerScale(relative_height=0.3)),
            OverlayLayerSource(id="ti", source_beat_id="V3", scale=LayerScale(relative_height=0.3)),
        ]
    )
    renderer_input = VisualRendererInput(project_id=project_id, composition_specs={"V4": composition_spec})
    result = renderer.run(renderer_input)

    render_job_order = [asset.beat_id for asset in result.manifest.assets]
    assert render_job_order.index("V1") < render_job_order.index("V4")
    assert render_job_order.index("V2") < render_job_order.index("V4")
    assert provider.call_count == 1


def test_topological_ordering_preserves_unconstrained_authored_order(engine, tmp_path):
    beats = [
        _composition_beat("V4"),
        _visual_beat("V2", media_type="DIAGRAM"),
        _visual_beat("V1", media_type="GENERATED_STILL"),
    ]
    project_id, *_ = _create_project_ready_for_visual_rendering(engine, beats=beats)
    provider = FakeVisualProvider([_real_still_response()])
    store = VisualFileStore(tmp_path / "visuals")
    renderer = VisualRenderer(engine, provider, _settings(), store)

    renderer_input = VisualRendererInput(
        project_id=project_id, composition_specs={"V4": _background_and_diagram_spec()}
    )
    result = renderer.run(renderer_input)

    # V2 and V1 have no dependency between each other -- V2's authored
    # position (before V1) should be preserved among themselves.
    render_job_order = [asset.beat_id for asset in result.manifest.assets]
    assert render_job_order.index("V2") < render_job_order.index("V1")
    assert render_job_order[-1] == "V4"


# ---------------------------------------------------------------------------
# Failure behavior
# ---------------------------------------------------------------------------


def test_missing_composition_spec_fails_explicitly(engine, tmp_path):
    beats = [_visual_beat("V1", media_type="GENERATED_STILL"), _composition_beat("V4")]
    project_id, *_ = _create_project_ready_for_visual_rendering(engine, beats=beats)
    provider = FakeVisualProvider([])
    store = VisualFileStore(tmp_path / "visuals")
    renderer = VisualRenderer(engine, provider, _settings(), store)

    with pytest.raises(MissingCompositionSpecError):
        renderer.run(VisualRendererInput(project_id=project_id))

    assert provider.call_count == 0
    assert list((tmp_path / "visuals").glob("**/*")) == []


def test_unknown_source_beat_fails_explicitly(engine, tmp_path):
    beats = [_visual_beat("V1", media_type="GENERATED_STILL"), _composition_beat("V4")]
    project_id, *_ = _create_project_ready_for_visual_rendering(engine, beats=beats)
    provider = FakeVisualProvider([])
    store = VisualFileStore(tmp_path / "visuals")
    renderer = VisualRenderer(engine, provider, _settings(), store)

    composition_spec = CompositionSpec(layers=[BackgroundLayerSource(id="bg", source_beat_id="V999")])
    renderer_input = VisualRendererInput(project_id=project_id, composition_specs={"V4": composition_spec})

    with pytest.raises(UnknownBackgroundBeatError):
        renderer.run(renderer_input)

    assert provider.call_count == 0


def test_requirement_without_resolvable_raster_fails_explicitly(engine, tmp_path):
    beats = [_visual_beat("V1", media_type="ASSET_REUSE", reuse_key="k"), _composition_beat("V4")]
    project_id, *_ = _create_project_ready_for_visual_rendering(engine, beats=beats)
    provider = FakeVisualProvider([])
    store = VisualFileStore(tmp_path / "visuals")
    renderer = VisualRenderer(engine, provider, _settings(), store)

    composition_spec = CompositionSpec(layers=[BackgroundLayerSource(id="bg", source_beat_id="V1")])
    renderer_input = VisualRendererInput(project_id=project_id, composition_specs={"V4": composition_spec})

    with pytest.raises(CompositionSourceNotRenderedError):
        renderer.run(renderer_input)

    assert provider.call_count == 0


def test_self_reference_fails_explicitly(engine, tmp_path):
    from app.renderers.visual.errors import SelfReferentialBackgroundBeatError

    beats = [
        _composition_beat("V4"),
        _visual_beat("V1", media_type="GENERATED_STILL"),
    ]
    project_id, *_ = _create_project_ready_for_visual_rendering(engine, beats=beats)
    provider = FakeVisualProvider([])
    store = VisualFileStore(tmp_path / "visuals")
    renderer = VisualRenderer(engine, provider, _settings(), store)

    # V4 depends on itself via a layer -- a direct self-reference.
    composition_spec = CompositionSpec(
        layers=[
            BackgroundLayerSource(id="bg", source_beat_id="V1"),
            OverlayLayerSource(id="self", source_beat_id="V4"),
        ]
    )
    renderer_input = VisualRendererInput(project_id=project_id, composition_specs={"V4": composition_spec})

    with pytest.raises(SelfReferentialBackgroundBeatError):
        renderer.run(renderer_input)

    assert provider.call_count == 0


def test_dependency_cycle_fails_explicitly(engine, tmp_path):
    # A genuine 2-beat cycle: V4's background is V5, V5's background is V4.
    beats = [_composition_beat("V4"), _composition_beat("V5")]
    project_id, *_ = _create_project_ready_for_visual_rendering(engine, beats=beats)
    provider = FakeVisualProvider([])
    store = VisualFileStore(tmp_path / "visuals")
    renderer = VisualRenderer(engine, provider, _settings(), store)

    renderer_input = VisualRendererInput(
        project_id=project_id,
        composition_specs={
            "V4": CompositionSpec(layers=[BackgroundLayerSource(id="bg", source_beat_id="V5")]),
            "V5": CompositionSpec(layers=[BackgroundLayerSource(id="bg", source_beat_id="V4")]),
        },
    )

    with pytest.raises(BackgroundBeatCycleError):
        renderer.run(renderer_input)

    assert provider.call_count == 0


def test_oversized_overlay_propagates_layer_composition_error(engine, tmp_path):
    from app.models.diagram import DiagramCanvas, DiagramLine, DiagramPoint, DiagramSpec

    # A very wide (300x50) diagram: scaling it to fill the 100x100
    # background's full height (relative_height=1.0) forces its width far
    # past the background's own width -- no position could ever fit it.
    wide_diagram_spec = DiagramSpec(
        canvas=DiagramCanvas(width=300, height=50),
        elements=[DiagramLine(start=DiagramPoint(x=0.1, y=0.5), end=DiagramPoint(x=0.9, y=0.5))],
    )
    beats = [
        _visual_beat("V1", media_type="GENERATED_STILL"),
        _visual_beat("V2", media_type="DIAGRAM", diagram_spec=wide_diagram_spec),
        _composition_beat("V4"),
    ]
    project_id, *_ = _create_project_ready_for_visual_rendering(engine, beats=beats)
    provider = FakeVisualProvider([_real_still_response(width=100, height=100)])
    store = VisualFileStore(tmp_path / "visuals")
    renderer = VisualRenderer(engine, provider, _settings(), store)

    composition_spec = CompositionSpec(
        layers=[
            BackgroundLayerSource(id="bg", source_beat_id="V1"),
            OverlayLayerSource(id="diagram", source_beat_id="V2", scale=LayerScale(relative_height=1.0)),
        ]
    )
    renderer_input = VisualRendererInput(project_id=project_id, composition_specs={"V4": composition_spec})

    with pytest.raises(LayerCompositionError):
        renderer.run(renderer_input)

    runs = list_module_runs_for_project(engine, project_id)
    render_runs = [run for run in runs if run.module == "visual_renderer"]
    assert render_runs[0].status == ModuleRunStatus.FAILED


# ---------------------------------------------------------------------------
# Provider-call behavior / manifest / determinism / lifecycle
# ---------------------------------------------------------------------------


def test_provider_call_count_unchanged_by_composition(engine, tmp_path):
    beats = [
        _visual_beat("V1", media_type="GENERATED_STILL"),
        _visual_beat("V2", media_type="DIAGRAM"),
        _visual_beat("V3", media_type="TI_STATE", ti_state="CURIOUS"),
        _composition_beat("V4"),
    ]
    project_id, *_ = _create_project_ready_for_visual_rendering(engine, beats=beats)
    provider = FakeVisualProvider([_real_still_response()])
    store = VisualFileStore(tmp_path / "visuals")
    ti_compositor = _ti_compositor(engine, tmp_path)
    renderer = VisualRenderer(engine, provider, _settings(), store, ti_compositor=ti_compositor)

    composition_spec = CompositionSpec(
        layers=[
            BackgroundLayerSource(id="bg", source_beat_id="V1"),
            OverlayLayerSource(id="diagram", source_beat_id="V2", scale=LayerScale(relative_height=0.3)),
            OverlayLayerSource(id="ti", source_beat_id="V3", scale=LayerScale(relative_height=0.3)),
        ]
    )
    renderer_input = VisualRendererInput(project_id=project_id, composition_specs={"V4": composition_spec})
    result = renderer.run(renderer_input)

    assert provider.call_count == 1
    assert result.provider_call_count == 1


def test_manifest_keeps_source_assets_and_final_composite(engine, tmp_path):
    beats = [
        _visual_beat("V1", media_type="GENERATED_STILL"),
        _visual_beat("V2", media_type="DIAGRAM"),
        _composition_beat("V4"),
    ]
    project_id, *_ = _create_project_ready_for_visual_rendering(engine, beats=beats)
    provider = FakeVisualProvider([_real_still_response()])
    store = VisualFileStore(tmp_path / "visuals")
    renderer = VisualRenderer(engine, provider, _settings(), store)

    renderer_input = VisualRendererInput(
        project_id=project_id, composition_specs={"V4": _background_and_diagram_spec()}
    )
    result = renderer.run(renderer_input)

    asset_beat_ids = {a.beat_id for a in result.manifest.assets}
    assert asset_beat_ids == {"V1", "V2", "V4"}
    file_paths = [a.file_path for a in result.manifest.assets]
    assert len(file_paths) == len(set(file_paths))


def test_deterministic_rerun(engine, tmp_path):
    beats = [
        _visual_beat("V1", media_type="GENERATED_STILL"),
        _visual_beat("V2", media_type="DIAGRAM"),
        _composition_beat("V4"),
    ]
    project_id, *_ = _create_project_ready_for_visual_rendering(engine, beats=beats)
    provider = FakeVisualProvider([_real_still_response(), _real_still_response()])
    store = VisualFileStore(tmp_path / "visuals")
    renderer = VisualRenderer(engine, provider, _settings(), store)

    renderer_input = VisualRendererInput(
        project_id=project_id, composition_specs={"V4": _background_and_diagram_spec()}
    )

    first = renderer.run(renderer_input)
    first_bytes = (store.root / next(a for a in first.manifest.assets if a.beat_id == "V4").file_path).read_bytes()
    second = renderer.run(renderer_input)
    second_bytes = (store.root / next(a for a in second.manifest.assets if a.beat_id == "V4").file_path).read_bytes()

    assert first_bytes == second_bytes


def test_module_run_success_recorded_for_composition(engine, tmp_path):
    beats = [
        _visual_beat("V1", media_type="GENERATED_STILL"),
        _visual_beat("V2", media_type="DIAGRAM"),
        _composition_beat("V4"),
    ]
    project_id, *_ = _create_project_ready_for_visual_rendering(engine, beats=beats)
    provider = FakeVisualProvider([_real_still_response()])
    store = VisualFileStore(tmp_path / "visuals")
    renderer = VisualRenderer(engine, provider, _settings(), store)

    renderer_input = VisualRendererInput(
        project_id=project_id, composition_specs={"V4": _background_and_diagram_spec()}
    )
    result = renderer.run(renderer_input)

    runs = list_module_runs_for_project(engine, project_id)
    render_runs = [run for run in runs if run.module == "visual_renderer"]
    assert len(render_runs) == 1
    assert render_runs[0].status == ModuleRunStatus.SUCCESS
    assert render_runs[0].output_id == str(result.manifest.id)


def test_freshness_gate_preserved_for_composition(engine, tmp_path):
    from app.engines.script.models import SCRIPT_PLAN_ARTIFACT_TYPE
    from app.renderers.visual.errors import StaleVoicePlanError
    from app.storage.artifacts import save_artifact
    from app.storage.projects import update_artifact_reference
    from tests.test_visual_renderer import _valid_script_plan

    beats = [
        _visual_beat("V1", media_type="GENERATED_STILL"),
        _visual_beat("V2", media_type="DIAGRAM"),
        _composition_beat("V4"),
    ]
    project_id, *_ = _create_project_ready_for_visual_rendering(engine, beats=beats)

    script_plan_b = _valid_script_plan(("L001", "L002", "L003", "L004"))
    save_artifact(engine, project_id, SCRIPT_PLAN_ARTIFACT_TYPE, script_plan_b)
    update_artifact_reference(engine, project_id, "script_plan_id", script_plan_b.id)

    provider = FakeVisualProvider([])
    store = VisualFileStore(tmp_path / "visuals")
    renderer = VisualRenderer(engine, provider, _settings(), store)

    renderer_input = VisualRendererInput(
        project_id=project_id, composition_specs={"V4": _background_and_diagram_spec()}
    )

    with pytest.raises(StaleVoicePlanError):
        renderer.run(renderer_input)

    assert provider.call_count == 0
    assert list((tmp_path / "visuals").glob("**/*")) == []
