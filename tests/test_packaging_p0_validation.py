from __future__ import annotations

from app.engines.packaging_p0.validation import validate_packaging_prototype
from app.models.packaging import PackagingPrototype


def _prototype(**overrides) -> PackagingPrototype:
    kwargs = dict(
        promise="A sturdy bridge tore itself apart in ordinary wind",
        title_direction="The bridge that shook itself to pieces",
        thumbnail_conflict="CAN WIND REALLY DO THIS?",
        viewer_expectation="An investigation into a real aerodynamic mechanism",
        risk_of_misleading="LOW",
    )
    kwargs.update(overrides)
    return PackagingPrototype(**kwargs)


def test_valid_prototype_has_no_issues():
    assert validate_packaging_prototype(_prototype()) == []


def test_blank_promise_is_flagged():
    issues = validate_packaging_prototype(_prototype(promise="   "))
    assert any("promise cannot be blank" in issue for issue in issues)


def test_blank_title_direction_is_flagged():
    issues = validate_packaging_prototype(_prototype(title_direction="   "))
    assert any("title_direction cannot be blank" in issue for issue in issues)


def test_blank_thumbnail_conflict_is_flagged():
    issues = validate_packaging_prototype(_prototype(thumbnail_conflict="   "))
    assert any("thumbnail_conflict cannot be blank" in issue for issue in issues)


def test_blank_viewer_expectation_is_flagged():
    issues = validate_packaging_prototype(_prototype(viewer_expectation="   "))
    assert any("viewer_expectation cannot be blank" in issue for issue in issues)


def test_multiple_blank_fields_all_reported_together():
    issues = validate_packaging_prototype(_prototype(promise="", title_direction=""))
    assert len(issues) == 2


def test_high_risk_is_never_a_validation_issue():
    """risk_of_misleading == HIGH is a legitimate, honestly-reported outcome --
    it must never trigger business-correction retry at the engine level."""
    assert validate_packaging_prototype(_prototype(risk_of_misleading="HIGH")) == []
