"""インジェスト・パイプラインの統括。

このモジュールは、ワークフローの実行エントリーポイント（旧 run_workflow.py）と、
データ変換ロジック（旧 data_converter.py）を1つのパイプラインモジュールに統合したものです。

主なエントリーポイント:
* run_workflow(config_path) -- CLI（コマンドライン）に対応した最上位のエントリーポイント。
  Sparkセッションを起動し、変換パイプラインを実行したあと、必要に応じてデータ検証モデルを呼び出します。
* data_converter(spark, config_path) -- 変換処理のメインパイプライン。
  設定ファイルを読み込み、definitionテーブルを登録し、各リーダー（C3D等）を通じてファイルを処理したあと、
  factテーブルをIcebergに書き込みます。
"""

from __future__ import annotations

import os
import re
import sys
from pathlib import Path
from typing import Dict, Optional

import pandas as pd
import yaml

from pyspark.sql import SparkSession

from harmo.config.loader import load_config, get_primary_study_id
from harmo.config.hed import enrich_sessions_with_hed, load_primary_hed_definition
from harmo.config.schema import validate_config
from harmo.config.validator import validate_study
from harmo.evidence.io import source_hashes
from harmo.warehouse.connection import get_warehouse_path, get_spark_session
from harmo.ingest.warehouse import (
    ingest_definition_table,
    print_summary_report,
    write_fact_tables,
)
from harmo.ingest.transform import NORMALIZATION_FUNCS
from harmo.ingest.session_hed import persist_session_hed

# Reader functions are expected to live in the readers sub-package.
# They may be populated by a separate migration step.
from harmo.ingest.readers import process_fact_batch, filter_files_needing_conversion
from harmo.ingest.utils.mat_preconvert import preconvert_mat_tables


# ---------------------------------------------------------------------------
# Main data-converter pipeline (from data_converter.py ``main``)
# ---------------------------------------------------------------------------


