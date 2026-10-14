"""Warehouse connection and query helpers.

The module deliberately keeps Spark and DuckDB imports lazy so that the core
harmo contracts remain usable in lightweight validation environments.
"""

from .connection import get_spark_session, get_warehouse_path, iceberg_table_path

__all__ = ["get_spark_session", "get_warehouse_path", "iceberg_table_path"]
