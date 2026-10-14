from __future__ import annotations

import warnings

from harmo.ingest.readers.sto import read_opensim_sto_trc, read_sto_file


def test_sto_reader_preserves_text_and_converts_numeric_headers_without_warning(tmp_path):
    path = tmp_path / "trial.mot"
    path.write_text(
        "Coordinates\n"
        "nRows=2\n"
        "nColumns=2\n"
        "units=unknown\n"
        "endheader\n"
        "time value\n"
        "0.0 1.0\n"
        "0.1 2.0\n",
        encoding="utf-8",
    )
    with warnings.catch_warnings():
        warnings.simplefilter("error", FutureWarning)
        header, data = read_sto_file(str(path))
    assert header.loc[0, "FileType"] == "Coordinates"
    assert header.loc[0, "nRows"] == 2
    assert header.loc[0, "units"] == "unknown"
    assert data["time"].tolist() == [0.0, 0.1]


def test_trc_reader_accepts_plural_times_exporter_header(tmp_path):
    path = tmp_path / "trial.trc"
    path.write_text(
        "PathFileType\t4\t(X/Y/Z)\ttrial.trc\n"
        "DataRate\tCameraRate\tNumFrames\tNumMarkers\tUnits\tOrigDataRate\tOrigDataStartFrame\tOrigNumFrames\n"
        "100\t100\t2\t2\tm\t100\t0\t1\n"
        "Frame#\tTimes\t\tLASIS\t\t\tRASIS\t\t\n"
        "\t\tX1\tY1\tZ1\tX2\tY2\tZ2\n"
        "0\t0.00\t1\t2\t3\t4\t5\t6\n"
        "1\t0.01\t2\t3\t4\t5\t6\t7\n",
        encoding="utf-8",
    )

    header, data = read_opensim_sto_trc(str(path))

    assert header.loc[0, "NumMarkers"] == 2
    assert data.columns.tolist() == [
        "frame_num",
        "time",
        "LASIS_X",
        "LASIS_Y",
        "LASIS_Z",
        "RASIS_X",
        "RASIS_Y",
        "RASIS_Z",
    ]
    assert data["time"].tolist() == [0.0, 0.01]
