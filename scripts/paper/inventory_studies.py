#!/usr/bin/env python3
"""Inventory configured studies without opening or redistributing source data."""
from __future__ import annotations

import argparse
import json
from collections import Counter
from pathlib import Path

import yaml


def inventory(root: Path):
    configs = sorted((root / "studies").glob("*/config.yaml"))
    records = []
    formats = Counter()
    plugins = Counter()
    mapping_count = 0
    active_mapping_count = 0
    for config_path in configs:
        data = yaml.safe_load(config_path.read_text(encoding="utf-8")) or {}
        mappings = data.get("file_mappings", []) or []
        enabled_mappings = [item for item in mappings if item.get("enabled", True)]
        definitions = data.get("definitions", {}) or {}
        status = data.get("dataset_status", "active")
        aliases = data.get("input_dir_aliases", {}) or {}
        mapping_count += len(mappings)
        if status == "active":
            active_mapping_count += len(enabled_mappings)
        missing_aliases = []
        missing_definitions = []
        for definition in definitions.values():
            raw_path = Path(str(definition.get("path", "")))
            resolved = raw_path if raw_path.is_absolute() else config_path.parent / raw_path
            if not resolved.is_file():
                missing_definitions.append(str(definition.get("path", "")))
        for mapping in mappings:
            formats[str(mapping.get("file_format", "unspecified"))] += 1
            plugin = mapping.get("reader_plugin")
            if plugin:
                plugins[str(plugin)] += 1
            alias = mapping.get("base_input_dir_alias")
            if alias and alias not in aliases:
                missing_aliases.append(str(alias))
        records.append({
            "config": config_path.relative_to(root).as_posix(),
            "directory": config_path.parent.name,
            "status": status,
            "readiness_notes": data.get("readiness_notes"),
            "mapping_count": len(mappings),
            "enabled_mapping_count": len(enabled_mappings),
            "quarantined_mappings": [
                item.get("name", "?") for item in mappings if not item.get("enabled", True)
            ],
            "definition_count": len(definitions),
            "missing_definition_files": sorted(set(missing_definitions)),
            "missing_input_aliases": sorted(set(missing_aliases)),
        })
    duplicate_numeric_ids = _duplicate_numeric_ids([item["directory"] for item in records])
    return {
        "inventory_version": "1.0.0",
        "configured_study_directories": len(records),
        "file_mappings": mapping_count,
        "active_study_directories": sum(item["status"] == "active" for item in records),
        "active_file_mappings": active_mapping_count,
        "formats": dict(sorted(formats.items())),
        "reader_plugins": dict(sorted(plugins.items())),
        "duplicate_numeric_study_ids": duplicate_numeric_ids,
        "studies": records,
    }


def _duplicate_numeric_ids(names):
    normalized = [name.replace("study_", "").replace("study", "") for name in names]
    counts = Counter(normalized)
    return sorted(key for key, count in counts.items() if count > 1)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--root", default=str(Path(__file__).resolve().parents[2]))
    parser.add_argument("--output", required=True)
    args = parser.parse_args()
    result = inventory(Path(args.root).resolve())
    target = Path(args.output)
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(json.dumps(result, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    if any(
        item["missing_input_aliases"]
        for item in result["studies"]
        if item["status"] == "active"
    ) or any(
        item["missing_definition_files"]
        for item in result["studies"]
        if item["status"] == "active"
    ):
        raise SystemExit(1)


if __name__ == "__main__":
    main()
