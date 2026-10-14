"""Read-only DuckDB queries over local Iceberg tables."""

from __future__ import annotations

import re
from typing import Any, Dict, List, Optional

from .connection import get_warehouse_path, iceberg_table_path


_IDENTIFIER = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")


def _validated_identifier(value: str, label: str) -> str:
    if not isinstance(value, str) or not _IDENTIFIER.fullmatch(value):
        raise ValueError("Unsafe {}: {}".format(label, value))
    return value


def _validated_table(value: str) -> str:
    if not isinstance(value, str) or not value:
        raise ValueError("Unsafe table name: {}".format(value))
    for part in value.split("."):
        _validated_identifier(part, "table name")
    return value


def _connection():
    try:
        import duckdb
    except ImportError as exc:  # pragma: no cover
        raise RuntimeError("DuckDB is required for querying the warehouse") from exc
    con = duckdb.connect()
    con.execute("INSTALL iceberg")
    con.execute("LOAD iceberg")
    return con


def _scan(table: str) -> str:
    _validated_table(table)
    path = iceberg_table_path(table, get_warehouse_path())
    escaped = path.replace("'", "''")
    return "iceberg_scan('{}', allow_moved_paths = true)".format(escaped)


def _where(filters: Optional[Dict[str, Any]]) -> tuple:
    filters = filters or {}
    clauses, values = [], []
    for key, value in filters.items():
        _validated_identifier(key, "filter column")
        clauses.append('"{}" = ?'.format(key))
        values.append(value)
    return ((" WHERE " + " AND ".join(clauses)) if clauses else "", values)


def get_distinct_values(table: str, column: str) -> List[Any]:
    return get_filtered_distinct_values(table, column, {})


def get_filtered_distinct_values(
    table: str, column: str, filters: Dict[str, Any]
) -> List[Any]:
    _validated_identifier(column, "column name")
    where, values = _where(filters)
    sql = 'SELECT DISTINCT "{0}" FROM {1}{2} ORDER BY 1'.format(
        column, _scan(table), where
    )
    connection = _connection()
    try:
        return [row[0] for row in connection.execute(sql, values).fetchall()]
    finally:
        connection.close()


def get_time_series_data(
    table: str,
    filters: Optional[Dict[str, Any]] = None,
    channels: Optional[List[Any]] = None,
    channel_column: str = "channel",
):
    """Return filtered long-form values for the visualization/API layer."""
    _validated_identifier(channel_column, "channel column")
    where, values = _where(filters)
    if channels:
        placeholders = ", ".join("?" for _ in channels)
        condition = '"{}" IN ({})'.format(channel_column, placeholders)
        where = where + (" AND " if where else " WHERE ") + condition
        values.extend(channels)
    projection = "*"
    if channel_column != "channel":
        projection += ', "{}" AS "channel"'.format(channel_column)
    sql = "SELECT {} FROM {}{} ORDER BY time".format(projection, _scan(table), where)
    connection = _connection()
    try:
        return connection.execute(sql, values).fetchdf()
    finally:
        connection.close()


def get_summary_data(table: str, filters=None):
    where, values = _where(filters)
    sql = "SELECT * FROM {}{}".format(_scan(table), where)
    connection = _connection()
    try:
        return connection.execute(sql, values).fetchdf()
    finally:
        connection.close()


def get_filtered_osim_names(name_to_get: str, filters=None):
    columns = {
        "method": ("method_name", "method_id"),
        "output": ("output_name", "output_id"),
    }
    if name_to_get not in columns:
        raise ValueError("name_to_get must be method or output")
    label, identifier = columns[name_to_get]
    where, values = _where(filters)
    sql = 'SELECT DISTINCT "{}", "{}" FROM {}{} ORDER BY 1'.format(
        label, identifier, _scan("osim_value"), where
    )
    connection = _connection()
    try:
        return connection.execute(sql, values).fetchall()
    finally:
        connection.close()


def get_all_osim_value_data_for_run(run_id: str, session_id: str):
    return get_summary_data("osim_value", {"run_id": run_id, "session_id": session_id})
