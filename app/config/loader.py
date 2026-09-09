"""Loading and validation for the Motily GlobalConfig contract."""

from __future__ import annotations

from pathlib import Path
from typing import Any

import yaml
from pydantic import BaseModel, ConfigDict, model_validator

from app.models.common import Confidence, ScienceDepth


class Brand(BaseModel):
    model_config = ConfigDict(extra="forbid")
    name: str
    signature_line: str


class AgeRange(BaseModel):
    model_config = ConfigDict(extra="forbid")
    min: int
    max: int

    @model_validator(mode="after")
    def _check_range(self) -> "AgeRange":
        if self.min > self.max:
            raise ValueError("audience.core_age.min must be <= audience.core_age.max")
        return self


class Audience(BaseModel):
    model_config = ConfigDict(extra="forbid")
    core_age: AgeRange
    prerequisite: str
    general_audience: bool


class Roles(BaseModel):
    model_config = ConfigDict(extra="forbid")
    narrator: bool
    investigator: bool
    character: bool


class Language(BaseModel):
    model_config = ConfigDict(extra="forbid")
    audience_address: str
    self_reference: str


class Profanity(BaseModel):
    model_config = ConfigDict(extra="forbid")
    allowed: bool
    censored_only: bool
    intensity: str


class Humor(BaseModel):
    model_config = ConfigDict(extra="forbid")
    enabled: bool
    profanity: Profanity


class Character(BaseModel):
    model_config = ConfigDict(extra="forbid")
    name: str
    core_trait: str
    roles: Roles
    language: Language
    humor: Humor


class Science(BaseModel):
    model_config = ConfigDict(extra="forbid")
    default_depth: ScienceDepth
    accuracy_priority: Confidence
    allow_strategic_incompleteness: bool


class Content(BaseModel):
    model_config = ConfigDict(extra="forbid")
    story_first: bool
    drama_allowed: bool
    misleading_clickbait: bool


class Visual(BaseModel):
    model_config = ConfigDict(extra="forbid")
    identity: str
    footage_dependency: str
    character_animation: str


class Production(BaseModel):
    model_config = ConfigDict(extra="forbid")
    target_duration_min: int
    target_duration_max: int
    target_production_days: int
    editor: str

    @model_validator(mode="after")
    def _check_duration_range(self) -> "Production":
        if self.target_duration_min > self.target_duration_max:
            raise ValueError(
                "production.target_duration_min must be <= production.target_duration_max"
            )
        return self


class Optimization(BaseModel):
    model_config = ConfigDict(extra="forbid")
    primary_goals: list[str]


class GlobalConfig(BaseModel):
    """Read-only brand/product contract. Nothing in Phase 1 mutates this."""

    model_config = ConfigDict(extra="forbid")
    brand: Brand
    audience: Audience
    character: Character
    science: Science
    content: Content
    visual: Visual
    production: Production
    optimization: Optimization
    immutable_rules: list[str]


DEFAULT_CONFIG_PATH = Path(__file__).parent / "global.yaml"


def load_global_config(path: Path | str = DEFAULT_CONFIG_PATH) -> GlobalConfig:
    """Load and validate the GlobalConfig contract from a YAML file.

    Raises FileNotFoundError if the file is missing and ValueError if the
    file is not valid YAML or does not contain a mapping. Schema violations
    (missing required fields, invalid ranges, etc.) raise
    pydantic.ValidationError from GlobalConfig.model_validate.
    """
    config_path = Path(path)
    if not config_path.exists():
        raise FileNotFoundError(f"Global config file not found: {config_path}")

    try:
        raw: Any = yaml.safe_load(config_path.read_text(encoding="utf-8"))
    except yaml.YAMLError as exc:
        raise ValueError(f"Global config file is not valid YAML: {config_path}") from exc

    if not isinstance(raw, dict):
        raise ValueError(f"Global config file must contain a YAML mapping: {config_path}")

    return GlobalConfig.model_validate(raw)
