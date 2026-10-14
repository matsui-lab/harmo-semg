import pytest
import numpy as np
from harmo.config.motion_tables import inspect_records


def motion(tmp_path, unit="s", extra="", values="0 0.0082988271627990225\n1 nan\n"):
    path = tmp_path / "a.mot"
    path.write_text("Coordinates\nnRows=2\nnColumns=2\ntime_units="+unit+"\n"+extra+"endheader\ntime angle\n"+values)
    return path


def test_mot_exact_nan_and_time(tmp_path):
    result = inspect_records(motion(tmp_path), tmp_path)
    assert result["records"][0]["time_unit"] == "s"
    with np.load(tmp_path / "record-0000/converted.npz") as saved:
        assert saved["values"][0, 0] == float("0.0082988271627990225")
        assert np.isnan(saved["values"][1, 0])


@pytest.mark.parametrize("unit", ["", "%", "normalized"])
def test_phase_not_seconds(tmp_path, unit):
    with pytest.raises(ValueError, match="explicit_time_unit_required"):
        inspect_records(motion(tmp_path, unit), tmp_path)


def test_conflicting_units(tmp_path):
    with pytest.raises(ValueError, match="time_unit_conflict"):
        inspect_records(motion(tmp_path, extra="timeunit=ms\n"), tmp_path)


def test_decreasing_time(tmp_path):
    with pytest.raises(ValueError, match="invalid_time_axis"):
        inspect_records(motion(tmp_path, values="1 2\n0 3\n"), tmp_path)


def trc(tmp_path, last="0.01", extra=""):
    path = tmp_path / "a.trc"
    path.write_text("PathFileType\t4\t(X/Y/Z)\ta.trc\nDataRate\tNumFrames\tNumMarkers\tUnits\n100\t2\t1\tmm\nFrame#\tTime\tA\t\t\n\t\tX1\tY1\tZ1\n\n1\t0\t1\t2\t3\n2\t"+last+"\t4\t5\t6"+extra+"\n")
    return path


def test_trc_and_frame_retention(tmp_path):
    result = inspect_records(trc(tmp_path), tmp_path)
    assert result["records"][0]["channels"] == ["A_X", "A_Y", "A_Z"]
    with np.load(tmp_path / "record-0000/converted.npz") as saved:
        np.testing.assert_array_equal(saved["frame_num"], [1, 2])


def test_trc_rate_conflict(tmp_path):
    with pytest.raises(ValueError, match="time_rate_conflict"):
        inspect_records(trc(tmp_path, last="0.02"), tmp_path)


def test_extra_values_not_silently_discarded(tmp_path):
    with pytest.raises(ValueError, match="unexpected_extra_values"):
        inspect_records(trc(tmp_path, extra="\t99"), tmp_path)
