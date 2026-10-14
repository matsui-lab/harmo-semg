"""JSON-Schema-based validation for study config.yaml files."""

from __future__ import annotations

from copy import deepcopy
from typing import Any, Dict, List

# We define the schema as a plain dict so that users without jsonschema
# installed can still *load* configs; validation is opt-in.

CONFIG_SCHEMA = {
    "type": "object",
    "required": ["definitions", "file_mappings"],
    "description": "harmo study config. MAT files that require non-standard extraction must declare reader_plugin explicitly for reproducibility.",
    "properties": {
        "dataset_status": {
            "type": "string",
            "enum": ["active", "draft", "quarantined"],
        },
        "readiness_notes": {"type": "string"},
        "warehouse_path": {"type": "string"},
        "input_dir_aliases": {
            "type": "object",
            "additionalProperties": {"type": "string"},
        },
        "definitions": {
            "type": "object",
            "additionalProperties": {
                "type": "object",
                "required": ["path", "source_type"],
                "properties": {
                    "source_type": {
                        "type": "string",
                        "enum": ["yaml", "csv", "tsv", "json"],
                    },
                    "path": {"type": "string"},
                    "table_name": {"type": "string"},
                    "yaml_key": {"type": "string"},
                    "json_key": {"type": "string"},
                    "normalize_func": {"type": "string"},
                    "reader_options": {"type": "object"},
                },
                "allOf": [
                    {
                        "if": {
                            "properties": {
                                "source_type": {"enum": ["yaml", "csv", "tsv"]}
                            },
                            "required": ["source_type"],
                        },
                        "then": {"required": ["table_name"]},
                    }
                ],
            },
        },
        "file_mappings": {
            "type": "array",
            "items": {
                "type": "object",
                "required": [
                    "name",
                    "base_input_dir_alias",
                    "iceberg_table",
                    "input_path_template",
                    "path_patterns",
                ],
                "properties": {
                    "name": {"type": "string"},
                    "enabled": {"type": "boolean"},
                    "allow_empty": {
                        "type": "boolean",
                        "description": "Explicitly allow an enabled mapping to match zero files. Defaults to false so production ingestion fails closed.",
                    },
                    "readiness_notes": {"type": "string"},
                    "base_input_dir_alias": {"type": "string"},
                    "iceberg_table": {"type": "string"},
                    "input_path_template": {"type": "string"},
                    "path_patterns": {"type": "object"},
                    "file_format": {
                        "type": "string",
                        "description": "Input file format. Standard MAT numeric reading can omit reader_plugin; opaque/MCOS/special MAT cases should declare reader_plugin explicitly.",
                    },
                    "reader_plugin": {
                        "type": "string",
                        "pattern": "^[A-Za-z0-9_]+$",
                        "description": "Explicit plugin module name under harmo.ingest.plugins. Required for non-standard MAT structures to keep extraction deterministic and auditable.",
                    },
                    "plugin_options": {
                        "type": "object",
                        "description": "Plugin-specific options. Must not be set unless reader_plugin is also set.",
                    },
                    "plugin_fallback": {
                        "type": "string",
                        "description": "Optional named fallback mode for a declared plugin. Must not be set unless reader_plugin is also set.",
                    },
                    "static_metadata": {"type": "object"},
                    "melt_options": {"type": "object"},
                    "mat_preconvert": {
                        "type": "boolean",
                        "description": "Enable Octave preconversion helper for MAT files. This is a file conversion aid, not a substitute for declaring reader_plugin when custom extraction logic is required.",
                    },
                    "reader_options": {"type": "object"},
                    "mat_data_key_template": {"type": "string"},
                    "mat_key_patterns": {"type": "object"},
                },
                "allOf": [
                    {
                        "if": {
                            "anyOf": [
                                {"required": ["plugin_options"]},
                                {"required": ["plugin_fallback"]},
                            ]
                        },
                        "then": {"required": ["reader_plugin"]},
                    }
                ],
            },
        },
    },
}


def _extract_reader_options(mapping: Dict[str, Any]) -> Dict[str, Any]:
    reader_options = mapping.get("reader_options", {})
    return reader_options if isinstance(reader_options, dict) else {}


def _validate_plugin_rules(config: Dict[str, Any]) -> List[str]:
    errors: List[str] = []

    for idx, mapping in enumerate(config.get("file_mappings", [])):
        if not isinstance(mapping, dict):
            continue
        if mapping.get("enabled", True) is False:
            continue

        path = "file_mappings -> {}".format(idx)
        fmt = str(mapping.get("file_format", "")).lower()
        reader_plugin = mapping.get("reader_plugin")
        plugin_options = mapping.get("plugin_options")
        plugin_fallback = mapping.get("plugin_fallback")
        reader_options = _extract_reader_options(mapping)
        data_key = reader_options.get("data_key")
        mat_template = mapping.get("mat_data_key_template")

        if plugin_options is not None and reader_plugin is None:
            errors.append("{}: plugin_options requires reader_plugin".format(path))

        if plugin_fallback is not None and reader_plugin is None:
            errors.append("{}: plugin_fallback requires reader_plugin".format(path))

        if fmt == "mat":
            explicit_standard_route = bool(mat_template) or (
                data_key is not None and str(data_key).lower() != "none"
            )
            if not explicit_standard_route and reader_plugin is None:
                errors.append(
                    "{}: MAT mapping must declare either reader_plugin or an explicit standard route via reader_options.data_key / mat_data_key_template".format(
                        path
                    )
                )

    return errors


def validate_config(config):
    # type: (Dict[str, Any]) -> List[str]
    """Validate *config* dict against :data:`CONFIG_SCHEMA`.

    Returns a list of error messages (empty if valid).
    Requires the ``jsonschema`` package; returns a single informational
    message if it is not installed.
    """
    try:
        import jsonschema
    except ImportError:
        return [
            "jsonschema is not installed; skipping schema validation."
        ] + _validate_plugin_rules(config)

    validator = jsonschema.Draft7Validator(deepcopy(CONFIG_SCHEMA))
    errors = sorted(validator.iter_errors(config), key=lambda e: list(e.absolute_path))
    schema_errors = [
        "{path}: {msg}".format(
            path=" -> ".join(str(p) for p in e.absolute_path) or "(root)",
            msg=e.message,
        )
        for e in errors
    ]
    return schema_errors + _validate_plugin_rules(config)
