"""Data transformation and normalization utilities for the ingest layer.

This module consolidates:

* Pandas-level normalization helpers (``normalize_markers``,
  ``normalize_subjects``) originally from ``data_converter.py``.
* Spark-level column mapping / signal extraction / melt helpers originally
  from ``ingest_data_spark.py``.
* OpenSim coordinate-transformation analysis originally from ``code/com.py``.
"""
from __future__ import annotations

import os
import re
from itertools import chain
from typing import Any, Dict, List, Optional, TYPE_CHECKING

import numpy as np
import pandas as pd

from pyspark.sql import DataFrame, SparkSession
from pyspark.sql.functions import (
    col,
    concat_ws,
    count,
    create_map,
    expr,
    format_string,
    lag,
    lit,
    max as spark_max,
    min as spark_min,
    posexplode,
    row_number,
    split,
    trim,
    when,
)
from pyspark.sql.types import DoubleType
from pyspark.sql.window import Window

# Optional dependency -- only needed for ``analyze``
try:
    import opensim as osim  # type: ignore[import-untyped]
except ImportError:
    osim = None  # type: ignore[assignment]


# ---------------------------------------------------------------------------
# Pandas-level normalization functions (from data_converter.py)
# ---------------------------------------------------------------------------

def normalize_markers(study_id: int, marker_config: dict) -> pd.DataFrame:
    """Flatten a marker-config dict into a flat DataFrame with *study_id*."""
    marker_list = []  # type: List[Dict[str, Any]]
    for segment_key, markers in marker_config.items():
        for marker in markers:
            marker_data = marker.copy()
            marker_data["study_id"] = study_id
            if "body" not in marker_data:
                marker_data["body"] = segment_key
            marker_list.append(marker_data)
    return pd.DataFrame(marker_list)


def normalize_subjects(subjects_list: list) -> pd.DataFrame:
    """Build a subjects DataFrame, computing BMI when possible."""
    df = pd.DataFrame(subjects_list)
    if "height_m" in df.columns and "weight_kg" in df.columns:
        df["bmi"] = df["weight_kg"] / (df["height_m"] ** 2)
    return df


NORMALIZATION_FUNCS = {
    "normalize_markers": normalize_markers,
    "normalize_subjects": normalize_subjects,
}  # type: Dict[str, Any]


# ---------------------------------------------------------------------------
# Spark-level column normalization (from ingest_data_spark.py)
# ---------------------------------------------------------------------------

def apply_column_normalization(df: DataFrame, rule_config: dict) -> DataFrame:
    """Apply dictionary-based value mapping or column concatenation.

    Uses the ``column_transform`` section of *rule_config*.
    Supported keys:
    * ``target_col``: Column to modify (e.g., "channel").
    * ``prepend_col``: Column whose value to prepend (e.g., "side").
    * ``separator``: Separator for prepend (default: "_").
    * ``mapping_rule``: Dictionary for alias-to-unified mapping.
    """
    transform_rule = rule_config.get("column_transform", {})
    target_col = transform_rule.get("target_col")
    mapping_rule = transform_rule.get("mapping_rule")
    prepend_col = transform_rule.get("prepend_col")
    separator = transform_rule.get("separator", "_")

    if target_col and target_col in df.columns:
        # 1. Prepend column if specified
        if prepend_col and prepend_col in df.columns:
            print("  [正規化] {} の先頭に {} を結合します (sep='{}')".format(target_col, prepend_col, separator))
            df = df.withColumn(target_col, concat_ws(separator, trim(col(prepend_col)), col(target_col))
            )

        # 2. Value mapping
        if mapping_rule:
            print("  [正規化] {} 列の表記揺れを補正します。".format(target_col))
            # Invert the mapping: {alias -> unified_name}
            reverse_mapping = {}  # type: Dict[str, str]
            for unified_name, aliases in mapping_rule.items():
                for alias in aliases:
                    reverse_mapping[alias] = unified_name

            # Build a Spark map expression
            mapping_expr = create_map([lit(x) for x in chain(*reverse_mapping.items())])

            df = df.withColumn(
                target_col,
                when(
                    mapping_expr.getItem(col(target_col)).isNotNull(),
                    mapping_expr.getItem(col(target_col)),
                ).otherwise(col(target_col)),
            )
    return df


# ---------------------------------------------------------------------------
# Spark-level signal extraction (from ingest_data_spark.py)
# ---------------------------------------------------------------------------

