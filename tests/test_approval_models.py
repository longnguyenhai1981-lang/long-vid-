from __future__ import annotations

from datetime import datetime, timezone

import pytest
from pydantic import ValidationError

from app.models.approval import HumanApproval


def test_valid_approval():
    approval = HumanApproval(
        project_id="12345678-1234-1234-1234-123456789012",
        stage="IDEA_REVIEW",
        status="APPROVED",
        created_at=datetime.now(timezone.utc),
    )
    assert approval.status.value == "APPROVED"


def test_invalid_status_rejects():
    with pytest.raises(ValidationError):
        HumanApproval(
            project_id="12345678-1234-1234-1234-123456789012",
            stage="IDEA_REVIEW",
            status="MAYBE",
            created_at=datetime.now(timezone.utc),
        )


def test_naive_created_at_rejects():
    with pytest.raises(ValidationError):
        HumanApproval(
            project_id="12345678-1234-1234-1234-123456789012",
            stage="IDEA_REVIEW",
            status="APPROVED",
            created_at=datetime.now(),
        )
