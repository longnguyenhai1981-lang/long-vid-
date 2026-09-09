from __future__ import annotations

import pytest
from pydantic import ValidationError

from app.models.research import Claim, ResearchPackage, Source


def test_valid_claim():
    claim = Claim(
        claim_id="C001",
        claim="Kháng sinh giết vi khuẩn bằng cách phá vỡ thành tế bào",
        status="SAFE",
        confidence="HIGH",
        source_ids=["S001"],
    )
    assert claim.status.value == "SAFE"


def test_invalid_claim_status_rejects():
    with pytest.raises(ValidationError):
        Claim(
            claim_id="C001",
            claim="text",
            status="NOT_A_STATUS",
            confidence="HIGH",
        )


def test_invalid_source_quality_tier_rejects():
    with pytest.raises(ValidationError):
        Source(
            source_id="S001",
            title="Nature paper",
            type="paper",
            quality_tier=4,
            authoritative=True,
        )


def test_research_package_round_trip():
    package = ResearchPackage(
        central_question="Vì sao vi khuẩn kháng thuốc?",
        executive_summary="Tóm tắt nghiên cứu",
        physics_core="Chọn lọc tự nhiên",
        simplification_boundary="Không đơn giản hoá cơ chế di truyền",
        prohibited_claims=["Kháng sinh chữa được mọi bệnh nhiễm trùng"],
        claims=[
            Claim(
                claim_id="C001",
                claim="Kháng sinh giết vi khuẩn",
                status="SAFE",
                confidence="HIGH",
            )
        ],
        sources=[
            Source(
                source_id="S001",
                title="Nature paper",
                type="paper",
                quality_tier=1,
                authoritative=True,
            )
        ],
    )
    dumped = package.model_dump_json()
    restored = ResearchPackage.model_validate_json(dumped)
    assert restored == package
