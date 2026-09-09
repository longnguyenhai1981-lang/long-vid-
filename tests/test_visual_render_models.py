from __future__ import annotations

from datetime import datetime, timezone
from uuid import uuid4

import pytest
from pydantic import ValidationError

from app.models.common import VisualRequirementStatus
from app.models.visual_render import RenderedVisualAsset, VisualRenderManifest, VisualRenderRequirement


def test_visual_requirement_status_has_exactly_four_values():
    assert {member.value for member in VisualRequirementStatus} == {
        "RENDERED",
        "EXTERNAL_REQUIRED",
        "REUSE_ONLY",
        "CANONICAL_ASSET_READY",
    }


# ---------------------------------------------------------------------------
# RenderedVisualAsset
# ---------------------------------------------------------------------------


def _asset(**overrides) -> RenderedVisualAsset:
    fields = dict(
        render_job_id="V001_R1",
        beat_id="V001",
        media_type="GENERATED_STILL",
        file_path="proj/plan/V001_R1.png",
    )
    fields.update(overrides)
    return RenderedVisualAsset(**fields)


def test_rendered_visual_asset_constructs_with_required_fields():
    asset = _asset()
    assert asset.width is None
    assert asset.height is None
    assert asset.provider_request_id is None


def test_rendered_visual_asset_rejects_blank_render_job_id():
    with pytest.raises(ValidationError):
        _asset(render_job_id="  ")


def test_rendered_visual_asset_rejects_blank_beat_id():
    with pytest.raises(ValidationError):
        _asset(beat_id="  ")


def test_rendered_visual_asset_rejects_blank_file_path():
    with pytest.raises(ValidationError):
        _asset(file_path="  ")


@pytest.mark.parametrize("field", ["width", "height"])
def test_rendered_visual_asset_rejects_non_positive_dimension(field):
    with pytest.raises(ValidationError):
        _asset(**{field: 0})


def test_rendered_visual_asset_accepts_positive_dimensions():
    asset = _asset(width=512, height=384)
    assert asset.width == 512
    assert asset.height == 384


def test_rendered_visual_asset_rejects_unknown_field():
    with pytest.raises(ValidationError):
        _asset(not_a_real_field="oops")


def test_rendered_visual_asset_accepts_resolved_ti_state_for_ti_state_media_type():
    asset = _asset(media_type="TI_STATE", resolved_ti_state="PANIC")
    assert asset.resolved_ti_state.value == "PANIC"


def test_rendered_visual_asset_rejects_resolved_ti_state_for_non_ti_state_media_type():
    with pytest.raises(ValidationError):
        _asset(media_type="GENERATED_STILL", resolved_ti_state="PANIC")


# ---------------------------------------------------------------------------
# VisualRenderRequirement
# ---------------------------------------------------------------------------


def _requirement(**overrides) -> VisualRenderRequirement:
    fields = dict(beat_id="V002", media_type="ASSET_REUSE", status="REUSE_ONLY", reference="key-001")
    fields.update(overrides)
    return VisualRenderRequirement(**fields)


def test_visual_render_requirement_constructs_with_required_fields():
    req = _requirement()
    assert req.notes is None
    assert req.resolved_ti_state is None


def test_visual_render_requirement_rejects_blank_beat_id():
    with pytest.raises(ValidationError):
        _requirement(beat_id="  ")


def test_visual_render_requirement_rejects_rendered_status():
    with pytest.raises(ValidationError):
        _requirement(status="RENDERED")


def test_visual_render_requirement_accepts_canonical_asset_ready_status_for_ti_state():
    req = _requirement(
        media_type="TI_STATE",
        status="CANONICAL_ASSET_READY",
        reference="asset-set/v1/NEUTRAL.png",
        resolved_ti_state="NEUTRAL",
    )
    assert req.status.value == "CANONICAL_ASSET_READY"
    assert req.resolved_ti_state.value == "NEUTRAL"


def test_visual_render_requirement_rejects_resolved_ti_state_for_non_ti_state_media_type():
    with pytest.raises(ValidationError):
        _requirement(resolved_ti_state="NEUTRAL")


def test_visual_render_requirement_rejects_unknown_field():
    with pytest.raises(ValidationError):
        _requirement(not_a_real_field="oops")


# ---------------------------------------------------------------------------
# VisualRenderManifest
# ---------------------------------------------------------------------------


def _manifest(**overrides) -> VisualRenderManifest:
    fields = dict(
        script_plan_id=uuid4(),
        voice_plan_id=uuid4(),
        visual_plan_id=uuid4(),
        provider="fake-visual",
        output_format="PNG",
        assets=[_asset()],
        requirements=[],
        created_at=datetime.now(timezone.utc),
    )
    fields.update(overrides)
    return VisualRenderManifest(**fields)


def test_visual_render_manifest_constructs_with_assets_only():
    manifest = _manifest()
    assert manifest.id is not None
    assert len(manifest.assets) == 1
    assert manifest.requirements == []


def test_visual_render_manifest_constructs_with_requirements_only():
    manifest = _manifest(assets=[], requirements=[_requirement()])
    assert manifest.assets == []
    assert len(manifest.requirements) == 1


def test_visual_render_manifest_rejects_no_assets_and_no_requirements():
    with pytest.raises(ValidationError):
        _manifest(assets=[], requirements=[])


def test_visual_render_manifest_rejects_blank_provider():
    with pytest.raises(ValidationError):
        _manifest(provider="  ")


def test_visual_render_manifest_rejects_naive_created_at():
    with pytest.raises(ValidationError):
        _manifest(created_at=datetime(2026, 1, 1))


def test_visual_render_manifest_rejects_unknown_field():
    with pytest.raises(ValidationError):
        _manifest(not_a_real_field="oops")
