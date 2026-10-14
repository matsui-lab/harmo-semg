"""harmo.ingest.readers -- format-specific file readers."""

from __future__ import annotations
import hashlib
import importlib
import os
import re
from concurrent.futures import ThreadPoolExecutor, as_completed
from functools import partial
from pathlib import Path
from typing import Any, Dict, List
import pandas as pd


def read_mat_file(*args: Any, **kwargs: Any):
    """Lazily expose the pandas MAT reader without importing PySpark."""
    from harmo.ingest.readers.mat import read_mat_file as implementation

    return implementation(*args, **kwargs)


def filter_files_needing_conversion(*args: Any, **kwargs: Any):
    """Lazily expose MAT pre-conversion classification."""
    from harmo.ingest.utils.mat_preconvert import (
        filter_files_needing_conversion as implementation,
    )

    return implementation(*args, **kwargs)


def _sha256_file(file_path: str) -> str:
    hasher = hashlib.sha256()
    with open(file_path, "rb") as f:
        for chunk in iter(lambda: f.read(1024 * 1024), b""):
            hasher.update(chunk)
    return hasher.hexdigest()


def _validate_plugin_output(df: pd.DataFrame, plugin_name: str, file_path: str) -> None:
    required_cols = {"time", "channel", "value"}
    missing = required_cols.difference(df.columns)
    if missing:
        raise ValueError(
            "Plugin '{}' output missing required columns {} for file {}".format(
                plugin_name,
                sorted(missing),
                file_path,
            )
        )


def _load_mat_with_explicit_plugin(
    spark: Any, file_path_list: List[str], rule_config: dict
):
    plugin_name = rule_config.get("reader_plugin")
    if not plugin_name:
        return None

    plugin_options = dict(rule_config.get("plugin_options", {}))
    plugin_options.setdefault("_rule_config", rule_config)
    module = importlib.import_module("harmo.ingest.plugins.{}".format(plugin_name))
    plugin_cls = getattr(module, "Plugin", None) or getattr(
        module, "PluginInstance", None
    )
    if plugin_cls is None:
        raise AttributeError(
            "Plugin module '{}' must expose 'Plugin' or 'PluginInstance'".format(
                plugin_name
            )
        )
    plugin = plugin_cls() if callable(plugin_cls) else plugin_cls

    plugin_version = str(getattr(plugin, "version", "unknown"))
    all_data_dfs: List[pd.DataFrame] = []
    for file_path in file_path_list:
        out_df = plugin.extract(file_path, plugin_options)
        if not isinstance(out_df, pd.DataFrame):
            raise TypeError(
                "Plugin '{}' must return pandas.DataFrame, got {}".format(
                    plugin_name,
                    type(out_df),
                )
            )
        _validate_plugin_output(out_df, plugin_name, file_path)
        if "source_file_path" not in out_df.columns:
            out_df["source_file_path"] = file_path
        out_df["_reader_plugin"] = plugin_name
        out_df["_reader_plugin_version"] = plugin_version
        out_df["_reader_plugin_source_sha256"] = _sha256_file(file_path)
        all_data_dfs.append(out_df)

    return _batched_pd_to_spark(spark, all_data_dfs)


def _read_files_parallel(
    file_path_list,
    read_fn,
    format_label="file",
    max_workers=None,
    allow_partial=False,
):
    """Read all inputs without silently dropping failed source files.

    ``allow_partial=True`` is reserved for explicit exploratory workflows.  The
    default is fail-closed because an unnoticed missing recording invalidates
    row counts, source manifests, and downstream scientific comparisons.
    """
    if max_workers is None:
        max_workers = min(len(file_path_list), os.cpu_count() or 4)
    if len(file_path_list) <= 1:
        results = []
        for fp in file_path_list:
            try:
                df = read_fn(fp)
                df["source_file_path"] = fp
                results.append(df)
            except Exception as e:
                if not allow_partial:
                    raise RuntimeError(
                        "{} 読み込み失敗: {}, {}".format(format_label, fp, e)
                    ) from e
                print("  [警告] {} 読み込み失敗: {}, {}".format(format_label, fp, e))
        return results
    ordered_results = [None] * len(file_path_list)

    def _read_one(index, file_path):
        try:
            df = read_fn(file_path)
            df["source_file_path"] = file_path
            return index, df, None
        except Exception as e:
            return index, None, e

    with ThreadPoolExecutor(max_workers=max_workers) as executor:
        futures = [
            executor.submit(_read_one, i, fp) for i, fp in enumerate(file_path_list)
        ]
        failures = []
        for future in as_completed(futures):
            idx, df, error = future.result()
            ordered_results[idx] = df
            if error is not None:
                failures.append((idx, file_path_list[idx], error))

    if failures and not allow_partial:
        failures.sort(key=lambda failure: failure[0])
        details = "; ".join("{}: {}".format(path, error) for _, path, error in failures)
        raise RuntimeError(
            "{} 読み込み失敗: {}".format(format_label, details)
        ) from failures[0][2]
    for _, path, error in sorted(failures, key=lambda failure: failure[0]):
        print("  [警告] {} 読み込み失敗: {}, {}".format(format_label, path, error))
    return [df for df in ordered_results if df is not None]


