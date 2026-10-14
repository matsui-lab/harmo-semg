from __future__ import annotations

import copy
import json
from pathlib import Path

import pytest
import pandas as pd

from harmo.validation_study import (
    _mapping_matches,
    compare_reproducibility_runs,
    compare_signal_records,
    create_reproducibility_snapshot,
    fault_injection_benchmark,
    gate_audit,
    inventory_source_tree,
    load_registry,
    opensim_denominator,
    physiology_timing_metrics,
    scientific_manifest,
    subject_hierarchical_bootstrap,
    validate_adapter_metrics,
    validate_registry,
    write_rows,
)
from harmo.format_fidelity import compare_format_frames


ROOT = Path(__file__).resolve().parents[1]
REGISTRY_PATH = ROOT / "paper" / "validation_registry.yaml"


def test_write_rows_uses_portable_lf_csv_line_endings(tmp_path):
    write_rows(tmp_path / "rows.json", [{"value": 1}, {"value": 2}])
    csv_bytes = (tmp_path / "rows.csv").read_bytes()
    assert csv_bytes.count(b"\n") == 3
    assert b"\r" not in csv_bytes


def test_registry_freezes_all_experiments_and_denominators():
    registry = load_registry(REGISTRY_PATH)
    assert validate_registry(registry) == []
    assert len(registry["studies"]) == 8
    assert set(registry["experiments"]) == set("ABCDEFG")
    assert registry["experiments"]["E"]["denominator"] == 336
    assert registry["experiments"]["D"]["environments"] == [
        "macOS-native",
        "github-actions-ubuntu",
    ]
    assert registry["catalog_source"]["sha256"] == (
        "dddda90fc92517dc63e38c753edbc1ad1ab46c8479847edafb96f24a1c5cfbd6"
    )
    assert sorted(
        item["selection_row"]
        for item in registry["studies"]
        if item["selection_source"] == "workbook"
    ) == [28, 31, 32, 33, 38, 43]
    assert sorted(
        item["id"]
        for item in registry["studies"]
        if item["selection_source"] == "post-workbook-addition"
    ) == ["study_1", "study_120"]
    g6b = registry["extensions"]["G6b"]
    assert g6b["downstream_mechanics_endpoint_status"] == (
        "failed-frozen-thresholds"
    )
    assert g6b["mechanics_result"] == (
        "paper/results/validation/g6b_dynamic_mechanics_pilot.json"
    )
    assert g6b["registration_endpoint_status"] == "failed-frozen-thresholds"
    assert g6b["registration_excluded_inputs"] == [
        "target-static-model",
        "heldout-4ms-trial",
    ]
    assert g6b["registered_mechanics_endpoint_status"] == (
        "failed-frozen-thresholds"
    )
    assert g6b["registered_mechanics_result"] == (
        "paper/results/validation/g6b_registered_mechanics_exploratory.json"
    )
    assert g6b["final_reporting_status"] == "complete-negative"
    assert g6b["final_scientific_endpoint_pass"] is False
    assert g6b["final_mechanics_readiness"] == "unvalidated"
    assert g6b["final_default_mechanics_export"] == "deny"


def test_registry_rejects_post_hoc_opensim_trial_change():
    registry = load_registry(REGISTRY_PATH)
    registry["experiments"]["E"]["trials"] = ["T2", "T3", "T4"]
    assert any("T1-T3" in value for value in validate_registry(registry))


def test_config_inventory_has_frozen_78_active_mappings(tmp_path):
    registry = load_registry(REGISTRY_PATH)
    _sources, mappings = inventory_source_tree(
        ROOT, registry, Path("/workspace/data"), tmp_path
    )
    assert len(mappings) == 78
    assert all(row["outcome"] == "missing" for row in mappings)
    assert all(row["error"] for row in mappings)


def test_mapping_discovery_supports_adjacent_placeholders(tmp_path):
    source = tmp_path / "50_StrokePiG" / "TVC01" / "BWA_1.c3d"
    source.parent.mkdir(parents=True)
    source.write_bytes(b"fixture")
    mapping = {
        "input_path_template": (
            "50_StrokePiG/{sub_id}/{bwa_prefix}{separator}{trial_id}.c3d"
        ),
        "path_patterns": {
            "sub_id": r"(TVC0*\d+)",
            "bwa_prefix": r"((?i:BWA))",
            "separator": r"([\s_-]*)",
            "trial_id": r"(\d+)",
        },
    }

    matches, error = _mapping_matches(tmp_path, mapping)

    assert error is None
    assert matches == [source]


def _signal(format_name="ascii"):
    return {
        "subject_id": "sub-1",
        "format": format_name,
        "sampling_rate_hz": 1000.0,
        "source_precision": 0.001,
        "times": [0.0, 0.001, 0.002],
        "channels": {"TA": [0.0, 1.0, 0.0], "GM": [1.0, 0.0, 1.0]},
        "event_samples": [0, 2],
    }


