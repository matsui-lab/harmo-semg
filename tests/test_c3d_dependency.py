import json
import subprocess
from pathlib import Path
from unittest.mock import patch

import pandas as pd
import pytest

from harmo.ingest.readers.c3d import _parse_c3d_worker_result, read_c3d_file


def _missing_ezc3d():
    return ModuleNotFoundError("No module named 'ezc3d'", name="ezc3d")


def test_c3d_dependency_error_is_actionable(monkeypatch):
    monkeypatch.delenv("HARMO_C3D_PYTHON", raising=False)

    with patch(
        "harmo.ingest.readers.c3d._read_c3d_with_ezc3d",
        side_effect=_missing_ezc3d(),
    ):
        with pytest.raises(RuntimeError, match=r"harmo\[c3d\].*HARMO_C3D_PYTHON"):
            read_c3d_file("recording.c3d")


def test_c3d_external_worker_is_configurable(monkeypatch):
    pytest.importorskip("pyarrow")
    monkeypatch.setenv("HARMO_C3D_PYTHON", "/opt/harmo-c3d/bin/python")
    expected = pd.DataFrame({"time": [0.0, 0.001], "EMG": [1.25, 2.5]})

    def fake_worker(command, **_kwargs):
        output_path = Path(command[command.index("--output-parquet") + 1])
        expected.to_parquet(output_path, index=False)
        return subprocess.CompletedProcess(
            args=command,
            returncode=0,
            stdout=json.dumps(
                {
                    "output_parquet": str(output_path),
                    "row_count": len(expected),
                    "column_count": len(expected.columns),
                }
            ),
            stderr="",
        )

    with patch(
        "harmo.ingest.readers.c3d._read_c3d_with_ezc3d",
        side_effect=_missing_ezc3d(),
    ):
        with patch(
            "harmo.ingest.readers.c3d.subprocess.run", side_effect=fake_worker
        ) as run:
            observed = read_c3d_file("recording.c3d", data_source="analog")

    pd.testing.assert_frame_equal(observed, expected)
    command = run.call_args.args[0]
    assert command[0] == "/opt/harmo-c3d/bin/python"
    assert "--as-c3d-worker" in command
    assert command[command.index("--data-source") + 1] == "analog"
    assert "--output-parquet" in command


def test_c3d_worker_rejects_redirected_output(tmp_path):
    expected_path = tmp_path / "expected.parquet"
    redirected_path = tmp_path / "redirected.parquet"
    result = subprocess.CompletedProcess(
        args=[],
        returncode=0,
        stdout=json.dumps({"output_parquet": str(redirected_path)}),
        stderr="",
    )

    with pytest.raises(RuntimeError, match="異なるParquetパス"):
        _parse_c3d_worker_result(result, str(expected_path))


def test_c3d_worker_rejects_inconsistent_shape_metadata(tmp_path):
    pytest.importorskip("pyarrow")
    output_path = tmp_path / "recording.parquet"
    pd.DataFrame({"time": [0.0], "value": [1.0]}).to_parquet(
        output_path, index=False
    )
    result = subprocess.CompletedProcess(
        args=[],
        returncode=0,
        stdout=json.dumps(
            {
                "output_parquet": str(output_path),
                "row_count": 2,
                "column_count": 2,
            }
        ),
        stderr="",
    )

    with pytest.raises(RuntimeError, match="行数メタデータ"):
        _parse_c3d_worker_result(result, str(output_path))