def _batched_pd_to_spark(spark, pd_dfs, batch_size=200):
    """Convert pandas DataFrames to a Spark DataFrame via Parquet temp files.

    各 pandas DataFrame を一時 Parquet ファイルに書き出してから
    spark.read.parquet() でネイティブに読み込む。
    これにより py4j 経由の JVM シリアライズを回避し、
    Java heap space OOM を防ぐ。

    batch_size は後方互換のために残しているが使用しない。
    """
    import shutil
    import tempfile
    from pyspark import StorageLevel
    from pyspark.sql.functions import col

    if not pd_dfs:
        return None

    # Spark の local.dir 配下に一時ディレクトリを作成する。
    # Spark セッションのライフサイクルと揃えることで意図しない
    # ディスク使用量の増大を避ける。
    try:
        spark_local_dir = spark.conf.get("spark.local.dir", "/tmp")
    except Exception:
        spark_local_dir = "/tmp"
    tmp_dir = tempfile.mkdtemp(prefix="harmo_pd2spark_", dir=spark_local_dir)

    try:
        # Normalize column dtypes across files to avoid parquet schema conflicts.
        all_cols = set()
        for df in pd_dfs:
            all_cols.update(df.columns)

        col_target_type: Dict[str, str] = {}
        for c in all_cols:
            saw_non_numeric = False
            for df in pd_dfs:
                if c not in df.columns:
                    continue
                dtype = df[c].dtype
                if pd.api.types.is_numeric_dtype(dtype) or pd.api.types.is_bool_dtype(
                    dtype
                ):
                    continue
                saw_non_numeric = True
                break
            col_target_type[c] = "string" if saw_non_numeric else "float"

        # 1 ファイルずつ Parquet に書き出す。
        # Python ヒープのピーク使用量が「1 DF 分」に限定される。
        for i, df in enumerate(pd_dfs):
            for c, t in col_target_type.items():
                if c not in df.columns:
                    continue
                if t == "float":
                    df[c] = pd.to_numeric(df[c], errors="coerce").astype("float64")
                else:
                    df[c] = df[c].astype("string")
            part_path = os.path.join(tmp_dir, "part_{:05d}.parquet".format(i))
            df.to_parquet(part_path, index=False)

        # Spark がファイル一覧を読み込んで遅延 DataFrame を構築する。
        sdf = spark.read.parquet(tmp_dir)

        default_parallelism = max(int(spark.sparkContext.defaultParallelism), 1)
        target_partitions = max(
            default_parallelism, min(len(pd_dfs), default_parallelism * 4)
        )
        if target_partitions > 1:
            if "source_file_path" in sdf.columns:
                sdf = sdf.repartition(target_partitions, col("source_file_path"))
            else:
                sdf = sdf.repartition(target_partitions)

        # DISK_ONLY で永続化して tmp_dir を安全に削除できる状態にする。
        sdf = sdf.persist(StorageLevel.DISK_ONLY)
        sdf.count()  # マテリアライズをトリガー
        return sdf
    finally:
        shutil.rmtree(tmp_dir, ignore_errors=True)