def test_format_fidelity_passes_with_source_precision_tolerance():
    candidate = _signal("c3d")
    candidate["channels"]["TA"][1] += 0.0004
    result = compare_signal_records(_signal(), candidate)
    assert result["pass"] is True
    assert result["max_numeric_error"] == pytest.approx(0.0004)


def test_format_fidelity_detects_time_event_and_channel_loss():
    candidate = _signal("mat")
    candidate["times"][1] += 0.0006
    candidate["event_samples"][1] += 2
    del candidate["channels"]["GM"]
    result = compare_signal_records(_signal(), candidate)
    assert result["pass"] is False
    assert result["max_time_difference_samples"] == pytest.approx(0.6)
    assert result["max_event_difference_samples"] == 2
    assert result["missing_channels"] == ["GM"]


def test_real_format_frame_comparison_retains_missingness_and_source_tolerance():
    reference = pd.DataFrame(
        {
            "Time": [0.0, 0.001, 0.002],
            "TA": [1.0, float("nan"), 3.0],
            "GM": [2.0, 4.0, 6.0],
        }
    )
    candidate = reference.copy()
    candidate.loc[2, "TA"] += 0.0004
    result = compare_format_frames(reference, candidate, 1000.0, 0.0005)
    assert result["equivalence_pass"] is True
    assert result["missingness_equal"] is True
    assert result["max_numeric_error"] == pytest.approx(0.0004)

    candidate.loc[1, "TA"] = 2.0
    result = compare_format_frames(reference, candidate, 1000.0, 0.0005)
    assert result["equivalence_pass"] is False
    assert result["missingness_equal"] is False


def test_real_format_frame_comparison_treats_shared_all_missing_channel_as_equal():
    reference = pd.DataFrame({"Time": [0.0, 0.001], "TA": [float("nan"), float("nan")]})
    result = compare_format_frames(reference, reference.copy(), 1000.0, 0.0005)
    assert result["equivalence_pass"] is True
    assert result["minimum_channel_correlation"] is None
    assert result["channel_metrics"]["TA"]["numeric_status"] == (
        "source-missing-in-both-representations"
    )
    json.dumps(result, allow_nan=False)


def test_eight_fault_benchmark_meets_frozen_thresholds():
    result = fault_injection_benchmark()
    assert result["faults_total"] == 8
    assert result["overall_sensitivity"] >= 0.95
    assert result["critical_sensitivity"] == 1.0
    assert result["clean_specificity"] == 1.0
    assert result["pass"] is True


def test_scientific_manifest_ignores_only_declared_volatile_fields(tmp_path):
    base = {
        "run_id": "run-1",
        "code_commit": "abc",
        "config_sha256": "0" * 64,
        "created_at": "first",
        "environment": {"platform": "mac"},
        "steps": [{"name": "convert", "started_at": "first", "parameters": {"x": 1}}],
    }
    changed_environment = copy.deepcopy(base)
    changed_environment["created_at"] = "second"
    changed_environment["environment"] = {"platform": "linux"}
    changed_environment["steps"][0]["started_at"] = "second"
    assert scientific_manifest(base) == scientific_manifest(changed_environment)

    paths = []
    for index, value in enumerate((base, changed_environment)):
        path = tmp_path / "run-{}.json".format(index)
        path.write_text(json.dumps(value), encoding="utf-8")
        paths.append(path)
    result = compare_reproducibility_runs(paths)
    assert result["pass"] is True
    assert result["environment_count"] == 2

    changed_environment["steps"][0]["parameters"]["x"] = 2
    paths[1].write_text(json.dumps(changed_environment), encoding="utf-8")
    assert compare_reproducibility_runs(paths)["pass"] is False


def test_contract_smoke_snapshot_is_deterministic_but_not_confirmatory(tmp_path):
    registry = load_registry(REGISTRY_PATH)
    paths = []
    for index in range(3):
        snapshot = create_reproducibility_snapshot(registry)
        path = tmp_path / "run-{}.json".format(index)
        path.write_text(json.dumps(snapshot), encoding="utf-8")
        paths.append(path)
    comparison = compare_reproducibility_runs(paths)
    assert comparison["pass"] is True
    assert comparison["scope"] == "contract-smoke"


def test_confirmatory_reproducibility_requires_balanced_clean_same_commit_runs(
    tmp_path,
):
    paths = []
    for environment_id in ("macOS-native", "github-actions-ubuntu"):
        for run_number in range(1, 4):
            value = {
                "validation_scope": "frozen-78-mapping-corpus",
                "run_id": "{}-{}".format(environment_id, run_number),
                "environment_id": environment_id,
                "environment": {"platform": environment_id},
                "code_commit": "a" * 40,
                "code_dirty": False,
                "pass": True,
                "numeric_comparison": {
                    "normalized_record_sha256": ["b" * 64, "b" * 64]
                },
                "mapping_ledger_sha256": "c" * 64,
            }
            path = tmp_path / "{}-{}.json".format(environment_id, run_number)
            path.write_text(json.dumps(value), encoding="utf-8")
            paths.append(path)
    result = compare_reproducibility_runs(
        paths,
        expected_environments=["macOS-native", "github-actions-ubuntu"],
        reruns_per_environment=3,
    )
    assert result["pass"] is True
    assert result["design_pass"] is True
    assert result["exact_numeric_match"] is True
    assert result["max_observed_numeric_difference"] == 0.0

    changed = json.loads(paths[-1].read_text(encoding="utf-8"))
    changed["code_dirty"] = True
    paths[-1].write_text(json.dumps(changed), encoding="utf-8")
    result = compare_reproducibility_runs(
        paths,
        expected_environments=["macOS-native", "github-actions-ubuntu"],
        reruns_per_environment=3,
    )
    assert result["pass"] is False
    assert result["clean_runs"] is False


