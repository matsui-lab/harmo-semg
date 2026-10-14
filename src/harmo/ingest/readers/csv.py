"""CSV / TSV file reader.

Provides a minimal CSV/TSV reader wrapping :func:`pandas.read_csv` and a
Spark-based horizontal-explode helper for wide-format files.

Extracted from ``BIDS_LIKE/ingest_data_spark.py``.
"""
from __future__ import annotations
import re
from typing import Any, Dict, List, Optional
import pandas as pd


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def read_csv_file(
    path: str,
    sep: str = ",",
    comment: str = "#",
    header: bool = True,
    float_precision: Optional[str] = None,
) -> pd.DataFrame:
    """Read a CSV or TSV file into a :class:`pandas.DataFrame`.
    Parameters
    ----------
    path : str
        File path.
    sep : str
        Column separator.
    comment : str
        Comment character (lines starting with this are skipped).
    header : bool
        Whether the first row is a header.
    Returns
    -------
    pd.DataFrame
    """
    header_arg = 0 if header else None
    if float_precision not in (None, "round_trip"):
        raise ValueError("float_precision must be None or round_trip")
    # Opt-in only: existing studies retain their original parsing behavior.
    if float_precision == "round_trip":
        return pd.read_csv(path, sep=sep, comment=comment, header=header_arg,
                           engine="c", float_precision="round_trip")
    df = pd.read_csv(path, sep=sep, comment=comment, header=header_arg, engine="python",)
    return df


def read_multi_rate_csv(
    path: str,
    multi_rate_info: List[Dict[str, Any]],
    sep: str = ",",
    comment: str = "#",
    header: bool = True,
    default_sampling_rate: Optional[float] = None,
) -> pd.DataFrame:
    """異なるサンプリングレートを持つ列が混在したCSVファイルを読み込みます。

        この関数は以下の処理を行います：
        1. CSVファイルを読み込む。
        2. 'multi_rate_info' の条件（match_type と pattern）に基づいて、列をグループ化する。
        3. 各グループの 'sampling_rate' に基づいて、それぞれの 'time'（時間軸）列を動的に生成する。
        4. 各グループをロング形式（time, channel, value）に Melt（縦型変換）する。
        5. すべてのグループを縦結合した、1つのロング形式の DataFrame を返す。

        Parameters
        ----------
        path : str
            ファイルパス。
        multi_rate_info : list[dict]
            各辞書（dict）には以下のキーを含める必要があります：
            - 'sampling_rate': float（サンプリング周波数）
            - 'pattern': str（列名を特定するための文字列または正規表現）
            - 'match_type': 'contains' | 'startswith' | 'endswith' | 'regex'
                            （部分一致、前方一致、後方一致、正規表現）
        sep, comment, header :
            pandas.read_csv の標準オプション。
        default_sampling_rate : float, optional
            どのルールにもマッチしなかった列に適用するデフォルトのサンプリングレート。
    """
    import numpy as np

    df_raw = read_csv_file(path, sep=sep, comment=comment, header=header)
    all_cols = [c for c in df_raw.columns]
    
    melted_groups = []

    # Keep track of which columns have been assigned to a group
    assigned_cols = set()

    for info in multi_rate_info:
        fs = float(info["sampling_rate"])
        pattern = info["pattern"]
        m_type = info.get("match_type", "contains")

        group_cols = []
        for col_name in all_cols:
            if col_name in assigned_cols:
                continue
            
            matched = False
            if m_type == "contains" and pattern in col_name:
                matched = True
            elif m_type == "startswith" and col_name.startswith(pattern):
                matched = True
            elif m_type == "endswith" and col_name.endswith(pattern):
                matched = True
            elif m_type == "regex" and re.search(pattern, col_name):
                matched = True
            
            if matched:
                group_cols.append(col_name)
                assigned_cols.add(col_name)

        if group_cols:
            # Extract data and generate time
            sub_df = df_raw[group_cols].copy()
            # Remove rows where all columns in this group are NaN (optional but cleaner)
            sub_df = sub_df.dropna(how="all")
            
            # Generate time vector: 0, 1/fs, 2/fs, ...
            time_vector = np.arange(len(sub_df)) / fs
            sub_df.insert(0, "time", time_vector)
            
            # Melt to long format
            melted = sub_df.melt(id_vars=["time"], var_name="channel", value_name="value")
            melted_groups.append(melted)

    # Handle remaining columns if default_sampling_rate is provided
    remaining_cols = [c for c in all_cols if c not in assigned_cols]
    if remaining_cols and default_sampling_rate:
        fs = float(default_sampling_rate)
        sub_df = df_raw[remaining_cols].copy().dropna(how="all")
        time_vector = np.arange(len(sub_df)) / fs
        sub_df.insert(0, "time", time_vector)
        melted = sub_df.melt(id_vars=["time"], var_name="channel", value_name="value")
        melted_groups.append(melted)
    elif remaining_cols:
        print(f"  [警告] グループに割り当てられなかった列があります: {remaining_cols}")

    if not melted_groups:
        return pd.DataFrame(columns=["time", "channel", "value"])

    return pd.concat(melted_groups, ignore_index=True)


def apply_horizontal_explode(
    spark: Any,
    file_path_list: List[str],
    reader_options: Dict[str, Any],
) -> Any:
    """Read wide-format (horizontal) data and explode into long format.

    Uses PySpark ``posexplode`` so that element order is preserved.

    Parameters
    ----------
    spark : SparkSession
    file_path_list : list[str]
    reader_options : dict
        ``sep`` (default ``','``).

    Returns
    -------
    pyspark.sql.DataFrame
        Single ``time`` column of ``DoubleType``.
    """
    # Lazy PySpark imports
    from pyspark.sql.functions import col, input_file_name, posexplode, split
    from pyspark.sql.types import DoubleType  # type: ignore[import-untyped]
    df = spark.read.text(file_path_list).withColumn(
        "source_file_path", input_file_name()
    )

    sep = reader_options.get("sep", ",")
    df = df.select(
        "source_file_path",
        posexplode(split(col("value"), sep)).alias("idx", "raw_value"),
    )
    df = (df.filter(col("raw_value") != "").withColumn("time", col("raw_value").cast(DoubleType())))

    return df.select("time", "source_file_path")