def _load_files_to_spark(spark, file_path_list, rule_config):
    from pyspark.sql.functions import lit

    from harmo.ingest.readers.c3d import load_c3d_to_spark
    from harmo.ingest.readers.csv import (
        apply_horizontal_explode as csv_horizontal_explode,
    )
    from harmo.ingest.readers.csv import read_csv_file, read_multi_rate_csv
    from harmo.ingest.readers.mat import load_mat_to_spark, load_mat_with_template
    from harmo.ingest.readers.sto import load_opensim_to_spark

    file_format = rule_config.get("file_format", "csv")
    reader_options = rule_config.get("reader_options", {})
    if rule_config.get("data_orientation") == "horizontal":
        df = csv_horizontal_explode(spark, file_path_list, reader_options)
        if df is None:
            return None
        if "source_file_path" not in df.columns:
            df = df.withColumn("source_file_path", lit(file_path_list[0]))
        return df
    if file_format == "mat":
        plugin_df = _load_mat_with_explicit_plugin(spark, file_path_list, rule_config)
        if plugin_df is not None:
            print(
                "  [情報] 明示指定プラグイン '{}' を使用して MAT を読み込みます。".format(
                    rule_config.get("reader_plugin")
                )
            )
            return plugin_df

        melt_options = rule_config.get("melt_options", {})
        time_candidates = melt_options.get("time_column_candidates", [])

        # Pass time-column hints from the upper pipeline to the MAT reader
        # so it can avoid generating synthetic frame-index time when
        # downstream renaming (e.g., Header -> time) is configured.
        mat_reader_options = dict(reader_options)
        if "time_column_candidates" not in mat_reader_options and time_candidates:
            mat_reader_options["time_column_candidates"] = time_candidates

        if rule_config.get("mat_data_key_template"):
            mat_rule_config = dict(rule_config)
            mat_rule_config["reader_options"] = mat_reader_options
            return load_mat_with_template(spark, file_path_list, mat_rule_config)
        return load_mat_to_spark(spark, file_path_list, mat_reader_options)
    if file_format == "c3d":
        return load_c3d_to_spark(spark, file_path_list, rule_config)
    if file_format in ("opensim", "sto", "mot", "trc"):
        return load_opensim_to_spark(spark, file_path_list, round_trip=reader_options.get("round_trip", False))
    sep = reader_options.get("sep", "\t")
    comment = reader_options.get("comment", "#")
    header = reader_options.get("header", True)

    multi_rate_info = rule_config.get("multi_rate_info")
    if multi_rate_info:
        default_fs = rule_config.get("default_sampling_rate")
        read_fn = partial(
            read_multi_rate_csv,
            multi_rate_info=multi_rate_info,
            sep=sep,
            comment=comment,
            header=header,
            default_sampling_rate=default_fs,
        )
    else:
        read_fn = partial(read_csv_file, sep=sep, comment=comment, header=header,
                          float_precision=reader_options.get("float_precision"))

    all_data_dfs = _read_files_parallel(file_path_list, read_fn, format_label="CSV/TSV")
    if not all_data_dfs:
        return None
    return _batched_pd_to_spark(spark, all_data_dfs)


def _extract_path_metadata(df, rule_config):
    from pyspark.sql.functions import col, regexp_extract, regexp_replace

    regex_pattern = rule_config.get("spark_native_regex_pattern")
    placeholder_keys = rule_config.get("spark_native_placeholder_keys", [])
    base_input_dir = rule_config.get("base_input_dir_for_udf", "")
    if base_input_dir:
        base_escaped = re.escape(base_input_dir.rstrip("/\\"))
        df = df.withColumn(
            "_relative_path",
            regexp_replace(
                col("source_file_path"), "^{}[/\\\\]".format(base_escaped), ""
            ),
        )
    else:
        df = df.withColumn("_relative_path", col("source_file_path"))
    if regex_pattern:
        for idx, key in enumerate(placeholder_keys, start=1):
            if key not in df.columns:
                df = df.withColumn(
                    key, regexp_extract(col("_relative_path"), regex_pattern, idx)
                )
    return df


