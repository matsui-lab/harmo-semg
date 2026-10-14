import importlib.util
from pathlib import Path

import yaml

from harmo.config.schema import validate_config


ROOT = Path(__file__).resolve().parents[1]


def _configs():
    for path in sorted((ROOT / "studies").glob("*/config.yaml")):
        yield path, yaml.safe_load(path.read_text(encoding="utf-8")) or {}


def test_all_active_study_configs_are_structurally_valid():
    failures = {}
    for path, config in _configs():
        if config.get("dataset_status", "active") != "active":
            continue
        errors = validate_config(config)
        if errors:
            failures[path.relative_to(ROOT).as_posix()] = errors
    assert failures == {}


def test_all_active_definition_files_exist():
    missing = []
    for path, config in _configs():
        if config.get("dataset_status", "active") != "active":
            continue
        for definition in config.get("definitions", {}).values():
            candidate = Path(str(definition.get("path", "")))
            resolved = candidate if candidate.is_absolute() else path.parent / candidate
            if not resolved.is_file():
                missing.append("{}:{}".format(path.relative_to(ROOT), candidate))
    assert missing == []


def test_every_enabled_reader_plugin_exists():
    missing = []
    for path, config in _configs():
        if config.get("dataset_status", "active") != "active":
            continue
        for mapping in config.get("file_mappings", []):
            if not mapping.get("enabled", True):
                continue
            plugin = mapping.get("reader_plugin")
            if plugin and importlib.util.find_spec("harmo.ingest.plugins.{}".format(plugin)) is None:
                missing.append("{}:{}".format(path.relative_to(ROOT), plugin))
    assert missing == []


def test_quarantined_configs_explain_why():
    for _, config in _configs():
        if config.get("dataset_status", "active") == "quarantined":
            assert config.get("readiness_notes")
