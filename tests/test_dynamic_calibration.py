from __future__ import annotations

import hashlib
import json
import subprocess
import sys
from pathlib import Path

import pytest
import yaml

from harmo.dynamic_calibration import (
    run_dynamic_calibration_benchmark,
    validate_public_source_bundle,
)


ROOT = Path(__file__).resolve().parents[1]


def _write_trc(path: Path, lengths, marker_b_y=0.0):
    rows = []
    for index, length in enumerate(lengths):
        rows.append(
            "{}\t{:.2f}\t0\t0\t0\t{}\t{}\t0".format(
                index, index / 100.0, length, marker_b_y
            )
        )
    path.write_text(
        "PathFileType\t4\t(X/Y/Z)\t{}\n".format(path.name)
        + "DataRate\tCameraRate\tNumFrames\tNumMarkers\tUnits\tOrigDataRate\tOrigDataStartFrame\tOrigNumFrames\n"
        + "100\t100\t{}\t2\tm\t100\t0\t{}\n".format(
            len(rows), len(rows) - 1
        )
        + "Frame#\tTimes\t\tA\t\t\tB\t\t\n"
        + "\t\tX1\tY1\tZ1\tX2\tY2\tZ2\n"
        + "\n"
        + "\n".join(rows)
        + "\n",
        encoding="utf-8",
    )


def _asset(path: Path, role: str):
    content = path.read_bytes()
    return {
        "role": role,
        "local_name": path.name,
        "download_url": "https://example.org/{}".format(path.name),
        "size_bytes": len(content),
        "sha256": hashlib.sha256(content).hexdigest(),
    }


def _fixture(tmp_path):
    static = tmp_path / "static.trc"
    calibration_a = tmp_path / "dynamic-a.trc"
    calibration_b = tmp_path / "dynamic-b.trc"
    heldout = tmp_path / "heldout.trc"
    _write_trc(static, [1.0, 1.0, 1.0, 1.0])
    _write_trc(calibration_a, [0.99, 1.00, 1.01, 4.0])
    _write_trc(calibration_b, [1.00, 1.01, 1.02, 0.1])
    _write_trc(heldout, [1.00, 1.01, 0.99, 8.0])
    manifest = tmp_path / "manifest.yaml"
    manifest.write_text(
        yaml.safe_dump(
            {
                "schema_version": "1.0.0",
                "source_record_doi": "10.example/test",
                "license": "CC0",
                "assets": [
                    _asset(static, "static"),
                    _asset(calibration_a, "dynamic_a"),
                    _asset(calibration_b, "dynamic_b"),
                    _asset(heldout, "heldout"),
                ],
            },
            sort_keys=False,
        ),
        encoding="utf-8",
    )
    protocol = tmp_path / "protocol.yaml"
    protocol.write_text(
        yaml.safe_dump(
            {
                "schema_version": "1.0.0",
                "pilot": {
                    "subject_id": "test",
                    "static_reference_role": "static",
                    "calibration_roles": ["dynamic_a", "dynamic_b"],
                    "heldout_dynamic_role": "heldout",
                },
                "estimation": {
                    "outlier_mad": 3.5,
                    "bootstrap_iterations": 100,
                    "bootstrap_seed": 7,
                },
                "thresholds": {
                    "measurement_coverage": 1.0,
                    "median_absolute_relative_error": 0.02,
                    "p95_absolute_relative_error": 0.02,
                    "heldout_p95_absolute_relative_error": 0.02,
                },
                "measurements": [
                    {
                        "id": "a-b",
                        "body": "test",
                        "measurement_class": "rigid-body",
                        "marker_a": "A",
                        "marker_b": "B",
                    }
                ],
            },
            sort_keys=False,
        ),
        encoding="utf-8",
    )
    return protocol, manifest


def test_dynamic_calibration_is_static_hidden_equal_trial_weighted_and_robust(tmp_path):
    protocol, manifest = _fixture(tmp_path)

    result = run_dynamic_calibration_benchmark(protocol, manifest, tmp_path)

    assert result["pass"] is True
    assert result["checks"]["static_hidden_from_estimation"] is True
    assert all(
        "absolute_path" not in asset for asset in result["source_audit"]["assets"]
    )
    row = result["measurements"][0]
    assert row["dynamic_estimate_m"] == pytest.approx(1.005)
    assert row["static_reference_m"] == pytest.approx(1.0)
    assert row["absolute_relative_error"] == pytest.approx(0.005)
    assert row["calibration_trial_count"] == 2
    assert all(item["inlier_count"] == 3 for item in row["calibration_trials"])


def test_dynamic_calibration_rejects_static_trial_leakage(tmp_path):
    protocol, manifest = _fixture(tmp_path)
    value = yaml.safe_load(protocol.read_text(encoding="utf-8"))
    value["pilot"]["calibration_roles"][0] = "static"
    protocol.write_text(yaml.safe_dump(value), encoding="utf-8")

    with pytest.raises(ValueError, match="cannot enter calibration"):
        run_dynamic_calibration_benchmark(protocol, manifest, tmp_path)


def test_public_source_audit_fails_closed_on_checksum_change(tmp_path):
    _, manifest = _fixture(tmp_path)
    audit = validate_public_source_bundle(manifest, tmp_path)
    assert audit["pass"] is True

    (tmp_path / "dynamic-a.trc").write_text("changed", encoding="utf-8")
    changed = validate_public_source_bundle(manifest, tmp_path)
    assert changed["pass"] is False
    assert changed["checks"]["all_assets_verified"] is False


def test_dynamic_calibration_cli_writes_machine_readable_outputs(tmp_path):
    protocol, manifest = _fixture(tmp_path)
    output = tmp_path / "result.json"

    completed = subprocess.run(
        [
            sys.executable,
            str(ROOT / "scripts/paper/run_validation.py"),
            "--root",
            str(ROOT),
            "opensim-dynamic-calibration",
            "--protocol",
            str(protocol),
            "--source-manifest",
            str(manifest),
            "--data-root",
            str(tmp_path),
            "--output",
            str(output),
        ],
        check=False,
        capture_output=True,
        text=True,
    )

    assert completed.returncode == 0, completed.stderr
    result = json.loads(output.read_text(encoding="utf-8"))
    assert result["pass"] is True
    assert result["environment"]["code_commit"]
    assert output.with_suffix(".csv").is_file()