def test_opensim_denominator_retains_missing_and_assessed_trials(tmp_path):
    registry = load_registry(REGISTRY_PATH)
    validation = tmp_path / "validation.json"
    validation.write_text(
        json.dumps(
            {
                "subject_id": "1",
                "session_id": "ses-V1-T1",
                "ik_rms_error": 0.01,
                "ik_max_error": 0.03,
                "residual_force_rms": 2.0,
                "ex_loads_rms": 100.0,
                "residual_moment_rms": 0.5,
                "ex_moment_rms": 100.0,
                "rra_angle_change_rms_deg": 4.0,
            }
        ),
        encoding="utf-8",
    )
    rows = opensim_denominator([validation], registry)
    assert len(rows) == 336
    assessed = next(
        row
        for row in rows
        if row["session_id"] == "ses-V1-T1" and row["subject_id"] == "1"
    )
    assert assessed["outcome"] == "pass"
    assert assessed["residual_force_fraction"] == pytest.approx(0.02)
    assert sum(row["outcome"] == "unassessed" for row in rows) == 335


def test_opensim_denominator_rejects_duplicate_trial_identity(tmp_path):
    registry = load_registry(REGISTRY_PATH)
    payload = {
        "subject_id": "1",
        "session_id": "ses-V1-T1",
        "ik_rms_error": 0.01,
    }
    first = tmp_path / "first.json"
    second = tmp_path / "second.json"
    first.write_text(json.dumps(payload), encoding="utf-8")
    second.write_text(json.dumps(payload), encoding="utf-8")

    with pytest.raises(ValueError, match="duplicate OpenSim validation identity"):
        opensim_denominator([first, second], registry)


def test_subject_bootstrap_uses_subject_means_not_trial_pseudoreplication():
    rows = [
        {"subject_id": "a", "value": 0.0},
        {"subject_id": "a", "value": 1.0},
        {"subject_id": "b", "value": 1.0},
    ]
    result = subject_hierarchical_bootstrap(rows, "value", iterations=1000, seed=7)
    assert result["subjects"] == 2
    assert result["estimate"] == pytest.approx(0.75)
    assert result["ci_lower"] <= result["estimate"] <= result["ci_upper"]


def test_gate_audit_does_not_treat_inventory_or_pending_licenses_as_validation(
    tmp_path,
):
    registry = load_registry(REGISTRY_PATH)
    rows = [{"outcome": "discovered", "matched_files": 1} for _ in range(78)]
    (tmp_path / "mapping_outcomes.json").write_text(json.dumps(rows), encoding="utf-8")
    audit = gate_audit(registry, tmp_path)
    assert audit["submission_ready"] is False
    assert any(
        item["gate"] == "G4"
        and item["pass"] is False
        and "conversion" in item["message"]
        for item in audit["checks"]
    )
    assert any(
        item["gate"] == "M0" and item["pass"] is False for item in audit["checks"]
    )


def test_physiology_control_reports_timing_without_cross_study_amplitude_pooling():
    registry = load_registry(REGISTRY_PATH)
    rows = [
        {
            "study_id": "study31",
            "subject_id": "sub-{:02d}".format(index),
            "muscle": "tibialis anterior",
            "side": "left",
            "normalization_method": "%MVC",
            "values": [0.0, 1.0, 0.0],
        }
        for index in range(1, 11)
    ]
    result = physiology_timing_metrics(rows, registry)
    assert result["amplitude_pooled_across_studies"] is False
    assert result["study_eligibility"][0]["eligible"] is True
    assert result["metrics"][0]["peak_timing_percent_gait_cycle"] == 50.0
    assert result["metrics"][0]["activation_centroid_percent_gait_cycle"] == 50.0


def test_adapter_metrics_require_zero_core_branches_and_complete_accounting():
    metrics = {
        "dataset": "ULTRA-MoCap",
        "core_dataset_id_branches": 0,
        "core_changed_lines": 0,
        "adapter_changed_lines": 120,
        "source_count": 195,
        "accounted_source_count": 195,
        "active_work_seconds": 3600,
    }
    assert validate_adapter_metrics(metrics)["pass"] is True
    incomplete = dict(metrics, accounted_source_count=194)
    assert validate_adapter_metrics(incomplete)["pass"] is False
