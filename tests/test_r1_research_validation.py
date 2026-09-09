from __future__ import annotations

from app.engines.research_r1.validation import validate_research_package
from app.models.research import Claim, ResearchPackage, Source
from app.research.models import RetrievedSource


def _package(claims=None, sources=None, **overrides) -> ResearchPackage:
    kwargs = dict(
        central_question="Why did the bridge collapse?",
        executive_summary="Summary",
        physics_core="Flutter",
        simplification_boundary="Boundary text",
        claims=claims or [],
        sources=sources or [],
    )
    kwargs.update(overrides)
    return ResearchPackage(**kwargs)


def _claim(claim_id="C001", status="SAFE", source_ids=None) -> Claim:
    return Claim(
        claim_id=claim_id,
        claim="Flutter is a self-excited oscillation",
        status=status,
        confidence="HIGH",
        source_ids=source_ids or [],
    )


def _source(source_id="S001", url="https://real.example/source", supports_claims=None) -> Source:
    return Source(
        source_id=source_id,
        title="Real Paper",
        url=url,
        type="paper",
        quality_tier=1,
        authoritative=True,
        supports_claims=supports_claims or [],
    )


def _evidence(urls: list[str]) -> list[RetrievedSource]:
    return [RetrievedSource(title=f"Source {i}", url=u) for i, u in enumerate(urls, start=1)]


def test_valid_package_has_no_issues():
    package = _package(
        claims=[_claim(source_ids=["S001"])],
        sources=[_source(supports_claims=["C001"])],
    )
    assert validate_research_package(package, _evidence(["https://real.example/source"])) == []


def test_unknown_source_url_is_flagged():
    package = _package(sources=[_source(url="https://invented.example/fake")])
    issues = validate_research_package(package, _evidence(["https://real.example/source"]))
    assert any("URL not present" in issue for issue in issues)


def test_source_with_no_url_is_not_flagged():
    package = _package(sources=[_source(url=None)])
    issues = validate_research_package(package, _evidence(["https://real.example/source"]))
    assert issues == []


def test_broken_claim_source_id_is_flagged():
    package = _package(claims=[_claim(source_ids=["S999"])])
    issues = validate_research_package(package, [])
    assert any("unknown source_id" in issue for issue in issues)


def test_broken_source_claim_id_is_flagged():
    package = _package(sources=[_source(supports_claims=["C999"])])
    issues = validate_research_package(package, _evidence(["https://real.example/source"]))
    assert any("unknown claim_id" in issue for issue in issues)


def test_duplicate_claim_id_is_flagged():
    package = _package(
        claims=[_claim(claim_id="C001", status="UNCERTAIN"), _claim(claim_id="C001", status="UNCERTAIN")]
    )
    issues = validate_research_package(package, [])
    assert any("Duplicate claim_id" in issue for issue in issues)


def test_duplicate_source_id_is_flagged():
    package = _package(
        sources=[_source(source_id="S001"), _source(source_id="S001")]
    )
    issues = validate_research_package(package, _evidence(["https://real.example/source"]))
    assert any("Duplicate source_id" in issue for issue in issues)


def test_safe_claim_without_source_requires_evidence():
    package = _package(claims=[_claim(status="SAFE", source_ids=[])])
    issues = validate_research_package(package, [])
    assert any("require at least one" in issue for issue in issues)


def test_qualified_claim_without_source_requires_evidence():
    package = _package(claims=[_claim(status="QUALIFIED", source_ids=[])])
    issues = validate_research_package(package, [])
    assert any("require at least one" in issue for issue in issues)


def test_disputed_claim_without_source_requires_evidence():
    package = _package(claims=[_claim(status="DISPUTED", source_ids=[])])
    issues = validate_research_package(package, [])
    assert any("require at least one" in issue for issue in issues)


def test_uncertain_claim_without_source_is_allowed():
    package = _package(claims=[_claim(status="UNCERTAIN", source_ids=[])])
    assert validate_research_package(package, []) == []


def test_prohibited_claim_without_source_is_allowed():
    package = _package(claims=[_claim(status="PROHIBITED", source_ids=[])])
    assert validate_research_package(package, []) == []


def test_multiple_issues_all_reported_together():
    package = _package(
        claims=[_claim(claim_id="C001", status="SAFE", source_ids=["S999"])],
        sources=[_source(source_id="S001", url="https://invented.example/fake")],
    )
    issues = validate_research_package(package, _evidence(["https://real.example/source"]))
    assert len(issues) >= 2