def data_converter(
    spark: SparkSession,
    config_path: str,
    source_digests: Optional[Dict[str, str]] = None,
) -> Dict[str, str]:
    """Run the full data-conversion pipeline.

    1. Register dimension tables from YAML / CSV definitions.
    2. Process fact-table file mappings through readers.
    3. Write collected DataFrames to Iceberg.
    4. Print a summary report.

    Parameters
    ----------
    spark:
        Active Spark session.
    config_path:
        Path to the study ``config.yaml``.

    Returns
    -------
    dict
        SHA-256 digest by definition, raw, and preconverted input path. When a
        mutable ``source_digests`` mapping is supplied, it is updated during
        execution so a failed run can retain the inputs observed before failure.
    """
    config = load_config(config_path)
    if source_digests is None:
        source_digests = {}

    input_dir_aliases = config.get("input_dir_aliases", {})
    definitions = config.get("definitions", {})
    definition_paths = sorted(
        {
            str(Path(definition["path"]).resolve())
            for definition in definitions.values()
            if definition.get("path") and Path(definition["path"]).is_file()
        }
    )
    source_digests.update(source_hashes(definition_paths))
    hed_dict, hed_def_key, hed_errors = load_primary_hed_definition(config)

    if hed_errors:
        for hed_err in hed_errors:
            print("  [警告] {}".format(hed_err))
    if hed_dict is not None:
        print(
            "  [情報] HED 定義 '{}' をロードしました ({} tags)".format(
                hed_def_key, len(hed_dict)
            )
        )

    # --- 0. Get shared study_id ---
    default_study_id = get_primary_study_id(definitions)

    # --- 1. Dimension table registration ---
    for key, definition in definitions.items():
        table_label = definition.get("table_name", "metadata-only")
        print("処理中: {} ({})".format(key, table_label))
        source_type = definition.get("source_type", "yaml")
        file_path = definition["path"]
        pd_df = None  # type: Optional[pd.DataFrame]

        if source_type == "yaml":
            with open(file_path, "r", encoding="utf-8") as f:
                yaml_data = yaml.safe_load(f)
            data_to_load = yaml_data.get(definition["yaml_key"])

            if not data_to_load:
                print(
                    "  警告: {} の yaml_key '{}' が見つかりません。".format(
                        key, definition["yaml_key"]
                    )
                )
                continue

            if isinstance(data_to_load, list) and default_study_id is not None:
                for item in data_to_load:
                    if isinstance(item, dict) and "study_id" not in item:
                        item["study_id"] = default_study_id

            # カルパー関数を呼び出す用のフラッグを削除
            if key == "channels" and isinstance(data_to_load, list):
                sanitized_rows = []
                for item in data_to_load:
                    if isinstance(item, dict):
                        clean_item = dict(item)
                        clean_item.pop("calc_cop", None)
                        sanitized_rows.append(clean_item)
                    else:
                        sanitized_rows.append(item)
                data_to_load = sanitized_rows

            func_name = definition.get("normalize_func")
            if func_name and func_name in NORMALIZATION_FUNCS:
                if func_name == "normalize_markers":
                    study_id_arg = yaml_data.get("study_id")
                    if study_id_arg is None:
                        study_id_arg = default_study_id

                    if study_id_arg is None:
                        print(
                            "  警告: {} に 'study_id' がなく、デフォルトIDも取得できませんでした。".format(
                                key
                            )
                        )
                        continue

                    pd_df = NORMALIZATION_FUNCS[func_name](study_id_arg, data_to_load)
                else:
                    pd_df = NORMALIZATION_FUNCS[func_name](data_to_load)
            else:
                pd_df = pd.DataFrame(data_to_load)

        elif source_type in ["csv", "tsv"]:
            reader_options = definition.get("reader_options", {})
            pd_df = pd.read_csv(file_path, **reader_options)

            if default_study_id is not None and "study_id" not in pd_df.columns:
                print(
                    "  [情報] CSVデータに study_id 列を追加します: {}".format(
                        default_study_id
                    )
                )
                pd_df["study_id"] = default_study_id

            short_name = definition.get("table_name", "").rsplit(".", 1)[-1]
            if short_name == "session_info" and hed_dict is not None:
                pd_df, hed_session_errors = enrich_sessions_with_hed(
                    session_df=pd_df,
                    file_mappings=config.get("file_mappings", []),
                    hed_dictionary=hed_dict,
                )
                if hed_session_errors:
                    for err in hed_session_errors:
                        print("  [警告] HED session展開: {}".format(err))
                else:
                    print("  [情報] session_info に HED 列を展開しました。")
        elif source_type == "json":
            # JSON definitions are metadata by default.
            # When expand=true is set, build session_hed and persist it.
            if bool(definition.get("expand", False)):
                target_table = definition.get(
                    "table_name", "local.warehouse.session_hed"
                )
                session_hed_df = persist_session_hed(
                    config_path=config_path,
                    hed_json_path=file_path,
                    json_key=definition.get("json_key"),
                    target_table=target_table,
                    dry_run=False,
                    spark=spark,
                )
                print(
                    "  [情報] JSON展開で {} 行を {} に永続化しました。".format(
                        len(session_hed_df),
                        target_table,
                    )
                )
            continue
        else:
            print(
                "  警告: 未知の source_type '{}' です。スキップします。".format(
                    source_type
                )
            )
            continue

        if pd_df is not None:
            ingest_definition_table(spark, pd_df, definition["table_name"])

    # --- 2. Fact-table batch processing ---
    dfs_to_write = {}

    for rule_config in config.get("file_mappings", []):
        if rule_config.get("enabled", True) is False:
            print(
                "  [情報] quarantined mappingをスキップ: {}".format(
                    rule_config.get("name", "?")
                )
            )
            continue

        alias = rule_config.get("base_input_dir_alias")
        if not alias or alias not in input_dir_aliases:
            continue
        base_input_dir = input_dir_aliases[alias]

        # --- Regex preparation ---
        spark_regex_str = rule_config["input_path_template"]
        patterns = rule_config.get("path_patterns", {})
        for ph, pattern in patterns.items():
            spark_regex_str = spark_regex_str.replace("{{{0}}}".format(ph), pattern)

        python_regex_str = spark_regex_str.replace("/", r"[\\/]")
        rule_regex = re.compile(python_regex_str)

        file_path_list = []
        base_path = Path(base_input_dir)

        # Infer extension from the template
        file_extension = os.path.splitext(rule_config["input_path_template"])[-1]
        if not file_extension:
            file_extension = "*.txt"

        for p in base_path.rglob("*{}".format(file_extension)):
            relative_path = str(p.relative_to(base_path)).replace(os.path.sep, "/")
            if rule_regex.search(relative_path):
                file_path_list.append(str(p.resolve()))

        file_path_list = sorted(set(file_path_list))

        if not file_path_list:
            message = "{} に一致するファイルが見つかりません。".format(
                rule_config["name"]
            )
            if rule_config.get("allow_empty", False):
                print("  [情報] {} allow_empty=trueのため続行します。".format(message))
                continue
            raise FileNotFoundError(message)

        original_hashes = source_hashes(file_path_list)
        source_digests.update(original_hashes)

        rule_config["base_input_dir_for_udf"] = str(base_path.resolve())
        rule_config["_config_dir"] = os.path.dirname(os.path.abspath(config_path))

        # --- Pass regex/placeholders to Spark ---
        rule_config["spark_native_regex_pattern"] = spark_regex_str
        # configの順番でなく、パスの順番で解いて行って
        template_placeholders = re.findall(
            r"\{(\w+)\}", rule_config["input_path_template"]
        )
        ordered_keys = []
        seen = set()
        for key in template_placeholders:
            if key in patterns and key not in seen:
                ordered_keys.append(key)
                seen.add(key)

        # config書きミスようの後方互換
        for key in patterns.keys():
            if key not in seen:
                ordered_keys.append(key)
                seen.add(key)

        rule_config["spark_native_placeholder_keys"] = ordered_keys

        # Inject study_id
        if "static_metadata" not in rule_config:
            rule_config["static_metadata"] = {}

        if (
            "study_id" not in rule_config["static_metadata"]
            and default_study_id is not None
        ):
            rule_config["static_metadata"]["study_id"] = default_study_id

        # --- Two-stage .mat loading: scipy probe -> Octave fallback ---
        if (
            rule_config.get("mat_preconvert", False)
            and rule_config.get("file_format") == "mat"
            and not rule_config.get("reader_plugin")
        ):
            readable_files, needs_conversion_files = filter_files_needing_conversion(
                file_path_list, rule_config
            )
            if needs_conversion_files:
                converted_files = preconvert_mat_tables(
                    needs_conversion_files, base_input_dir
                )
                file_path_list = sorted(set(readable_files + converted_files))
            else:
                file_path_list = readable_files
                print(
                    "  [情報] 全ファイルがscipyで直接読み込み可能です。Octave変換をスキップします。"
                )

        if not file_path_list:
            raise RuntimeError(
                "{}: no readable files remained after preconversion".format(
                    rule_config["name"]
                )
            )

        final_hashes = (
            original_hashes
            if set(file_path_list) == set(original_hashes)
            else source_hashes(file_path_list)
        )
        source_digests.update(final_hashes)
        rule_config["_source_hashes"] = final_hashes

        final_df = process_fact_batch(
            spark=spark,
            file_path_list=file_path_list,
            rule_config=rule_config,
        )

        if final_df is not None:
            table_name = rule_config["iceberg_table"]
            if table_name not in dfs_to_write:
                dfs_to_write[table_name] = []
            dfs_to_write[table_name].append(final_df)

    # --- 3. Write ---
    write_fact_tables(spark, dfs_to_write)

    # --- 4. Summary report ---
    print_summary_report(spark, dfs_to_write, definitions)

    # --- 5. Generate osim_analysis_config.yaml (if marker_metadata exists) ---
    _generate_osim_analysis_config(config_path, definitions)
    return source_digests


