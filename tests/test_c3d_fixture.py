from pathlib import Path
import sys

import numpy as np
import pandas as pd
import pytest

pytest.importorskip("ezc3d")

from harmo.ingest.readers.c3d import _read_c3d_with_worker, read_c3d_file


FIXTURE = (
    Path(__file__).parents[1]
    / "studies"
    / "study_1"
    / "notebook"
    / "Ref01_sss_le_shoes05.c3d"
)


@pytest.mark.parametrize(
    ("data_source", "expected_rows", "expected_columns", "expected_interval"),
    [
        ("point", 343, 592, 1 / 200),
        ("analog", 1715, 49, 1 / 1000),
    ],
)
def test_repository_c3d_fixture_is_readable(
    data_source, expected_rows, expected_columns, expected_interval
):
    frame = read_c3d_file(str(FIXTURE), data_source=data_source)

    assert frame.shape == (expected_rows, expected_columns)
    assert "time" in frame.columns
    assert not frame.columns.duplicated().any()
    assert np.isfinite(frame["time"]).all()
    assert frame["time"].is_monotonic_increasing
    assert frame["time"].iloc[0] == pytest.approx(0.0)
    assert frame["time"].iloc[-1] > frame["time"].iloc[0]
    assert frame["time"].diff().dropna().to_numpy() == pytest.approx(expected_interval)
    assert len(frame.columns) > 1


def test_repository_c3d_fixture_cross_process_parquet_is_lossless():
    pytest.importorskip("pyarrow")
    direct = read_c3d_file(str(FIXTURE), data_source="point")
    transferred = _read_c3d_with_worker(
        sys.executable,
        str(FIXTURE),
        "point",
        None,
        None,
        None,
    )

    pd.testing.assert_frame_equal(transferred, direct, check_exact=True)
