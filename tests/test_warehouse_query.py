from unittest.mock import Mock, patch

import pandas as pd
import pytest

from harmo.warehouse.query import (
    get_filtered_distinct_values,
    get_summary_data,
    get_time_series_data,
)


def _connection(rows=None, frame=None):
    connection = Mock()
    cursor = Mock()
    cursor.fetchall.return_value = rows or []
    cursor.fetchdf.return_value = frame if frame is not None else pd.DataFrame()
    connection.execute.return_value = cursor
    return connection


def test_time_series_query_matches_visualization_call_contract():
    expected = pd.DataFrame({"time": [0.0], "channel": ["TA"], "value": [1.0]})
    connection = _connection(frame=expected)

    with patch("harmo.warehouse.query._connection", return_value=connection):
        observed = get_time_series_data(
            "raw_data",
            {"study_id": "study-1"},
            ["TA", "GM"],
            "channel",
        )

    pd.testing.assert_frame_equal(observed, expected)
    sql, parameters = connection.execute.call_args.args
    assert '"study_id" = ?' in sql
    assert '"channel" IN (?, ?)' in sql
    assert parameters == ["study-1", "TA", "GM"]
    connection.close.assert_called_once_with()


def test_nonstandard_channel_column_is_aliased_for_ui():
    connection = _connection()
    with patch("harmo.warehouse.query._connection", return_value=connection):
        get_time_series_data("osim_input_value", {}, ["hip_flexion"], "column")
    sql = connection.execute.call_args.args[0]
    assert '"column" AS "channel"' in sql


@pytest.mark.parametrize(
    ("operation", "message"),
    [
        (lambda: get_summary_data("../../secret"), "Unsafe table name"),
        (
            lambda: get_summary_data("raw_data", {"bad-key": "value"}),
            "Unsafe filter column",
        ),
        (
            lambda: get_filtered_distinct_values("raw_data", 'x" FROM secret', {}),
            "Unsafe column name",
        ),
    ],
)
def test_query_identifiers_reject_injection_and_path_traversal(operation, message):
    with pytest.raises(ValueError, match=message):
        operation()
