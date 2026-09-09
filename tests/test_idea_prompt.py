from __future__ import annotations

import uuid

from app.config.loader import load_global_config
from app.engines.idea.models import DiscoveryMode, IdeaEngineInput
from app.engines.idea.prompt import build_system_prompt, build_user_prompt


def _global_config():
    return load_global_config()


def test_system_prompt_includes_brand_name():
    assert "Một Tí Lý" in build_system_prompt(_global_config())


def test_system_prompt_includes_audience_range():
    prompt = build_system_prompt(_global_config())
    assert "16" in prompt and "30" in prompt


def test_system_prompt_includes_story_first_rule():
    assert "story-first" in build_system_prompt(_global_config()).lower()


def test_system_prompt_includes_physics_gravity_rule():
    prompt = build_system_prompt(_global_config()).lower()
    assert "physics" in prompt
    assert "central question" in prompt


def test_system_prompt_includes_abt_framework():
    prompt = build_system_prompt(_global_config())
    assert "and_context" in prompt
    assert "but_complication" in prompt
    assert "therefore_investigation" in prompt


def test_system_prompt_includes_primary_payoff_choices():
    prompt = build_system_prompt(_global_config())
    for payoff in ("EXPLANATION", "DISCOVERY", "REVERSAL", "HUMAN_INGENUITY"):
        assert payoff in prompt


def test_system_prompt_forbids_script_writing():
    prompt = build_system_prompt(_global_config()).lower()
    assert "script" in prompt
    assert "do not include" in prompt


def test_system_prompt_asks_for_one_recommendation_not_a_menu():
    prompt = build_system_prompt(_global_config()).lower()
    assert "one recommended" in prompt or "exactly one" in prompt


def test_user_prompt_includes_seed_in_expand_mode():
    engine_input = IdeaEngineInput(
        project_id=uuid.uuid4(), discovery_mode=DiscoveryMode.EXPAND, seed="Tacoma Narrows Bridge"
    )
    prompt = build_user_prompt(engine_input)
    assert "Tacoma Narrows Bridge" in prompt
    assert "EXPAND" in prompt


def test_user_prompt_open_mode_has_no_seed_line_but_instructs_discovery():
    engine_input = IdeaEngineInput(project_id=uuid.uuid4(), discovery_mode=DiscoveryMode.OPEN)
    prompt = build_user_prompt(engine_input)
    assert "OPEN" in prompt
    assert "Seed:" not in prompt
    assert "discover" in prompt.lower()


def test_user_prompt_includes_additional_context_when_supplied():
    engine_input = IdeaEngineInput(
        project_id=uuid.uuid4(),
        discovery_mode=DiscoveryMode.OPEN,
        additional_context="Focus on bridges and structural failure.",
    )
    prompt = build_user_prompt(engine_input)
    assert "Focus on bridges and structural failure." in prompt


def test_user_prompt_omits_additional_context_when_absent():
    engine_input = IdeaEngineInput(project_id=uuid.uuid4(), discovery_mode=DiscoveryMode.OPEN)
    prompt = build_user_prompt(engine_input)
    assert "Additional context" not in prompt