# ---------------------------------------------------------------------------
# OpenSim analysis config auto-generation (post-ingest)
# ---------------------------------------------------------------------------


def _generate_osim_analysis_config(config_path: str, definitions: dict) -> None:
    """Generate osim_analysis_config.yaml from marker_metadata.yaml after ingest.

    If marker_metadata.yaml exists in the config directory and
    osim_analysis_config.yaml does not yet exist, this function auto-generates
    the OpenSim analysis config with values derived from marker metadata
    (scaling pairs, marker weights, etc.).

    Parameters
    ----------
    config_path : str
        Path to config.yaml
    definitions : dict
        Definitions dict from config (contains markers section path)
    """
    try:
        from harmo.analysis.osim_config import generate_template_yaml
    except ImportError:
        print(
            "  [情報] osim_config module not available. Skipping osim_analysis_config generation."
        )
        return

    config_dir = os.path.dirname(os.path.abspath(config_path))
    osim_config_path = os.path.join(config_dir, "osim_analysis_config.yaml")

    # Check if osim_analysis_config.yaml already exists
    if os.path.exists(osim_config_path):
        print(f"  [情報] {osim_config_path} は既に存在するため、スキップします。")
        return

    # Resolve marker_metadata.yaml path from definitions
    markers_def = definitions.get("markers", {})
    if not markers_def.get("path"):
        print(
            "  [情報] marker_metadata が定義されていないため、osim_analysis_config の生成をスキップします。"
        )
        return

    marker_metadata_path = os.path.join(config_dir, markers_def["path"])
    if not os.path.exists(marker_metadata_path):
        print(
            f"  [警告] {marker_metadata_path} が見つかりません。osim_analysis_config の生成をスキップします。"
        )
        return

    # Generate the config
    try:
        output_path = generate_template_yaml(marker_metadata_path, osim_config_path)
        print(f"  [成功] osim_analysis_config.yaml を生成しました: {output_path}")
    except Exception as e:
        print(f"  [警告] osim_analysis_config の生成に失敗しました: {e}")


