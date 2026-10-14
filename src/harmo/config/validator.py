"""Cross-table consistency checks for study configurations.

The :func:`validate_study` function checks that:

* All ``base_input_dir_alias`` values in file_mappings exist in ``input_dir_aliases``.
* All definition table file paths exist on disk.
* CSV/TSV definition files contain the required columns for their target table.
* IDs do not contain underscores (project convention: use hyphens).
* Referenced ``yaml_key`` entries exist in their YAML files.
"""

from __future__ import annotations

import os
import re
from pathlib import Path
import yaml


# -- Required columns per warehouse table (based on downstream analysis usage) --
# Note: study_id is excluded because pipeline.py auto-supplements it from
# the study definition YAML when missing in CSV/TSV files.
_TABLE_REQUIRED_COLUMNS = {
    "subject_table": ["subject_id", "height_m", "weight_kg", "age_year"],
    "session_info": ["subject_id", "session_id", "task_id"],
    "dim_marker": ["marker", "body", "location"],
    "dim_channels": ["column_name", "sensor_type"],
    "dim_device": ["type", "samplingrate_Hz"],
    "dim_coordinate": ["sensor_type", "x_positive", "y_positive", "z_positive"],
}  # type: Dict[str, List[str]]

# -- Common aliases / misspellings for required columns --
_COLUMN_ALIASES = {
    "study_id": ["studyid", "study"],
    "subject_id": ["subjectid", "sub_id", "participant_id"],
    "session_id": ["sessionid", "ses_id"],
    "task_id": ["taskid", "task"],
    "height_m": ["height", "height_cm"],
    "weight_kg": ["weight", "mass", "body_weight"],
    "age_year": ["age", "age_years"],
    "marker": ["marker_name", "markername"],
    "column_name": ["channel_name", "col_name"],
    "sensor_type": ["sensortype"],
    "samplingrate_Hz": ["sampling_rate", "samplingrate", "fs"],
    "x_positive": ["x_direction", "x_dir"],
    "y_positive": ["y_direction", "y_dir"],
    "z_positive": ["z_direction", "z_dir"],
}  # type: Dict[str, List[str]]


def validate_study(config):
    # type: (Dict[str, Any]) -> List[str]
    """Run cross-table consistency checks on a loaded config.

    Returns a list of human-readable error/warning strings.
    Empty list means all checks passed.
    """
    errors = []  # type: List[str]
    _check_aliases(config, errors)
    _check_definition_paths(config, errors)
    _check_definition_columns(config, errors)
    _check_id_conventions(config, errors)
    _check_yaml_keys(config, errors)
    _check_file_patterns(config, errors)
    return errors


def _check_aliases(config, errors):
    # type: (Dict[str, Any], List[str]) -> None
    aliases = set(config.get("input_dir_aliases", {}).keys())
    for mapping in config.get("file_mappings", []):
        if mapping.get("enabled", True) is False:
            continue
        alias = mapping.get("base_input_dir_alias", "")
        if alias and alias not in aliases:
            errors.append(
                "file_mapping '{}': base_input_dir_alias '{}' is not defined in input_dir_aliases".format(
                    mapping.get("name", "?"), alias
                )
            )


def _check_definition_paths(config, errors):
    # type: (Dict[str, Any], List[str]) -> None
    for key, defn in config.get("definitions", {}).items():
        path = defn.get("path", "")
        if path and not os.path.exists(path):
            errors.append("definition '{}': file not found: {}".format(key, path))


def _check_definition_columns(config, errors):
    # type: (Dict[str, Any], List[str]) -> None
    """Check that CSV/TSV definition files contain the required columns."""
    for key, defn in config.get("definitions", {}).items():
        source_type = defn.get("source_type", "yaml")
        if source_type not in ("csv", "tsv"):
            continue

        path = defn.get("path", "")
        if not path or not os.path.exists(path):
            continue

        # Resolve table short name from e.g. 'local.warehouse.subject_table'
        table_name = defn.get("table_name", "")
        short_name = table_name.rsplit(".", 1)[-1] if table_name else ""
        required_cols = _TABLE_REQUIRED_COLUMNS.get(short_name)
        if required_cols is None:
            continue

        # Read CSV/TSV header row
        import csv as csv_mod

        sep = "\t" if source_type == "tsv" else ","
        reader_opts = defn.get("reader_options", {})
        if "sep" in reader_opts:
            sep = reader_opts["sep"]

        try:
            with open(path, "r", encoding="utf-8-sig") as f:
                reader = csv_mod.reader(f, delimiter=sep)
                header = next(reader, None)
        except Exception:
            continue

        if header is None:
            errors.append("definition '{}': file is empty: {}".format(key, path))
            continue

        actual_cols = [c.strip() for c in header]
        actual_lower_map = {c.lower(): c for c in actual_cols}

        # Build reverse alias lookup: alias_lower -> expected column name
        alias_to_expected = {}  # type: Dict[str, str]
        for expected, aliases in _COLUMN_ALIASES.items():
            for alias in aliases:
                alias_to_expected[alias.lower()] = expected

        for req_col in required_cols:
            if req_col in actual_cols:
                continue

            # Case-insensitive match
            if req_col.lower() in actual_lower_map:
                found = actual_lower_map[req_col.lower()]
                errors.append(
                    "definition '{}': column '{}' found but expected '{}' "
                    "(case mismatch — rename to '{}')".format(
                        key, found, req_col, req_col
                    )
                )
                continue

            # Known alias match
            matched_alias = None
            for ac in actual_cols:
                if (
                    ac.lower() in alias_to_expected
                    and alias_to_expected[ac.lower()] == req_col
                ):
                    matched_alias = ac
                    break

            if matched_alias is not None:
                errors.append(
                    "definition '{}': column '{}' found but expected '{}' "
                    "(did you mean '{}' ?)".format(key, matched_alias, req_col, req_col)
                )
                continue

            # Missing entirely
            errors.append(
                "definition '{}': required column '{}' not found in {}".format(
                    key, req_col, os.path.basename(path)
                )
            )


