"""STO / MOT file reader (OpenSim storage format).

Also contains the shared :func:`read_opensim_sto_trc` dispatcher that
handles both ``.sto`` / ``.mot`` and ``.trc`` files.

STO/MOT files consist of key=value header lines terminated by
``endheader``, followed by whitespace-delimited data rows.

Extracted from ``BIDS_LIKE/ingest_data_spark.py`` (lines 1313-1674).
"""
from __future__ import annotations

from typing import Any, Dict, List, Tuple

import pandas as pd


def _deduplicate_columns(df: pd.DataFrame) -> pd.DataFrame:
    seen: Dict[str, int] = {}
    new_cols: List[str] = []
    for col_name in list(df.columns):
        base = str(col_name)
        cnt = seen.get(base, 0)
        if cnt == 0:
            new_cols.append(base)
        else:
            new_cols.append("{}_{}".format(base, cnt))
        seen[base] = cnt + 1
    if len(new_cols) == len(set(new_cols)):
        df.columns = new_cols
    return df


def _convert_header_columns(header_df: pd.DataFrame) -> pd.DataFrame:
    """Convert wholly numeric header columns without deprecated coercion."""
    result = header_df.copy()
    for column in result.columns:
        converted = pd.to_numeric(result[column], errors="coerce")
        if converted.notna().sum() == result[column].notna().sum():
            result[column] = converted
    return result


# ---------------------------------------------------------------------------
# Core parser
# ---------------------------------------------------------------------------

def read_opensim_sto_trc(path: str, round_trip: bool = False) -> Tuple[pd.DataFrame, pd.DataFrame]:
    """Read an OpenSim ``.sto``, ``.mot``, or ``.trc`` file.

    TRC fixed 5-line header structure::

        Line 0: PathFileType\\t4\\t(X/Y/Z)\\t<filename>
        Line 1: DataRate\\tCameraRate\\tNumFrames\\tNumMarkers\\tUnits\\t...
        Line 2: 100.00\\t100.00\\t113\\t39\\tmm\\t...
        Line 3: Frame#\\tTime\\tMarker1\\t\\t\\tMarker2\\t\\t\\t...
        Line 4: \\t\\tX1\\tY1\\tZ1\\tX2\\tY2\\tZ2\\t...
        Line 5: data rows

    MOT/STO format: key=value pairs until ``endheader``, then data rows.

    Parameters
    ----------
    path : str
        File path.

    Returns
    -------
    (header_df, data_df)
        ``header_df`` is a single-row DataFrame of header metadata.
        ``data_df`` contains the numeric time-series data.
    """
    header_dict: Dict[str, str] = {}

    with open(path, "r", encoding="utf-8") as f:
        lines = f.readlines()

    # --- TRC detection ---
    if lines and lines[0].strip().startswith("PathFileType"):
        header_dict["FileType"] = "TRC"

        # Line 1 (keys) + Line 2 (values)
        if len(lines) >= 3:
            keys = lines[1].strip().split("\t")
            values = lines[2].strip().split("\t")
            for k, v in zip(keys, values):
                k = k.strip()
                if k:
                    header_dict[k] = v.strip()

        # Build column names from marker names on Line 3
        col_names: List[str] = ["frame_num", "time"]
        if len(lines) >= 4:
            marker_names_raw = lines[3].rstrip("\n\r").split("\t")
            # Several valid exporters label the second column ``Times``
            # rather than ``Time``.  Treat both as the time field instead of
            # accidentally manufacturing a three-axis marker named ``Times``.
            # The latter shifts every marker column and, for files with a
            # complete header, produces a hard length mismatch.
            markers = [
                m.strip()
                for m in marker_names_raw
                if m.strip()
                and m.strip().lower() not in {"frame#", "time", "times"}
            ]
            for m in markers:
                col_names.extend([f"{m}_X", f"{m}_Y", f"{m}_Z"])

        # Data starts at line 5, skip blanks
        start_idx = 5
        while start_idx < len(lines) and not lines[start_idx].strip():
            start_idx += 1

        data_df = pd.read_csv(
            path,
            sep="\t",
            skiprows=start_idx,
            header=None,
            **({"engine": "c", "float_precision": "round_trip"} if round_trip else {"engine": "python"}),
        )
        if data_df.shape[1] < len(col_names):
            raise ValueError(
                "TRC data has {} columns but its marker header declares {}".format(
                    data_df.shape[1], len(col_names)
                )
            )
        data_df = data_df.iloc[:, : len(col_names)]
        data_df.columns = col_names
        data_df = _deduplicate_columns(data_df)

    else:
        # --- MOT / STO ---
        start_idx = 0
        for i, line in enumerate(lines):
            clean_line = line.strip()

            if clean_line == "endheader":
                start_idx = i + 1
                break

            if "=" in clean_line:
                key, value = map(str.strip, clean_line.split("=", 1))
                header_dict[key] = value
            elif i == 0 and clean_line:
                header_dict["FileType"] = clean_line

        data_df = pd.read_csv(
            path, sep=r"\s+", skiprows=start_idx,
            **({"engine": "c", "float_precision": "round_trip"} if round_trip else {"engine": "python"})
        )
        data_df.columns = data_df.columns.str.strip().str.lower()
        data_df = _deduplicate_columns(data_df)

        if "frame#" in data_df.columns:
            data_df = data_df.rename(columns={"frame#": "frame_num"})

    # Header as a single-row DataFrame
    header_df = pd.DataFrame([header_dict])
    header_df = _convert_header_columns(header_df)

    if "time" not in data_df.columns:
        if "frame_num" in data_df.columns and "DataRate" in header_dict:
            rate = float(header_dict["DataRate"])
            data_df["time"] = data_df["frame_num"] / rate
        else:
            print(
                f"\u8b66\u544a: 'time' \u5217\u304c\u898b\u3064\u304b\u3089\u305a\u3001\u751f\u6210\u3082\u3067\u304d\u307e\u305b\u3093\u3067\u3057\u305f in {path}"
            )

    return header_df, data_df


# ---------------------------------------------------------------------------
# Convenience wrappers
# ---------------------------------------------------------------------------

def read_sto_file(path: str) -> Tuple[pd.DataFrame, pd.DataFrame]:
    """Read an OpenSim ``.sto`` or ``.mot`` file.

    This is a thin alias for :func:`read_opensim_sto_trc` applied to
    storage-format files.
    """
    return read_opensim_sto_trc(path)


# ---------------------------------------------------------------------------
# Spark helper
# ---------------------------------------------------------------------------

def load_opensim_to_spark(
    spark: Any, file_path_list: List[str], round_trip: bool = False
) -> Any:
    """Read multiple OpenSim files into a single Spark DataFrame.

    Parameters
    ----------
    spark : SparkSession
    file_path_list : list[str]

    Returns
    -------
    pyspark.sql.DataFrame or None
    """
    from harmo.ingest.readers import _read_files_parallel

    def _read_opensim_data_only(file_path: str) -> pd.DataFrame:
        _, data_df = read_opensim_sto_trc(file_path, round_trip=round_trip)
        return data_df

    all_data_dfs = _read_files_parallel(
        file_path_list, _read_opensim_data_only, format_label="OpenSim"
    )

    if not all_data_dfs:
        return None

    from harmo.ingest.readers import _batched_pd_to_spark

    return _batched_pd_to_spark(spark, all_data_dfs)
