from __future__ import annotations

import json
import runpy
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
NAMESPACE = runpy.run_path(
    str(ROOT / "scripts/paper/compare_reproduction_runs.py")
)
compare_adapter_runs = NAMESPACE["compare_adapter_runs"]
compare_physiology_runs = NAMESPACE["compare_physiology_runs"]


def _write(path: Path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(value, sort_keys=True, indent=2) + "\n",
        encoding="utf-8",
    )


def _adapter_run(path: Path, elapsed: float):
    _write(
        path / "adapter_metrics.json",
        {
            "dataset": "fixture",
            "protocol_id": "fixture-protocol",
            "repeat_count": 2,
            "normalized_manifest_sha256": "a" * 64,
            "active_work_seconds": elapsed,
            "core_changed_lines": 0,
            "core_comparison_baseline_commit": "base",
            "core_comparison_implementation_commit": "implementation",
            "pass": True,
        },
    )
    _write(path / "ultra_mocap_trial_manifest.json", {"trials": [1, 2]})
    _write(path / "ultra_mocap_source_manifest.json", {"sources": [1]})
    _write(
        path / "ultra_mocap_adapter_reproducibility.json",
        {"identical": True, "pass": True},
    )
    _write(
        path / "ultra_mocap_adapter_run.json",
        {"code_commit": "execution", "code_dirty": False},
    )


def test_adapter_comparator_ignores_only_execution_time(tmp_path):
    first = tmp_path / "first"
    second = tmp_path / "second"
    _adapter_run(first, 1.5)
    _adapter_run(second, 2.0)

    result = compare_adapter_runs([first, second])

    assert result["pass"] is True
    assert result["independent_process_count"] == 2
    assert result["total_complete_scans"] == 4
    assert result["scientific_metrics_identical_excluding_execution_time"] is True
    assert result["trial_manifest"]["byte_identical"] is True


def test_adapter_comparator_detects_manifest_difference(tmp_path):
    first = tmp_path / "first"
    second = tmp_path / "second"
    _adapter_run(first, 1.5)
    _adapter_run(second, 2.0)
    _write(second / "ultra_mocap_trial_manifest.json", {"trials": [1]})

    result = compare_adapter_runs([first, second])

    assert result["pass"] is False
    assert result["trial_manifest"]["byte_identical"] is False


def _physiology_run(path: Path, commit: str):
    _write(
        path / "physiology_positive_control.json",
        {
            "protocol_sha256": "b" * 64,
            "result_sha256": "c" * 64,
            "reporting_complete": True,
            "scientific_endpoint_pass": True,
            "amplitude_pooled_across_studies": False,
        },
    )
    _write(
        path / "physiology_positive_control_run.json",
        {"environment": {"code_commit": commit, "code_dirty": "false"}},
    )


def test_physiology_comparator_emits_gate_compatible_digest_fields(tmp_path):
    first = tmp_path / "first"
    second = tmp_path / "second"
    correction = tmp_path / "correction.json"
    frozen_commit = "2cf1496b2281d32af442e3643f0617dc21f61e3c"
    _physiology_run(first, frozen_commit)
    _physiology_run(second, frozen_commit)
    _write(correction, {"invalid_result_sha256": "d" * 64})

    result = compare_physiology_runs(
        [first, second], correction, frozen_commit
    )

    assert result["pass"] is True
    assert result["byte_identical"] is True
    assert result["corrected_internal_result_sha256"] == "c" * 64
    assert result["invalid_pre_correction_result_sha256"] == "d" * 64
    assert result["execution_differs_from_frozen_commit"] is False


def test_physiology_comparator_detects_result_difference(tmp_path):
    first = tmp_path / "first"
    second = tmp_path / "second"
    correction = tmp_path / "correction.json"
    frozen_commit = "2cf1496b2281d32af442e3643f0617dc21f61e3c"
    _physiology_run(first, frozen_commit)
    _physiology_run(second, frozen_commit)
    _write(correction, {"invalid_result_sha256": "d" * 64})
    value = json.loads(
        (second / "physiology_positive_control.json").read_text(encoding="utf-8")
    )
    value["scientific_endpoint_pass"] = False
    _write(second / "physiology_positive_control.json", value)

    result = compare_physiology_runs(
        [first, second], correction, frozen_commit
    )

    assert result["pass"] is False
    assert result["byte_identical"] is False