def process_fact_batch(spark, file_path_list, rule_config):
    from pyspark.sql.functions import broadcast, col, expr, lit

    from harmo.evidence.io import source_hashes
    from harmo.ingest.readers.c3d import (
        _load_cop_channels_from_definitions,
        _load_marker_names_from_definitions,
    )
    from harmo.ingest.transform import (
        add_percentage_column,
        apply_column_normalization,
        apply_signal_extraction,
        build_session_id,
        melt_channels_to_long,
    )

    config_dir = rule_config.get("_config_dir")

    def _resolve(p):
        if not p or os.path.isabs(p):
            return p
        return os.path.join(config_dir, p) if config_dir else p

    # Load channel definitions for filtering if available
    valid_channel_names = None
    chan_defs_path = rule_config.get("channel_definitions_path") or rule_config.get(
        "point_channel_definitions_path"
    )
    if chan_defs_path:
        resolved_chan_path = _resolve(chan_defs_path)
        if os.path.exists(resolved_chan_path):
            import yaml

            with open(resolved_chan_path, "r", encoding="utf-8") as f:
                chan_data = yaml.safe_load(f)
            channels_list = chan_data.get("channels", [])
            valid_channel_names = [
                c.get("column_name") for c in channels_list if c.get("column_name")
            ]

    if rule_config.get("calc_cop"):
        yaml_path = rule_config.get("channel_definitions_path") or rule_config.get(
            "point_channel_definitions_path"
        )
        if yaml_path:
            rule_config["cop_channels"] = _load_cop_channels_from_definitions(
                _resolve(yaml_path)
            )
    if rule_config.get("filter_markers"):
        yaml_path = rule_config.get("marker_definitions_path")
        if yaml_path:
            rule_config["marker_names"] = _load_marker_names_from_definitions(
                _resolve(yaml_path)
            )
    df = _load_files_to_spark(spark, file_path_list, rule_config)
    if df is None:
        return None
    if "source_file_path" in df.columns:
        input_hashes = rule_config.get("_source_hashes")
        if input_hashes is None:
            input_hashes = source_hashes(file_path_list)
        if set(input_hashes) != set(file_path_list):
            raise ValueError(
                "precomputed source hashes do not match the reader input file set"
            )
        hash_rows = []
        for path, digest in input_hashes.items():
            aliases = {path}
            if "://" not in path:
                aliases.add(Path(path).resolve().as_uri())
            hash_rows.extend((alias, digest) for alias in aliases)
        hash_df = spark.createDataFrame(
            sorted(set(hash_rows)), ["source_file_path", "_source_sha256"]
        )
        df = df.join(broadcast(hash_df), on="source_file_path", how="left")
        if df.filter(col("_source_sha256").isNull()).limit(1).count() > 0:
            raise ValueError(
                "source_file_path could not be matched to its pre-transformation SHA-256"
            )

    # Keep the dataset distributed before expensive melt/window operations.
    default_parallelism = max(int(spark.sparkContext.defaultParallelism), 1)
    configured_partitions = rule_config.get("target_partitions")
    target_partitions = (
        configured_partitions
        if configured_partitions
        else max(default_parallelism, min(len(file_path_list), default_parallelism * 4))
    )
    if int(target_partitions) > 1 and df.rdd.getNumPartitions() < int(
        target_partitions
    ):
        if "source_file_path" in df.columns:
            df = df.repartition(int(target_partitions), col("source_file_path"))
        else:
            df = df.repartition(int(target_partitions))

    if "source_file_path" in df.columns:
        df = _extract_path_metadata(df, rule_config)
    static_metadata = rule_config.get("static_metadata", {})
    for key, value in static_metadata.items():
        df = df.withColumn(key, lit(value))
    if "subject_id" not in df.columns:
        for candidate in ("sub_id", "subject", "participant_id"):
            if candidate in df.columns:
                df = df.withColumn("subject_id", col(candidate).cast("string"))
                break
    session_template = rule_config.get("session_id_template")
    if session_template and "session_id" not in df.columns:
        df = build_session_id(df, session_template)
    if "study_id" not in df.columns:
        df = df.withColumn("study_id", lit(None))
    if "subject_id" not in df.columns:
        df = df.withColumn("subject_id", lit(None))
    if "session_id" not in df.columns:
        df = df.withColumn("session_id", lit(None))
    if "channel" not in df.columns or "value" not in df.columns:
        melt_options = rule_config.get("melt_options", {})
        time_candidates = melt_options.get("time_column_candidates", [])
        if "time" not in df.columns and time_candidates:
            for candidate in time_candidates:
                if candidate in df.columns:
                    df = df.withColumnRenamed(candidate, "time")
                    break
        # Identify all potential identifier columns to exclude from melt
        path_placeholders = rule_config.get("spark_native_placeholder_keys", [])
        extra_meta_keys = list(
            rule_config.get("reader_options", {}).get("extra_metadata_keys", {}).keys()
        )
        known_id_vars = [
            "time",
            "study_id",
            "subject_id",
            "session_id",
            "sensor_type",
            "unit",
            "data_type",
            "processed_id",
            "event_name",
            "activity",
            "trial",
            "step",
            "sub_id",
            "side",
            "ses_speed",
            "ses_trial",
            "source_file_path",
            "_source_sha256",
            "_relative_path",
            "_reader_plugin",
            "_reader_plugin_version",
            "_reader_plugin_source_sha256",
        ]
        id_vars = [
            c
            for c in set(
                known_id_vars
                + list(melt_options.get('id_columns', []))
                + path_placeholders
                + extra_meta_keys
                + list(static_metadata.keys())
            )
            if c in df.columns
        ]

        melted = melt_channels_to_long(
            df, id_vars, exclude_cols=["source_file_path", "_relative_path"]
        )
        if melted is None:
            return None
        df = melted

    # Filter by defined channels if applicable
    if valid_channel_names and "channel" in df.columns:
        print(
            "  [フィルタ] dim_channels に基づき {} 個のチャンネルに絞り込みます。".format(
                len(valid_channel_names)
            )
        )
        df = df.filter(col("channel").isin(valid_channel_names))

    df = apply_column_normalization(df, rule_config)
    filter_condition = rule_config.get("filter_condition")
    if filter_condition:
        df = df.filter(expr(filter_condition))
    if rule_config.get("extraction_rules") or rule_config.get("detect_edge"):
        df = apply_signal_extraction(df, rule_config)
    if rule_config.get("add_percentage_column") and "time" in df.columns:
        group_cols = [
            c
            for c in ["study_id", "subject_id", "session_id", "channel"]
            if c in df.columns
        ]
        if group_cols:
            df = add_percentage_column(df, group_cols)
    if "_relative_path" in df.columns:
        df = df.drop("_relative_path")
    return df


__all__ = ["read_mat_file", "filter_files_needing_conversion", "process_fact_batch"]