# ---------------------------------------------------------------------------
# Workflow entry point (from run_workflow.py ``main``)
# ---------------------------------------------------------------------------


def run_workflow(config_path: Optional[str] = None) -> None:
    """Top-level workflow entry point.

    Creates a Spark session configured for Iceberg, runs the
    data-conversion pipeline, and optionally the study-model step.

    Parameters
    ----------
    config_path:
        Path to the study ``config.yaml``.  When ``None``, the first
        positional CLI argument (``sys.argv[1]``) is used.
    """
    if config_path is None:
        if len(sys.argv) <= 1:
            print("使用法: python -m harmo.ingest.pipeline <config.yaml>")
            sys.exit(1)
        config_path = sys.argv[1]

    config = load_config(config_path)
    dataset_status = config.get("dataset_status", "active")
    if dataset_status != "active":
        raise ValueError(
            "dataset config is {}; ingestion is disabled: {}".format(
                dataset_status, config.get("readiness_notes", "no readiness note")
            )
        )

    config_errors = validate_config(config) + validate_study(config)
    if config_errors:
        raise ValueError(
            "dataset config validation failed:\n- {}".format("\n- ".join(config_errors))
        )

    warehouse_path = config.get("warehouse_path")
    resolved_warehouse = get_warehouse_path(warehouse_path)

    spark = None
    workflow_error = None
    source_digests = {}  # type: Dict[str, str]

    try:
        spark = get_spark_session(warehouse_path=resolved_warehouse)
        data_converter(spark, config_path, source_digests)
        try:
            from harmo.model.osim_io import main as prepare_osim_model
        except ImportError:
            prepare_osim_model = None

        if prepare_osim_model is not None:
            try:
                prepare_osim_model(config_path)
            except Exception as e:
                print("  [警告] OpenSim モデル準備に失敗しました: {}".format(e))
        else:
            print("  [情報] opensim が見つからないため、モデル準備をスキップします。")
    except Exception as exc:
        workflow_error = exc
        raise
    finally:
        stop_error = None
        if spark is not None:
            try:
                spark.stop()
            except Exception as exc:
                stop_error = exc

        effective_error = workflow_error or stop_error
        manifest_error = None
        try:
            from harmo.evidence.run import capture_run_manifest

            manifest_path = capture_run_manifest(
                config_path=config_path,
                warehouse_uri=resolved_warehouse,
                operation="ingest",
                status="failed" if effective_error else "completed",
                error=str(effective_error) if effective_error else None,
                source_digests=source_digests,
            )
            print("Evidence manifest: {}".format(manifest_path))
        except Exception as exc:
            manifest_error = exc

        # Never report a successful scientific run without its evidence
        # manifest.  If the workflow itself already failed, preserve that
        # original exception instead of masking it with a secondary failure.
        if workflow_error is None and (stop_error or manifest_error):
            issues = []
            if stop_error:
                issues.append("Spark stop failed: {}".format(stop_error))
            if manifest_error:
                issues.append(
                    "evidence manifest generation failed: {}".format(manifest_error)
                )
            cause = manifest_error or stop_error
            raise RuntimeError("; ".join(issues)) from cause
        if manifest_error:
            print(
                "  [警告] Evidence manifest generation failed: {}".format(
                    manifest_error
                )
            )

    print("すべての処理が完了しました。")


def run_ingest_pipeline(config_path: str) -> None:
    # type: (str) -> None
    """Backward-compatible ingest entry point used by CLI."""
    run_workflow(config_path)


# ---------------------------------------------------------------------------
# CLI entry
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    run_workflow()
