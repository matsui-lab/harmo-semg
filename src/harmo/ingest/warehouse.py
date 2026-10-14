"""Iceberg table writing and validation utilities for the ingest layer.

This module consolidates:

* ``enforce_id_column_types`` / ``validate_id_values`` -- ID column type
  coercion and validation guards.
* ``run_validation`` -- schema-existence checks for required columns.
* ``ingest_definition_table`` -- dimension-table registration via Spark/Iceberg.
* ``write_fact_tables`` -- batched fact-table writing with schema evolution.
* ``print_summary_report`` -- post-pipeline summary of registered tables.
"""
from __future__ import annotations

from typing import Any, Dict, List, Optional, TYPE_CHECKING

from pyspark.sql import DataFrame, SparkSession
from pyspark.sql.functions import col, expr, lit
from pyspark.sql.types import IntegerType, StringType

from harmo.warehouse.connection import get_warehouse_path, iceberg_table_path

# ---------------------------------------------------------------------------
# ID column coercion (from ingest_data_spark.py)
# ---------------------------------------------------------------------------

def enforce_id_column_types(df: DataFrame) -> DataFrame:
    """Unify ID column types.

    * ``study_id`` -> ``IntegerType``
    * ``subject_id`` -> ``StringType``
    * ``notes`` -> ``StringType``
    """
    if "study_id" in df.columns:
        df = df.withColumn("study_id", col("study_id").cast(IntegerType()))
    if "subject_id" in df.columns:
        df = df.withColumn("subject_id", col("subject_id").cast(StringType()))
    if "notes" in df.columns:
        df = df.withColumn("notes", col("notes").cast(StringType()))
    return df


# ---------------------------------------------------------------------------
# ID value validation (from ingest_data_spark.py)
# ---------------------------------------------------------------------------

def validate_id_values(df: DataFrame, context_name: str) -> bool:
    """Validate ID columns.

    * ``study_id`` -- must not be NULL.
    * ``subject_id`` -- must not be NULL or empty string.
    """
    is_valid = True

    if "study_id" in df.columns:
        invalid_study_id = df.filter(expr("study_id IS NULL")).limit(1).count()
        if invalid_study_id > 0:
            print("  [検証エラー] '{}' に study_id が NULL の行があります。".format(context_name))
            is_valid = False

    if "subject_id" in df.columns:
        invalid_subject_id = (
            df.filter(expr("subject_id IS NULL OR trim(subject_id) = ''")).limit(1).count())
        if invalid_subject_id > 0:
            print("  [検証エラー] '{}' に subject_id が NULL/空文字の行があります。".format(context_name))
            is_valid = False

    return is_valid


# ---------------------------------------------------------------------------
# Schema existence check (from ingest_data_spark.py)
# ---------------------------------------------------------------------------

def run_validation(df: DataFrame, rule_name: str, required_cols: list) -> bool:
    """Check that *required_cols* exist in *df* and that ID values are valid."""
    df_cols = df.columns
    is_valid = True
    missing_cols = []  # type: List[str]

    for col_name in required_cols:
        if col_name not in df_cols:
            missing_cols.append(col_name)
            is_valid = False

    if is_valid:
        print("  検証クリア。")
    else:
        print("  [検証エラー] ルール '{}' で必須カラムが不足しています。".format(rule_name))
        print("  不足カラム: {}".format(", ".join(missing_cols)))

    if is_valid:
        is_valid = validate_id_values(df, rule_name)

    return is_valid


# ---------------------------------------------------------------------------
# Dimension table registration (from ingest_data_spark.py)
# ---------------------------------------------------------------------------

def ingest_definition_table(spark: SparkSession, df: "pd.DataFrame", table_name: str) -> None:
    """Append a Pandas DataFrame into an Iceberg dimension table.

    Parameters
    ----------
    spark:
        Active Spark session.
    df:
        Pandas DataFrame to write.
    table_name:
        Iceberg table name.  May be fully-qualified
        (``local.warehouse.xxx``) or unqualified (``xxx``).
    """
    import pandas as pd  # noqa: F811 -- local import to keep module header light

    df = spark.createDataFrame(df)
    df = enforce_id_column_types(df)

    if not validate_id_values(df, table_name):
        raise ValueError("ID列の値検証に失敗しました: {}".format(table_name))

    # Accept both fully-qualified and unqualified table names
    if table_name.count(".") >= 2:
        full_table_name = table_name
    else:
        full_table_name = "local.warehouse.{}".format(table_name)

    try:
        if spark.catalog.tableExists(full_table_name):
            extra_columns = [c for c in df.columns if c not in spark.table(full_table_name).columns]
            if extra_columns:
                for field in df.schema.fields:
                    if field.name in extra_columns:
                        spark.sql(
                            "ALTER TABLE {tbl} ADD COLUMN {col} {dtype}".format(
                                tbl=full_table_name,
                                col=field.name,
                                dtype=field.dataType.simpleString(),
                            )
                        )

            target_table_df = spark.table(full_table_name)
            target_schema = {
                field.name: field.dataType.simpleString()
                for field in target_table_df.schema.fields
            }
            target_columns = list(target_schema.keys())

            # notes must be string
            if "notes" in target_schema and target_schema["notes"] != "string":
                raise TypeError(
                    "既存テーブル '{}' の notes 型が {} です。"
                    "入力仕様では notes は string のため、テーブル再作成またはスキーマ修正が必要です。".format(
                        full_table_name, target_schema["notes"]
                    )
                )

            # Cast columns to match existing schema
            for col_name, spark_type in target_schema.items():
                if col_name in df.columns:
                    if col_name == "notes":
                        continue
                    df = df.withColumn(col_name, col(col_name).cast(spark_type))

            # Fill missing columns with NULL
            for col_name in target_columns:
                if col_name not in df.columns:
                    df = df.withColumn(col_name, lit(None))
            df = df.select(*target_columns)

            df.writeTo(full_table_name).append()
        else:
            writer = df.writeTo(full_table_name)
            writer.create()
    except Exception as e:
        print("Error writing to {}: {}".format(table_name, e))
        raise