def apply_signal_extraction(df: DataFrame, rule_config: dict) -> DataFrame:
    """Extract events from continuous signals via edge detection and debouncing.

    Reads ``extraction_rules`` or the legacy ``detect_edge`` key from
    *rule_config*.
    """
    # 1. Legacy mode: convert simple detect_edge key to extraction_rules format
    rules = rule_config.get("extraction_rules", [])
    if not rules and "detect_edge" in rule_config:
        rules = [
            {
                "target_channel": rule_config.get("target_channel_for_edge"),
                "detect_edge": rule_config["detect_edge"],
                "min_interval": rule_config.get("min_interval", 0.0),
                "event_name": rule_config.get("static_metadata", {}).get(
                    "event_name", "Detected_Event"
                ),
                "value": rule_config.get("static_metadata", {}).get("value", 1.0),
                "edge_threshold": rule_config.get("edge_threshold", 0.5),
            }
        ]

    if not rules:
        return df
    
    extracted_dfs = []  # type: List[DataFrame]

    window_spec = Window.partitionBy("session_id", "channel").orderBy("time")

    for r in rules:
        target_ch = r.get("target_channel")
        mode = r.get("detect_edge", "rising")
        threshold = r.get("edge_threshold", 0.5)
        min_int = r.get("min_interval", 0.0)
        evt_name = r.get("event_name", "Event")
        val_to_set = r.get("value", 1.0)

        target_df = df
        if target_ch:
            target_df = df.filter(col("channel") == target_ch)

        target_df = target_df.withColumn("prev_val", lag("value").over(window_spec))

        if mode == "rising":
            condition = (col("prev_val") < threshold) & (col("value") >= threshold)
        elif mode == "falling":
            condition = (col("prev_val") >= threshold) & (col("value") < threshold)
        else:  # both
            condition = (
                ((col("prev_val") < threshold) & (col("value") >= threshold))
                | ((col("prev_val") >= threshold) & (col("value") < threshold))
            )

        events_df = target_df.filter(condition)

        # Debouncing
        if min_int > 0:
            w_debounce = Window.partitionBy("session_id", "channel").orderBy("time")
            events_df = events_df.withColumn(
                "prev_event_time", lag("time").over(w_debounce)
            )
            events_df = events_df.filter(
                (col("prev_event_time").isNull())
                | ((col("time") - col("prev_event_time")) >= min_int)
            )

        events_df = events_df.withColumn("event_name", lit(evt_name))
        events_df = events_df.withColumn(
            "value", lit(val_to_set).cast(DoubleType())
        )

        extracted_dfs.append(events_df)

    if extracted_dfs:
        final_extracted = extracted_dfs[0]
        for d in extracted_dfs[1:]:
            final_extracted = final_extracted.union(d)
        return final_extracted
    return df


# ---------------------------------------------------------------------------
# Spark-level horizontal explode (from ingest_data_spark.py)
# ---------------------------------------------------------------------------

def apply_horizontal_explode(
    spark: SparkSession, file_path_list: list, reader_options: dict
) -> DataFrame:
    """Read horizontally-oriented data and explode it into a single ``time`` column."""
    df = spark.read.text(file_path_list)

    sep = reader_options.get("sep", ",")
    df = df.select(
        posexplode(split(col("value"), sep)).alias("idx", "raw_value")
    )
    df = (
        df.filter(col("raw_value") != "")
        .withColumn("time", col("raw_value").cast(DoubleType()))
    )
    return df.select("time")


# ---------------------------------------------------------------------------
# Melt / reshape helpers (extracted from process_fact_batch)
# ---------------------------------------------------------------------------

def melt_channels_to_long(
    df: DataFrame,
    id_vars: List[str],
    exclude_cols: Optional[List[str]] = None,
) -> Optional[DataFrame]:
    """Melt wide-format sensor columns into long ``(channel, value)`` rows.

    Parameters
    ----------
    df:
        Wide-format Spark DataFrame.
    id_vars:
        Columns to keep as identifiers (time, metadata, etc.).
    exclude_cols:
        Additional columns to exclude from the melt (e.g. path columns).

    Returns
    -------
    DataFrame or None if no value columns are found.
    """
    if exclude_cols is None:
        exclude_cols = []

    value_vars = [c for c in df.columns if c not in id_vars + exclude_cols]

    if not value_vars:
        print("  [警告] melt対象のチャンネル列が見つかりません。")
        return None

    # Cast inline in a single projection to keep the physical plan compact.
    stack_expr = "stack({}, {}) AS (channel, value)".format(
        len(value_vars),
        ", ".join(
            ["'{}', CAST(`{}` AS DOUBLE)".format(v, v) for v in value_vars]
        ),
    )

    return df.select(*id_vars, expr(stack_expr))


