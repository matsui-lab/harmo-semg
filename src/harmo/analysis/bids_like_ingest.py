"""BIDS-like input file ingestion for OpenSim analysis pipeline.

Ingests analysis *input* files (TRC, externalloads MOT, processed EMG STO)
into Iceberg tables ``osim_input_header`` and ``osim_input_value``.

This is distinct from :mod:`harmo.analysis.result_writer`, which handles
analysis *output* files.
"""
from __future__ import annotations

import os
import glob
import pandas as pd
from concurrent.futures import ThreadPoolExecutor
from pyspark.sql import SparkSession
from pyspark.sql.functions import lit

from harmo.analysis.result_writer import load_opensim_storage
from harmo.warehouse.connection import get_warehouse_path, get_spark_session


def read_trc(file_path):
    """Read a TRC (motion capture marker) file.

    Returns
    -------
    header_df : pd.DataFrame
        Single-row DataFrame with header metadata.
    data_df : pd.DataFrame
        Marker coordinate data with ``time`` column.
    """
    with open(file_path, "r") as f:
        header_lines = [next(f) for _ in range(5)]

    # --- 1. Header metadata ---
    header_keys = header_lines[1].strip().split("\t")
    header_values = header_lines[2].strip().split("\t")

    header_dict = {}
    limit = min(len(header_keys), len(header_values))
    for i in range(limit):
        if header_keys[i]:
            header_dict[header_keys[i]] = header_values[i]

    header_df = pd.DataFrame([header_dict])

    # --- 2. Column names (marker names + axes) ---
    marker_names_line = header_lines[3].rstrip("\n").split("\t")
    header_row = header_lines[4].rstrip("\n").split("\t")

    columns = marker_names_line[:2]  # Frame#, Time

    marker_name = ""
    for i in range(2, len(header_row)):
        if i < len(marker_names_line) and marker_names_line[i]:
            marker_name = marker_names_line[i]
        if header_row[i]:
            axis = header_row[i][0]
            columns.append(f"{marker_name}_{axis}")

    columns = columns[1:]  # drop Frame#

    # --- 3. Data body ---
    data_df = pd.read_csv(file_path, sep="\t", skiprows=5, header=None, index_col=0)

    if data_df.shape[1] > len(columns):
        data_df = data_df.iloc[:, : len(columns)]
    elif data_df.shape[1] < len(columns):
        columns = columns[: data_df.shape[1]]

    data_df.columns = columns
    data_df.rename(columns={"Time": "time"}, inplace=True)

    return header_df, data_df


def to_long_format_input(
    df: pd.DataFrame,
    study_id: int,
    subject_id: str,
    session_id: str,
    run_id: str,
    file_type: int,
):
    """Melt a wide DataFrame to long format with metadata columns."""
    id_vars = ["time"]
    if "frame" in df.columns:
        id_vars.append("frame")

    long_df = df.melt(id_vars=id_vars, var_name="column", value_name="value")

    long_df["study_id"] = study_id
    long_df["subject_id"] = subject_id
    long_df["session_id"] = session_id
    long_df["run_id"] = run_id
    long_df["type"] = file_type

    return long_df


def to_header_format_input(
    df: pd.DataFrame,
    study_id: int,
    subject_id: str,
    session_id: str,
    run_id: str,
    file_type: int,
):
    """Attach metadata columns to a header DataFrame."""
    df = df.copy()
    df["study_id"] = study_id
    df["subject_id"] = subject_id
    df["session_id"] = session_id
    df["run_id"] = run_id
    df["type"] = file_type
    return df


def ingest_bids_like_data(
    input_dir: str,
    run_id: str,
    spark: SparkSession = None,
):
    """Ingest BIDS-like input files into Iceberg tables.

    Discovers ``.trc``, ``*externalloads.mot``, and ``*processed_emg.sto``
    files in *input_dir*, converts them to long format, and writes to
    ``osim_input_header`` / ``osim_input_value``.
    """
    _warehouse_path = get_warehouse_path()
    created_spark = False
    if spark is None:
        spark = get_spark_session(warehouse_path=_warehouse_path)
        created_spark = True

    header_table = "local.warehouse.osim_input_header"
    value_table = "local.warehouse.osim_input_value"

    file_paths = (
        glob.glob(os.path.join(input_dir, "*.trc"))
        + glob.glob(os.path.join(input_dir, "*externalloads.mot"))
        + glob.glob(os.path.join(input_dir, "*processed_emg.sto"))
    )

    if not file_paths:
        print("[BIDS-like Ingest] No files found. Skipping.")
        if created_spark:
            spark.stop()
        return

    all_headers_pd = []
    all_values_pd = []
    import traceback
    def _process_bids_file(file_path):
        filename = os.path.basename(file_path)
        try:
            parts = filename.split("_")
            study_id = int(parts[0])
            subject_id = parts[1]
            session_id = parts[2]

            header_df, data_df = None, None
            file_type = 0

            if filename.endswith(".trc"):
                file_type = 1
                header_df, data_df = read_trc(file_path)
            elif "externalloads.mot" in filename:
                file_type = 2
                header_df, data_df = load_opensim_storage(file_path)
            elif "processed_emg.sto" in filename:
                file_type = 3
                header_df, data_df = load_opensim_storage(file_path)

            if data_df is not None:
                header_long = None
                if not header_df.empty:
                    header_long = to_header_format_input(
                        header_df, study_id, subject_id, session_id, run_id, file_type
                    ).astype(str)
                value_long = to_long_format_input(
                    data_df, study_id, subject_id, session_id, run_id, file_type
                )
                return (header_long, value_long)
        except Exception as e:
            print(f"  [SKIP] Error processing {filename}: {e}")
            traceback.print_exc()
        return None

    _n_workers = min(8, len(file_paths)) or 1
    with ThreadPoolExecutor(max_workers=_n_workers) as _exec:
        _results = list(_exec.map(_process_bids_file, file_paths))

    for _r in _results:
        if _r is not None:
            _hdr, _val = _r
            if _hdr is not None:
                all_headers_pd.append(_hdr)
            all_values_pd.append(_val)

    if all_values_pd:
        value_df_unified = spark.createDataFrame(
            pd.concat(all_values_pd, ignore_index=True, sort=False)
        )

        if spark.catalog.tableExists(value_table):
            target_value_columns = spark.table(value_table).columns
            for col_name in target_value_columns:
                if col_name not in value_df_unified.columns:
                    value_df_unified = value_df_unified.withColumn(col_name, lit(None))
            value_df_unified = value_df_unified.select(*target_value_columns)
            value_df_unified.writeTo(value_table).append()
        else:
            value_df_unified.writeTo(value_table).partitionedBy(
                "study_id", "subject_id", "session_id"
            ).create()

    if all_headers_pd:
        header_df_unified = spark.createDataFrame(
            pd.concat(all_headers_pd, ignore_index=True, sort=False).astype(str)
        )

        if spark.catalog.tableExists(header_table):
            target_header_columns = spark.table(header_table).columns
            for col_name in target_header_columns:
                if col_name not in header_df_unified.columns:
                    header_df_unified = header_df_unified.withColumn(
                        col_name, lit(None)
                    )
            header_df_unified = header_df_unified.select(*target_header_columns)
            header_df_unified.writeTo(header_table).append()
        else:
            header_df_unified.writeTo(header_table).partitionedBy(
                "study_id", "subject_id", "session_id"
            ).create()

    if created_spark:
        spark.stop()
