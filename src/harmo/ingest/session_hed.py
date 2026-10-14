"""Build and persist session_hed records from config + HED JSON.

This module provides a source-code implementation of the notebook workflow:

1. Read ``config.yaml`` and HED JSON dictionary.
2. Expand file paths with ``input_path_template`` / ``path_patterns``.
3. Resolve ``hed_tag`` and map it to HED ontology strings.
4. Persist the resulting session metadata to Iceberg table
   ``local.warehouse.session_hed`` (or user-specified target).
"""
from __future__ import annotations

import json
import os
import re
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

import pandas as pd
from pyspark.sql import DataFrame, SparkSession
from pyspark.sql import functions as F

from harmo.config.hed import load_hed_definition, resolve_hed_tag
from harmo.config.loader import load_config
from harmo.warehouse.connection import get_spark_session


_PLACEHOLDER_RE = re.compile(r"\{(\w+)\}")


def _compile_path_regex(template: str, path_patterns: Dict[str, Any]) -> re.Pattern:
    path_patterns = path_patterns or {}

    def _normalize_capture(pattern: str) -> str:
        s = str(pattern).strip()
        if s.startswith("(") and s.endswith(")"):
            s = s[1:-1]
        if s.startswith("^"):
            s = s[1:]
        if s.endswith("$"):
            s = s[:-1]
        return s

    escaped = re.escape(str(template))

    # Replace declared placeholders with named capture groups.
    for key, raw_pattern in path_patterns.items():
        token = re.escape("{" + str(key) + "}")
        escaped = escaped.replace(
            token,
            "(?P<{}>{})".format(str(key), _normalize_capture(str(raw_pattern))),
        )

    # Fallback for undeclared placeholders.
    escaped = re.sub(
        r"\\\{([A-Za-z_][A-Za-z0-9_]*)\\\}",
        lambda m: "(?P<{}>[^/]+)".format(m.group(1)),
        escaped,
    )

    escaped = escaped.replace(r"/", r"[\\/]")
    return re.compile("^{}$".format(escaped))


def _extract_numeric_hed_map(hed_ontology: str) -> Dict[str, str]:
    """Extract key/value numeric pairs from HED ontology.

    Numeric values nested under ``Condition-variable/`` are ignored.
    """
    if not isinstance(hed_ontology, str):
        return {}

    out: Dict[str, str] = {}
    chunks = re.split(r",\s*", hed_ontology)
    for chunk in chunks:
        if "Condition-variable/" in chunk:
            continue
        for m in re.finditer(r"([A-Za-z][A-Za-z0-9_\- ]*)/([+-]?\d+(?:\.\d+)?)\b", chunk):
            k = m.group(1).strip()
            v = m.group(2)
            if k and k not in out:
                out[k] = v
    return out


def _resolve_study_id(config: Dict[str, Any]) -> Optional[int]:
    definitions = config.get("definitions", {})
    for _key, defn in definitions.items():
        if defn.get("yaml_key") == "studies":
            path = defn.get("path")
            if path and os.path.exists(path):
                import yaml

                with open(path, "r", encoding="utf-8") as f:
                    ydata = yaml.safe_load(f) or {}
                studies = ydata.get("studies", [])
                if studies and isinstance(studies, list):
                    sid = studies[0].get("study_id")
                    if sid is not None:
                        return int(sid)
    return None


def _resolve_hed_lookup(
    config: Dict[str, Any],
    hed_json_path: Optional[str] = None,
    json_key: Optional[str] = None,
) -> Tuple[Dict[str, str], str]:
    definitions = config.get("definitions", {})
    hed_def = definitions.get("hed_json", {})

    raw_path = hed_json_path or hed_def.get("path")
    if not raw_path:
        raise KeyError("HED JSON path is not set. Use definitions.hed_json.path or --hed-json")

    key = json_key or hed_def.get("json_key") or "HED"
    lookup = load_hed_definition(str(raw_path), json_key=str(key))
    return lookup, str(raw_path)


