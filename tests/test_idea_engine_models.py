from __future__ import annotations

import uuid

import pytest
from pydantic import ValidationError

from app.engines.idea.models import DiscoveryMode, IdeaEngineInput


def test_valid_open_mode_without_seed():
    engine_input = IdeaEngineInput(project_id=uuid.uuid4(), discovery_mode=DiscoveryMode.OPEN)
    assert engine_input.seed is None
    assert engine_input.domain == "physics"


def test_valid_expand_mode_with_seed():
    engine_input = IdeaEngineInput(
        project_id=uuid.uuid4(), discovery_mode=DiscoveryMode.EXPAND, seed="Tacoma Narrows Bridge"
    )
    assert engine_input.seed == "Tacoma Narrows Bridge"


def test_expand_mode_without_seed_rejected():
    with pytest.raises(ValidationError):
        IdeaEngineInput(project_id=uuid.uuid4(), discovery_mode=DiscoveryMode.EXPAND)


def test_expand_mode_with_blank_seed_rejected():
    with pytest.raises(ValidationError):
        IdeaEngineInput(project_id=uuid.uuid4(), discovery_mode=DiscoveryMode.EXPAND, seed="   ")


def test_blank_domain_rejected():
    with pytest.raises(ValidationError):
        IdeaEngineInput(project_id=uuid.uuid4(), discovery_mode=DiscoveryMode.OPEN, domain="  ")


def test_additional_context_optional():
    engine_input = IdeaEngineInput(project_id=uuid.uuid4(), discovery_mode=DiscoveryMode.OPEN)
    assert engine_input.additional_context is None


def test_invalid_discovery_mode_rejected():
    with pytest.raises(ValidationError):
        IdeaEngineInput(project_id=uuid.uuid4(), discovery_mode="SOMETHING_ELSE")


def test_idea_engine_input_does_not_select_provider_or_model():
    assert "provider" not in IdeaEngineInput.model_fields
    assert "model" not in IdeaEngineInput.model_fields
