import hashlib

import pandas as pd
import pytest

from harmo.analysis.data_prep import _external_static_markers
from harmo.analysis.db_to_osim import _prepare_trc_data
from harmo.analysis.osim_config import resolve_workflow


def _trc(path):
    path.write_text(
        "PathFileType\t4\t(X/Y/Z)\tstatic.trc\n"
        "DataRate\tCameraRate\tNumFrames\tNumMarkers\tUnits\n"
        "100\t100\t3\t1\tmm\n"
        "Frame#\tTime\tRASIS\t\t\n"
        "\t\tX1\tY1\tZ1\n"
        "1\t0.00\t1\t2\t3\n"
        "2\t0.01\t1\t2\t3\n"
        "3\t0.02\t1\t2\t3\n",
        encoding="utf-8",
    )


def _source(path):
    return {
        "path": "static.trc",
        "absolute_path": str(path),
        "observed_sha256": hashlib.sha256(path.read_bytes()).hexdigest(),
        "coordinate_unit": "mm",
        "coordinate_system_id": "data_31_rule",
        "sampling_rate_hz": 100,
    }


def test_external_trc_rechecks_checksum_header_unit_and_timebase(tmp_path):
    path = tmp_path / "static.trc"
    _trc(path)

    markers, unit, rate, coordinate_system = _external_static_markers(_source(path))

    assert list(markers.columns) == ["time", "RASIS_X", "RASIS_Y", "RASIS_Z"]
    assert unit == "mm"
    assert rate == 100
    assert coordinate_system == "data_31_rule"


def test_external_trc_rejects_manifest_timebase_mismatch(tmp_path):
    path = tmp_path / "static.trc"
    _trc(path)
    source = _source(path)
    source["sampling_rate_hz"] = 200

    with pytest.raises(ValueError, match="sampling rate mismatch"):
        _external_static_markers(source)


def test_trc_preparation_converts_declared_centimetres_to_metres():
    raw = pd.DataFrame(
        {
            "time": [0.0, 0.01],
            "marker_x": [100.0, 200.0],
            "marker_y": [50.0, 100.0],
            "marker_z": [25.0, 50.0],
        }
    )

    converted, excluded = _prepare_trc_data(raw, "cm")

    assert excluded == []
    assert converted["marker_X"].tolist() == [1.0, 2.0]


def test_rra_workflow_is_explicit_and_depends_on_ik():
    assert resolve_workflow({})["RRA"] is False
    resolved = resolve_workflow({"workflow": {"IK": False, "RRA": True}})
    assert resolved["IK"] is False
    assert resolved["RRA"] is False
