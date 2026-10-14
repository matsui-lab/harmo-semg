import numpy as np
from scipy.io import savemat, loadmat
import pytest

from harmo.config.mat_records import inspect_records


def fixture(path, conflict=False, square=False):
    records = np.empty((1, 2), dtype=[("EMG", object), ("other", object)] + ([("EMGFreq", object)] if conflict else []))
    for index in range(2):
        records[0, index]["EMG"] = np.arange(4 if square else 6, dtype=float).reshape(2, 2 if square else 3) + index
        records[0, index]["other"] = np.ones((3, 2))
        if conflict:
            records[0, index]["EMGFreq"] = np.array([[2000]])
    savemat(path, {"s": {"Data": records, "EmgVarName": np.array(["A ", "B "]), "EMGFreq": 1000.0}})


def test_every_record_kept_and_other_fields_held(tmp_path):
    path = tmp_path / "source.mat"
    fixture(path)
    out = tmp_path / "out"
    out.mkdir()
    result = inspect_records(path, out)
    assert result["status"] == "partial"
    assert [r["data_key"] for r in result["records"]] == ["s.Data.0.EMG", "s.Data.1.EMG"]
    assert len(result["held"]) == 2
    with np.load(out / "record-0001/converted.npz") as converted:
        np.testing.assert_array_equal(converted["values"], np.arange(6).reshape(2, 3).T + 1)
        np.testing.assert_array_equal(converted["time"], np.arange(3)/1000)


def test_partial_local_declaration_blocks_inheritance(tmp_path):
    path = tmp_path / "source.mat"
    fixture(path, conflict=True)
    result = inspect_records(path, tmp_path)
    assert not result["records"]
    assert any(r["reason"] == "incomplete_local_metadata" for r in result["held"])


def test_square_array_not_guessed(tmp_path):
    path = tmp_path / "source.mat"
    fixture(path, square=True)
    result = inspect_records(path, tmp_path)
    assert not result["records"]
    assert any(r["reason"] == "ambiguous_array_orientation" for r in result["held"])


@pytest.mark.parametrize("field,value,reason", [
    ("EMGFreq", np.array([[np.nan]]), "invalid_sampling_rate"),
    ("EmgVarName", np.array(["A", "A"]), "invalid_labels"),
])
def test_bad_metadata_does_not_generate_records(tmp_path, field, value, reason):
    path = tmp_path / "source.mat"
    fixture(path)
    data = loadmat(path)["s"]
    data[0, 0][field] = value
    savemat(path, {"s": data})
    result = inspect_records(path, tmp_path)
    assert not result["records"]
    assert any(r["reason"] == reason for r in result["held"])


def test_nan_signal_is_retained(tmp_path):
    path = tmp_path / "source.mat"
    fixture(path)
    data = loadmat(path)["s"]
    data[0, 0]["Data"][0, 0]["EMG"][:, 1] = np.nan
    savemat(path, {"s": data})
    result = inspect_records(path, tmp_path)
    assert result["records"][0]["missing_cells"] == 2
    with np.load(tmp_path / "record-0000/converted.npz") as output:
        assert np.isnan(output["values"][1]).all()


def test_modalities_keep_own_rates_and_angles_held(tmp_path):
    path = tmp_path / "source.mat"
    trials = np.empty((1, 2), dtype=[(name, object) for name in ("EMG", "Marker", "Grf", "Ang")])
    for item in trials.flat:
        for name in trials.dtype.names:
            item[name] = np.arange(10).reshape(2, 5)
    root = {"Data": trials, "EmgVarName": np.array(["A", "B"]), "EMGFreq": 1000,
            "MarkerVarName": np.array(["M_X", "M_Y"]), "KinFreq": 200,
            "GrfVarName": np.array(["F_X", "F_Y"]), "GrfFreq": 800}
    savemat(path, {"s": root})
    result = inspect_records(path, tmp_path)
    assert len(result["records"]) == 6
    assert {(r["signal_type"], r["sampling_rate_hz"]) for r in result["records"]} == {("EMG", 1000), ("Marker", 200), ("Grf", 800)}
    assert all("axis_semantics" in row["reason"] for row in result["held"])
    assert len(result["held"]) == 2
