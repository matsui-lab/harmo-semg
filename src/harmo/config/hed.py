"""HED configuration helpers.

This module provides:

* Loading HED tag dictionaries from JSON definitions.
* Resolving hed_tag templates declared in file_mappings.
* Validating HED strings against HED Schema v8.4.0.
* Enriching session_info rows with hed_ontology / hed_map fields.
"""
from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

import pandas as pd


_PLACEHOLDER_RE = re.compile(r"\{(\w+)\}")


def _find_first_dict_with_key(payload: Any, key: str) -> Optional[Dict[str, Any]]:
    if isinstance(payload, dict):
        if key in payload and isinstance(payload[key], dict):
            return payload[key]
        for value in payload.values():
            found = _find_first_dict_with_key(value, key)
            if found is not None:
                return found
    elif isinstance(payload, list):
        for item in payload:
            found = _find_first_dict_with_key(item, key)
            if found is not None:
                return found
    return None


def load_hed_definition(json_path: str, json_key: str = "HED") -> Dict[str, str]:
    """Load a `{tag: hed_ontology}` map from a JSON definition file."""
    with open(json_path, "r", encoding="utf-8") as f:
        payload = json.load(f)

    hed_map = _find_first_dict_with_key(payload, json_key)
    if hed_map is None:
        raise ValueError("json_key '{}' not found as a dictionary in {}".format(json_key, json_path))

    normalized = {}
    for key, value in hed_map.items():
        normalized[str(key)] = str(value)
    return normalized


def _build_session_template_regex(session_id_template: str, path_patterns: Dict[str, str]) -> re.Pattern:
    parts = []
    cursor = 0
    for match in _PLACEHOLDER_RE.finditer(session_id_template):
        start, end = match.span()
        key = match.group(1)
        parts.append(re.escape(session_id_template[cursor:start]))
        pattern = str(path_patterns.get(key, r"[^/]+"))
        pattern = pattern.strip()
        if pattern.startswith("^"):
            pattern = pattern[1:]
        if pattern.endswith("$"):
            pattern = pattern[:-1]
        parts.append("(?P<{}>{})".format(key, pattern))
        cursor = end
    parts.append(re.escape(session_id_template[cursor:]))
    return re.compile("^{}$".format("".join(parts)))


def _stringify_context_value(value: Any) -> str:
    if value is None:
        return ""
    if isinstance(value, float) and pd.isna(value):
        return ""
    return str(value)


def resolve_hed_tag(hed_tag_def: Any, context: Dict[str, Any]) -> str:
    """Resolve hed_tag declarations (string/list) with row/match context."""
    if isinstance(hed_tag_def, list):
        parts = []
        for token in hed_tag_def:
            token_str = str(token)
            if _PLACEHOLDER_RE.search(token_str):
                parts.append(token_str.format(**context))
            elif token_str in context:
                parts.append(_stringify_context_value(context[token_str]))
            else:
                parts.append(token_str)
        return "_".join([p for p in parts if p != ""])

    text = str(hed_tag_def)
    if _PLACEHOLDER_RE.search(text):
        return text.format(**context)
    return text


def _build_session_hed_rules(file_mappings: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    rules = []
    for mapping in file_mappings:
        if not isinstance(mapping, dict):
            continue
        if "hed_tag" not in mapping:
            continue
        session_id_template = mapping.get("session_id_template")
        if not session_id_template:
            continue
        regex = _build_session_template_regex(
            session_id_template=str(session_id_template),
            path_patterns=dict(mapping.get("path_patterns", {})),
        )
        rules.append(
            {
                "name": str(mapping.get("name", "?")),
                "regex": regex,
                "hed_tag": mapping.get("hed_tag"),
            }
        )
    return rules


def enrich_sessions_with_hed(
    session_df: pd.DataFrame,
    file_mappings: List[Dict[str, Any]],
    hed_dictionary: Dict[str, str],
) -> Tuple[pd.DataFrame, List[str]]:
    """Populate hed_ontology/hed_map columns for session_info rows."""
    if session_df is None or session_df.empty:
        return session_df, []

    rules = _build_session_hed_rules(file_mappings)
    if not rules:
        out = session_df.copy()
        if "hed_ontology" not in out.columns:
            out["hed_ontology"] = None
        if "hed_map" not in out.columns:
            out["hed_map"] = None
        return out, []

    out = session_df.copy()
    if "hed_ontology" not in out.columns:
        out["hed_ontology"] = None
    if "hed_map" not in out.columns:
        out["hed_map"] = None

    errors: List[str] = []

    for idx, row in out.iterrows():
        session_id = _stringify_context_value(row.get("session_id"))
        if session_id == "":
            continue

        matched_rule = None
        capture_context = {}
        for rule in rules:
            m = rule["regex"].match(session_id)
            if m:
                matched_rule = rule
                capture_context = dict(m.groupdict())
                break

        if matched_rule is None:
            continue

        row_context = {
            key: _stringify_context_value(value)
            for key, value in row.items()
        }
        row_context.update(capture_context)

        try:
            resolved_tag = resolve_hed_tag(matched_rule["hed_tag"], row_context)
        except Exception as exc:
            errors.append(
                "session_id '{}': failed to resolve hed_tag in rule '{}': {}".format(
                    session_id,
                    matched_rule["name"],
                    exc,
                )
            )
            continue

        hed_ontology = hed_dictionary.get(resolved_tag)
        if hed_ontology is None:
            errors.append(
                "session_id '{}': resolved hed_tag '{}' not found in HED definition".format(
                    session_id,
                    resolved_tag,
                )
            )
            continue

        out.at[idx, "hed_ontology"] = hed_ontology
        out.at[idx, "hed_map"] = json.dumps({"tag": resolved_tag}, ensure_ascii=False)

    return out, errors


def validate_hed_schema(hed_dictionary: Dict[str, str], version: str = "8.4.0") -> List[str]:
    """Validate HED ontology strings with HED schema, returning error messages."""
    errors: List[str] = []
    try:
        from hed import HedString, load_schema_version
    except Exception:
        return [
            "HED validation requires the 'hedtools' package. Install with: pip install hedtools"
        ]

    try:
        schema = load_schema_version(version)
    except Exception as exc:
        return ["Failed to load HED Schema v{}: {}".format(version, exc)]

    for tag, hed_str in hed_dictionary.items():
        try:
            issues = HedString(str(hed_str), schema).validate()
        except Exception as exc:
            errors.append("hed_tag '{}': parser error: {}".format(tag, exc))
            continue

        if issues:
            errors.append("hed_tag '{}': {} issue(s) in HED string".format(tag, len(issues)))

    return errors


def load_primary_hed_definition(config: Dict[str, Any]) -> Tuple[Optional[Dict[str, str]], Optional[str], List[str]]:
    """Load the first HED JSON definition in `definitions`.

    Returns `(hed_dict, definition_key, errors)`.
    """
    definitions = config.get("definitions", {})
    for def_key, defn in definitions.items():
        if str(defn.get("source_type", "")).lower() != "json":
            continue
        json_key = defn.get("json_key")
        if not json_key:
            continue
        path = defn.get("path")
        if not path:
            return None, None, ["definition '{}': missing path".format(def_key)]
        path_obj = Path(str(path))
        if not path_obj.exists():
            return None, None, ["definition '{}': file not found: {}".format(def_key, path)]
        try:
            hed_dict = load_hed_definition(str(path_obj), json_key=str(json_key))
            return hed_dict, str(def_key), []
        except Exception as exc:
            return None, str(def_key), ["definition '{}': {}".format(def_key, exc)]

    return None, None, []