def build_session_hed_dataframe(
    config_path: str,
    hed_json_path: Optional[str] = None,
    json_key: Optional[str] = None,
) -> pd.DataFrame:
    """Build ``session_hed`` rows from config and file paths."""
    config = load_config(config_path)
    hed_lookup, hed_source = _resolve_hed_lookup(
        config=config,
        hed_json_path=hed_json_path,
        json_key=json_key,
    )

    study_id = _resolve_study_id(config)
    aliases = config.get("input_dir_aliases", {})

    rows: List[Dict[str, Any]] = []

    for mapping in config.get("file_mappings", []):
        hed_tag_def = mapping.get("hed_tag")
        template = mapping.get("input_path_template")
        session_id_template = mapping.get("session_id_template")
        alias = mapping.get("base_input_dir_alias")

        if not hed_tag_def or not template or not session_id_template or not alias:
            continue
        if alias not in aliases:
            continue

        base_dir = Path(str(aliases[alias]))
        if not base_dir.exists():
            continue

        regex = _compile_path_regex(str(template), dict(mapping.get("path_patterns", {})))
        ext = Path(str(template)).suffix
        pattern = "*{}".format(ext) if ext else "*"

        for p in base_dir.rglob(pattern):
            if not p.is_file():
                continue

            rel = str(p.relative_to(base_dir)).replace(os.sep, "/")
            m = regex.search(rel)
            if m is None:
                continue

            context = dict(m.groupdict())
            try:
                session_id = str(session_id_template).format(**context)
                resolved_tag = resolve_hed_tag(hed_tag_def, context)
            except Exception:
                continue

            hed_ontology = hed_lookup.get(resolved_tag)
            if hed_ontology is None:
                continue

            subject_id = context.get("sub_id") or context.get("subject_id")
            if subject_id is None:
                continue

            rows.append(
                {
                    "study_id": study_id,
                    "subject_id": str(subject_id),
                    "session_id": str(session_id),
                    "hed_tag": str(resolved_tag),
                    "hed_ontology": str(hed_ontology),
                    "hed_map": _extract_numeric_hed_map(str(hed_ontology)),
                    "source_file": str(p),
                    "hed_source": hed_source,
                }
            )

    if not rows:
        raise ValueError("No session_hed rows generated from {}".format(config_path))

    df = pd.DataFrame(rows)
    df["hed_map_json"] = df["hed_map"].apply(lambda x: json.dumps(x, ensure_ascii=False, sort_keys=True))
    df = df.drop_duplicates(
        subset=["study_id", "subject_id", "session_id", "hed_tag", "hed_ontology", "hed_map_json"]
    ).reset_index(drop=True)

    return df[
        [
            "study_id",
            "subject_id",
            "session_id",
            "hed_tag",
            "hed_ontology",
            "hed_map",
            "hed_map_json",
            "source_file",
            "hed_source",
        ]
    ]


def _to_spark_session_hed(spark: SparkSession, df: pd.DataFrame) -> DataFrame:
    """Convert pandas DataFrame to Spark DataFrame with map<string,string>."""
    export = df[
        [
            "study_id",
            "subject_id",
            "session_id",
            "hed_tag",
            "hed_ontology",
            "hed_map_json",
            "source_file",
            "hed_source",
        ]
    ].copy()

    raw = spark.createDataFrame(export)
    return raw.select(
        F.col("study_id").cast("int").alias("study_id"),
        F.col("subject_id").cast("string").alias("subject_id"),
        F.col("session_id").cast("string").alias("session_id"),
        F.col("hed_tag").cast("string").alias("hed_tag"),
        F.col("hed_ontology").cast("string").alias("hed_ontology"),
        F.from_json(F.col("hed_map_json"), "map<string,string>").alias("hed_map"),
        F.col("source_file").cast("string").alias("source_file"),
        F.col("hed_source").cast("string").alias("hed_source"),
    )


def persist_session_hed(
    config_path: str,
    hed_json_path: Optional[str] = None,
    json_key: Optional[str] = None,
    target_table: str = "local.warehouse.session_hed",
    dry_run: bool = False,
    spark: Optional[SparkSession] = None,
) -> pd.DataFrame:
    """Build and optionally persist session_hed rows to Iceberg."""
    df = build_session_hed_dataframe(
        config_path=config_path,
        hed_json_path=hed_json_path,
        json_key=json_key,
    )

    if dry_run:
        return df

    created = spark is None
    if created:
        spark = get_spark_session(app_name="harmo-session-hed")

    try:
        spark_df = _to_spark_session_hed(spark, df)
        spark.sql("CREATE NAMESPACE IF NOT EXISTS local.warehouse")
        spark_df.writeTo(target_table).using("iceberg").append()
    finally:
        if created and spark is not None:
            spark.stop()

    return df