_UNDERSCORE_RE = re.compile(r"_")
_TARGET_ID_KEYS = {"study_id", "subject_id", "session_id"}


def _check_id_conventions(config, errors):
    # type: (Dict[str, Any], List[str]) -> None
    """Warn if any IDs use underscores (project convention: hyphens only)."""
    for key, defn in config.get("definitions", {}).items():
        path = defn.get("path", "")
        if not path or not os.path.exists(path):
            continue

        source_type = defn.get("source_type", "yaml")
        try:
            if source_type == "yaml":
                with open(path, "r", encoding="utf-8") as f:
                    data = yaml.safe_load(f)
                    _check_yaml_ids(data, key, errors)
            elif source_type in ("csv", "tsv"):
                import csv as csv_mod

                sep = "\t" if source_type == "tsv" else ","
                with open(path, "r", encoding="utf-8-sig") as f:
                    reader = csv_mod.DictReader(f, delimiter=sep)
                    for row_num, row in enumerate(reader, start=2):
                        for col_name, val in row.items():
                            if col_name in _TARGET_ID_KEYS and val and "_" in val:
                                errors.append(
                                    "definition '{}' row {}: {} '{}' contains underscore "
                                    "(use hyphens instead)".format(
                                        key, row_num, col_name, val
                                    )
                                )
        except Exception:
            pass  # file read errors are already caught by _check_definition_paths


def _check_yaml_ids(data, context, errors):
    # type: (Any, str, List[str]) -> None
    if isinstance(data, dict):
        for k, v in data.items():
            if (
                isinstance(k, str)
                and k in _TARGET_ID_KEYS
                and isinstance(v, str)
                and "_" in v
            ):
                errors.append(
                    "definition '{}': {} '{}' contains underscore (use hyphens instead)".format(
                        context, k, v
                    )
                )
            _check_yaml_ids(v, context, errors)
    elif isinstance(data, list):
        for item in data:
            _check_yaml_ids(item, context, errors)


def _check_yaml_keys(config, errors):
    # type: (Dict[str, Any], List[str]) -> None
    for key, defn in config.get("definitions", {}).items():
        if defn.get("source_type", "yaml") != "yaml":
            continue
        yaml_key = defn.get("yaml_key")
        path = defn.get("path", "")
        if not yaml_key or not path or not os.path.exists(path):
            continue
        try:
            with open(path, "r", encoding="utf-8") as f:
                data = yaml.safe_load(f)
            if yaml_key not in (data or {}):
                errors.append(
                    "definition '{}': yaml_key '{}' not found in {}".format(
                        key, yaml_key, path
                    )
                )
        except Exception:
            pass


def _check_file_patterns(config, errors):
    # type: (Dict[str, Any], List[str]) -> None
    """Check that file pattern templates match at least one file."""
    aliases = config.get("input_dir_aliases", {})
    for mapping in config.get("file_mappings", []):
        if mapping.get("enabled", True) is False:
            continue
        if mapping.get("allow_empty", False):
            continue
        alias = mapping.get("base_input_dir_alias", "")
        base_dir = aliases.get(alias)
        if not base_dir or not os.path.isdir(base_dir):
            continue

        template = mapping.get("input_path_template", "")
        patterns = mapping.get("path_patterns", {})
        regex_str = template
        for ph, pattern in patterns.items():
            regex_str = regex_str.replace("{%s}" % ph, pattern)

        regex_str = regex_str.replace("/", r"[\\/]")
        try:
            compiled = re.compile(regex_str)
        except re.error:
            errors.append(
                "file_mapping '{}': invalid regex pattern: {}".format(
                    mapping.get("name", "?"), regex_str
                )
            )
            continue

        ext = os.path.splitext(template)[-1]
        if not ext:
            ext = ".txt"

        base = Path(base_dir)
        found = False
        for p in base.rglob("*{}".format(ext)):
            rel = str(p.relative_to(base)).replace(os.sep, "/")
            if compiled.search(rel):
                found = True
                break

        if not found:
            errors.append(
                "file_mapping '{}': no files matched pattern in {}".format(
                    mapping.get("name", "?"), base_dir
                )
            )
