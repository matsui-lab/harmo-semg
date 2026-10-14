import sys
from types import SimpleNamespace
import numpy as np
import pytest
from harmo.config.c3d_records import inspect_records


def source(tmp_path, monkeypatch):
    path = tmp_path / "source.c3d"
    path.write_bytes(b"controlled decoder fixture")
    output = tmp_path / "output"
    output.mkdir()
    data = {"data": {"points": np.arange(24, dtype=float).reshape(4, 2, 3),
                     "analogs": np.arange(12, dtype=float).reshape(1, 2, 6)},
            "header": {"points": {"frame_rate": 100}, "analogs": {"frame_rate": 200}},
            "parameters": {"POINT": {"LABELS": {"value": ["P:A", "P:B"]}, "RATE": {"value": [100]}, "UNITS": {"value": ["mm"]}},
                           "ANALOG": {"LABELS": {"value": ["one", "two"]}, "RATE": {"value": [200]}, "UNITS": {"value": ["mV", "V"]}}}}
    monkeypatch.setitem(sys.modules, "ezc3d", SimpleNamespace(c3d=lambda _: data))
    return path, output, data


def test_streams_separate_and_values_preserved(tmp_path, monkeypatch):
    path, output, data = source(tmp_path, monkeypatch)
    report = inspect_records(path, output)
    assert report["status"] == "accepted"
    assert [r["sampling_rate_hz"] for r in report["records"]] == [100, 200]
    assert report["records"][1]["options"]["c3d_data_source"] == "analog-native"
    with np.load(output / "record-0000/converted.npz") as saved:
        np.testing.assert_array_equal(saved["point_fourth_component"], data["data"]["points"][3])
    with np.load(output / "record-0001/converted.npz") as saved:
        np.testing.assert_array_equal(saved["values"], data["data"]["analogs"][0].T)


@pytest.mark.parametrize("failure", ["duplicate", "rate", "nonfinite"])
def test_bad_stream_held_without_losing_other(tmp_path, monkeypatch, failure):
    path, output, data = source(tmp_path, monkeypatch)
    if failure == "duplicate":
        data["parameters"]["POINT"]["LABELS"]["value"] = ["P:A", "Q:A"]
    elif failure == "rate":
        data["header"]["points"]["frame_rate"] = 200
    else:
        data["data"]["points"][0, 0, 0] = np.inf
    report = inspect_records(path, output)
    assert report["status"] == "partial"
    assert report["records"][0]["stream"] == "ANALOG"
    assert report["held"][0]["stream"] == "POINT"


def test_missing_units_not_invented(tmp_path, monkeypatch):
    path, output, data = source(tmp_path, monkeypatch)
    del data["parameters"]["ANALOG"]["UNITS"]
    result = inspect_records(path, output)
    assert result["records"][1]["unit_status"] == "unresolved"
    assert result["records"][1]["scientific_use"] == "not_evaluated"
