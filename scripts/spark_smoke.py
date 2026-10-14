#!/usr/bin/env python3
"""CI smoke test for the supported Spark/Iceberg dependency pair."""

from __future__ import annotations

import tempfile
from unittest.mock import patch

import pandas as pd

from harmo.ingest.readers import _batched_pd_to_spark
from harmo.warehouse.connection import get_spark_session
from harmo.warehouse.query import (
    get_filtered_distinct_values,
    get_time_series_data,
)


def main():
    with tempfile.TemporaryDirectory(prefix="harmo-spark-") as warehouse:
        spark = get_spark_session(warehouse, app_name="harmo-ci-smoke")
        try:
            if spark.range(3).count() != 3:
                raise RuntimeError("Spark count smoke test failed")
            if spark.conf.get("spark.sql.catalog.local.type") != "hadoop":
                raise RuntimeError("local Iceberg catalog was not configured")
            frame = _batched_pd_to_spark(
                spark,
                [
                    pd.DataFrame(
                        {
                            "time": [0.0, 0.001],
                            "channel": ["TA", "TA"],
                            "value": [1.0, 2.0],
                            "source_file_path": ["raw-a.csv", "raw-a.csv"],
                        }
                    ),
                    pd.DataFrame(
                        {
                            "time": [0.0],
                            "channel": ["GM"],
                            "value": [3.0],
                            "source_file_path": ["raw-b.csv"],
                        }
                    ),
                ],
            )
            if frame.count() != 3:
                raise RuntimeError("pandas-to-Spark Parquet bridge lost rows")
            spark.sql("CREATE NAMESPACE IF NOT EXISTS local.warehouse")
            frame.writeTo("local.warehouse.harmo_smoke").create()
            observed = spark.table("local.warehouse.harmo_smoke")
            if observed.count() != 3 or set(observed.columns) != set(frame.columns):
                raise RuntimeError("Iceberg write/read smoke test failed")
            frame.unpersist()
        finally:
            spark.stop()
        with patch.dict("os.environ", {"HARMO_WAREHOUSE_PATH": warehouse}):
            channels = get_filtered_distinct_values("harmo_smoke", "channel", {})
            observed = get_time_series_data("harmo_smoke", {}, ["TA"], "channel")
        if channels != ["GM", "TA"] or len(observed) != 2:
            raise RuntimeError("Spark-to-DuckDB Iceberg interoperability failed")


if __name__ == "__main__":
    main()