# ---------------------------------------------------------------------------
# Fact table writing (extracted from data_converter.py)
# ---------------------------------------------------------------------------

def write_fact_tables(
    spark: SparkSession,
    dfs_to_write: Dict[str, List[DataFrame]],
) -> None:
    """Write batched DataFrames to their respective Iceberg fact tables.

    Parameters
    ----------
    spark:
        Active Spark session.
    dfs_to_write:
        Mapping of ``{table_name: [df, ...]}`` collected during pipeline
        execution.  Each DataFrame is appended to its target table; the
        table is created (partitioned by ``study_id``, ``subject_id``,
        ``session_id``) if it does not yet exist.
    """
    for table_name, df_list in dfs_to_write.items():
        if not df_list:
            continue
        print("  テーブル '{}' への書き込み開始 ({} バッチ)".format(table_name, len(df_list)))
        for i, df_to_write in enumerate(df_list):
            print("    - バッチ {}/{} を書き込み中...".format(i + 1, len(df_list)))
            table_exists = spark.catalog.tableExists(table_name)
            if table_exists:
                # Schema evolution: fill missing columns with NULL
                target_columns = spark.table(table_name).columns
                for col_name in target_columns:
                    if col_name not in df_to_write.columns:
                        df_to_write = df_to_write.withColumn(col_name, lit(None))
                df_to_write = df_to_write.select(*target_columns)
                # Iceberg ClusteredWriter はパーティションキー順を要求するため
                # 書き込み前にパーティション列でソートする。
                sort_keys = [c for c in ("study_id", "subject_id", "session_id") if c in df_to_write.columns]
                if sort_keys:
                    df_to_write = df_to_write.sortWithinPartitions(*sort_keys)
                df_to_write.writeTo(table_name).append()
            else:
                sort_keys = [c for c in ("study_id", "subject_id", "session_id") if c in df_to_write.columns]
                if sort_keys:
                    df_to_write = df_to_write.sortWithinPartitions(*sort_keys)
                df_to_write.writeTo(table_name).partitionedBy(
                    "study_id", "subject_id", "session_id"
                ).create()


# ---------------------------------------------------------------------------
# Summary report (extracted from data_converter.py)
# ---------------------------------------------------------------------------

def print_summary_report(
    spark: SparkSession,
    dfs_to_write: Dict[str, List[DataFrame]],
    definitions: Dict[str, Any],
) -> None:
    """Print a summary report of all registered Iceberg tables."""
    print("\n" + "=" * 40)
    print("  パイプライン実行 サマリーレポート")
    print("=" * 40)

    written_tables = list(dfs_to_write.keys())

    if not written_tables:
        print("  [情報] 今回の実行で書き込まれたファクトテーブルはありませんでした。")

    definition_tables = [d["table_name"] for d in definitions.values()]
    all_tables = sorted(list(set(written_tables + definition_tables)))

    print("\n### Iceberg テーブル登録状況 ###")

    for table_name in all_tables:
        if not spark.catalog.tableExists(table_name):
            print("\n--- テーブル: {} ---".format(table_name))
            print("  [エラー] テーブルが存在しません。")
            continue

        try:
            print("\n--- テーブル: {} ---".format(table_name))

            total_count_df = spark.sql(
                "SELECT COUNT(*) as total_rows FROM {}".format(table_name)
            )
            total_rows = total_count_df.first()["total_rows"]
            print("総行数: {:,}".format(total_rows))

            table_cols = spark.table(table_name).columns
            if "session_id" in table_cols and "subject_id" in table_cols:
                print("  パーティション別 行数 (Top 5):")
                count_df = spark.sql(
                    "SELECT study_id, subject_id, session_id, COUNT(*) as row_count "
                    "FROM {tbl} "
                    "GROUP BY study_id, subject_id, session_id "
                    "ORDER BY study_id, subject_id, session_id "
                    "LIMIT 5".format(tbl=table_name)
                )
                count_df.show(truncate=False)
            else:
                print("  データサンプル (Top 5):")
                spark.table(table_name).show(5, truncate=False)

        except Exception as e:
            print(
                "  [エラー] テーブル '{}' の集計中にエラーが発生しました: {}".format(
                    table_name, e
                )
            )

    print("\n" + "=" * 40)
    print("  サマリーレポート終了")
    print("=" * 40)
