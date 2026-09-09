from __future__ import annotations

import pytest
from pydantic import ValidationError

from app.models.packaging import PackagingPrototype


def test_valid_packaging_prototype():
    prototype = PackagingPrototype(
        promise="Bạn sẽ hiểu vì sao kháng sinh đang mất tác dụng",
        title_direction="Vì sao thuốc kháng sinh sắp hết thời?",
        thumbnail_conflict="Viên thuốc vs vi khuẩn khổng lồ",
        viewer_expectation="Giải thích khoa học dễ hiểu",
        risk_of_misleading="LOW",
    )
    assert prototype.risk_of_misleading.value == "LOW"


def test_invalid_risk_level_rejects():
    with pytest.raises(ValidationError):
        PackagingPrototype(
            promise="p",
            title_direction="t",
            thumbnail_conflict="c",
            viewer_expectation="v",
            risk_of_misleading="EXTREME",
        )