def add_percentage_column(
    df: DataFrame,
    group_by_cols: List[str],
    time_col: str = "time",
) -> DataFrame:
    """Add a ``percentage`` column (0-100) based on the time range per group."""
    window_spec_index = Window.partitionBy(*group_by_cols).orderBy(lit(1))

    df_stats = df.groupBy(*group_by_cols).agg(
        spark_min(time_col).alias("min_time"),
        spark_max(time_col).alias("max_time"),
        count(lit(1)).alias("num_rows"),
    )

    df = df.join(df_stats, group_by_cols)

    df = df.withColumn(
        "percentage",
        when(
            (col("min_time").isNotNull())
            & (col("max_time").isNotNull())
            & ((col("max_time") - col("min_time")) > 0),
            (col(time_col) - col("min_time"))
            / (col("max_time") - col("min_time"))
            * 100,
        ).otherwise(
            when(
                col("num_rows") > 1,
                (row_number().over(window_spec_index) - 1)
                / (col("num_rows") - 1)
                * 100,
            ).otherwise(100.0)
        ),
    ).drop("min_time", "max_time", "num_rows")

    return df


def build_session_id(df: DataFrame, session_id_template: str) -> DataFrame:
    """Build a ``session_id`` column from a template with ``{placeholder}`` tokens."""
    session_id_format = session_id_template
    format_args = []  # type: List[Any]
    placeholders = re.findall(r"\{(\w+)\}", session_id_template)

    for ph in placeholders:
        if ph in df.columns:
            session_id_format = session_id_format.replace(
                "{{{0}}}".format(ph), "%s", 1
            )
            format_args.append(col(ph))
        else:
            session_id_format = session_id_format.replace(
                "{{{0}}}".format(ph), "NULL"
            )

    if format_args:
        df = df.withColumn(
            "session_id", format_string(session_id_format, *format_args)
        )
    else:
        df = df.withColumn("session_id", lit(session_id_template))

    return df


# ---------------------------------------------------------------------------
# OpenSim coordinate-transformation analysis (from code/com.py)
# ---------------------------------------------------------------------------

def analyze(
    model_path,
    storage_path,
    ex_loads_path,
    muscle_analyze=False,
    force_report=True,
    JointReaction=True,
    body_kinematics=True,
    iaa=True,
):
    # type: (str, str, str, bool, bool, bool, bool, bool) -> Any
    """Run the OpenSim AnalyzeTool for coordinate transformation analysis.

    Wraps ``opensim.AnalyzeTool`` to compute muscle analysis, force
    reports, joint reactions, body kinematics and induced-acceleration
    analysis on a given model / states trajectory.

    Requires the ``opensim`` package to be installed.
    """
    if osim is None:
        raise ImportError(
            "opensim パッケージがインストールされていません。"
            "'conda install opensim' を実行してください。"
        )

    output_dir = os.path.dirname(model_path)
    xml_path = os.path.join(output_dir, "analysis.xml")

    if not os.path.exists(output_dir):
        os.makedirs(output_dir)

    analyze_tool = osim.AnalyzeTool()
    analyze_tool.setName("analyze")
    analyze_tool.setResultsDir(output_dir)
    analyze_tool.setModelFilename(model_path)
    analyze_tool.setStatesFileName(storage_path)
    analyze_tool.setExternalLoadsFileName(ex_loads_path)

    storage = osim.Storage(storage_path)
    analyze_tool.setInitialTime(storage.getFirstTime())
    analyze_tool.setFinalTime(storage.getLastTime())

    if muscle_analyze is True:
        analyze_tool.updAnalysisSet().cloneAndAppend(osim.MuscleAnalysis())
    if force_report is True:
        analyze_tool.updAnalysisSet().cloneAndAppend(osim.ForceReporter())
    if JointReaction is True:
        jr = osim.JointReaction()
        jr.setName("JointReaction")
        analyze_tool.updAnalysisSet().cloneAndAppend(jr)
    if body_kinematics is True:
        analyze_tool.updAnalysisSet().cloneAndAppend(osim.BodyKinematics())

    solution = None  # type: Any
    if iaa is True:
        model = osim.Model(model_path)  # noqa: F841
        solution = osim.MocoTrajectory(storage_path)  # noqa: F841
        analysis_set = osim.AnalysisSet()  # noqa: F841
        iaa_obj = osim.InducedAccelerations()
        iaa_obj.setName("InducedAccelerations")
        analysis_set.cloneAndAppend(iaa_obj)
        analyze_tool.updAnalysisSet().cloneAndAppend(iaa_obj)

    analyze_tool.updControllerSet().cloneAndAppend(
        osim.PrescribedController(storage_path)
    )
    analyze_tool.printToXML(xml_path)

    analyze_tool = osim.AnalyzeTool(xml_path)
    analyze_tool.run()
    return solution
