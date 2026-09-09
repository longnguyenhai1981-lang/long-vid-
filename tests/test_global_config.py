from __future__ import annotations

import yaml
import pytest
from pydantic import ValidationError

from app.config.loader import DEFAULT_CONFIG_PATH, load_global_config


def _load_raw_config() -> dict:
    return yaml.safe_load(DEFAULT_CONFIG_PATH.read_text(encoding="utf-8"))


def test_valid_yaml_loads():
    config = load_global_config(DEFAULT_CONFIG_PATH)
    assert config.brand.name == "Một Tí Lý"
    assert config.character.name == "Tí"
    assert "keep_ti" in config.immutable_rules


def test_missing_file_fails_clearly(tmp_path):
    missing = tmp_path / "does_not_exist.yaml"
    with pytest.raises(FileNotFoundError, match="not found"):
        load_global_config(missing)


def test_malformed_yaml_fails_clearly(tmp_path):
    bad_file = tmp_path / "bad.yaml"
    bad_file.write_text("brand: [unclosed", encoding="utf-8")
    with pytest.raises(ValueError, match="not valid YAML"):
        load_global_config(bad_file)


def test_invalid_age_range_fails(tmp_path):
    raw = _load_raw_config()
    raw["audience"]["core_age"]["min"] = 40
    raw["audience"]["core_age"]["max"] = 20
    bad_file = tmp_path / "bad_age.yaml"
    bad_file.write_text(yaml.safe_dump(raw), encoding="utf-8")
    with pytest.raises(ValidationError):
        load_global_config(bad_file)


def test_invalid_production_duration_range_fails(tmp_path):
    raw = _load_raw_config()
    raw["production"]["target_duration_min"] = 20
    raw["production"]["target_duration_max"] = 5
    bad_file = tmp_path / "bad_duration.yaml"
    bad_file.write_text(yaml.safe_dump(raw), encoding="utf-8")
    with pytest.raises(ValidationError):
        load_global_config(bad_file)


def test_missing_required_brand_field_fails(tmp_path):
    raw = _load_raw_config()
    del raw["brand"]["signature_line"]
    bad_file = tmp_path / "bad_brand.yaml"
    bad_file.write_text(yaml.safe_dump(raw), encoding="utf-8")
    with pytest.raises(ValidationError):
        load_global_config(bad_file)
