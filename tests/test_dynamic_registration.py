from __future__ import annotations

import hashlib
import json
import runpy
from pathlib import Path

import pytest
import yaml

import harmo.dynamic_registration as dynamic_registration
from harmo.dynamic_registration import (
    build_dynamic_registration_plan,
    build_registered_mechanics_plan,
    evaluate_dynamic_registration,
)


ROOT = Path(__file__).resolve().parents[1]


def _sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _asset(path: Path, role: str):
    return {
        "role": role,
        "local_name": path.name,
        "download_url": "https://example.org/{}".format(path.name),
        "size_bytes": path.stat().st_size,
        "sha256": _sha(path),
    }


def _trc(path: Path):
    path.write_text(
        "PathFileType\t4\t(X/Y/Z)\ttest.trc\n"
        "DataRate\tCameraRate\tNumFrames\tNumMarkers\tUnits\tOrigDataRate\tOrigDataStartFrame\tOrigNumFrames\n"
        "100\t100\t2\t1\tm\t100\t0\t1\n"
        "Frame#\tTimes\t\tA\n"
        "\t\tX1\tY1\tZ1\n\n"
        "0\t19\t0\t0\t0\n"
        "1\t31\t1\t0\t0\n",
        encoding="utf-8",
    )


def _fixture(tmp_path):
    trials = []
    for index in range(3):
        trial = tmp_path / "trial-{}.trc".format(index)
        _trc(trial)
        trials.append(trial)
    ik = tmp_path / "ik.xml"
    ik.write_text(
        "<OpenSimDocument><IKTaskSet><objects>"
        '<IKMarkerTask name="A"><apply>true</apply><weight>1</weight>'
        "</IKMarkerTask></objects></IKTaskSet></OpenSimDocument>",
        encoding="utf-8",
    )
    model = tmp_path / "dynamic.osim"
    model.write_text("dynamic model", encoding="utf-8")
    g6b2 = tmp_path / "g6b2.json"
    g6b2.write_text(
        json.dumps({"pass": True, "generated_model_sha256": _sha(model)}),
        encoding="utf-8",
    )
    baseline = tmp_path / "baseline.json"
    baseline.write_text(json.dumps({"pass": False}), encoding="utf-8")
    manifest = tmp_path / "manifest.yaml"
    roles = ["dynamic_a", "dynamic_b", "dynamic_c"]
    manifest.write_text(
        yaml.safe_dump(
            {
                "schema_version": "1.0.0",
                "source_record_doi": "10.example/test",
                "license": "CC0",
                "assets": [
                    *[_asset(path, role) for path, role in zip(trials, roles)],
                    _asset(ik, "ik"),
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
                "claim_boundary": "synthetic test",
                "preconditions": {
                    "target_subject_id": "02",
                    "g6b2_result_sha256": _sha(g6b2),
                    "retained_g6b3_result_sha256": _sha(baseline),
                    "base_dynamic_model_sha256": _sha(model),
                },
                "design": {
                    "calibration_roles": roles,
                    "ik_setup_role": "ik",
                    "excluded_from_registration_worker": ["hidden", "heldout"],
                    "calibration_window_s": [20.0, 30.0],
                    "active_markers": ["A"],
                },
                "registration": {
                    "iteration_count": 3,
                    "sampled_frame_stride": 10,
                },
                "thresholds": {
                    "minimum_trial_rms_improvement_fraction": 0.25,
                    "final_calibration_ik_rms_error_m": 0.02,
                    "final_calibration_ik_max_error_m": 0.04,
                    "maximum_marker_displacement_m": 0.12,
                    "maximum_within_trial_local_mad_m": 0.025,
                },
            },
            sort_keys=False,
        ),
        encoding="utf-8",
    )
    return protocol, manifest, model, g6b2, baseline


def test_registration_plan_excludes_static_and_heldout_inputs(tmp_path):
    protocol, manifest, model, g6b2, baseline = _fixture(tmp_path)

    plan = build_dynamic_registration_plan(
        protocol, manifest, tmp_path, model, g6b2, baseline
    )

    assert plan["calibration_roles"] == ["dynamic_a", "dynamic_b", "dynamic_c"]
    assert set(plan["input_paths"]) == {
        "dynamic_a",
        "dynamic_b",
        "dynamic_c",
        "ik",
        "base_dynamic_model",
    }
    assert "hidden" not in plan["input_paths"]
    assert "heldout" not in plan["input_paths"]


def test_registration_plan_rejects_changed_baseline(tmp_path):
    protocol, manifest, model, g6b2, baseline = _fixture(tmp_path)
    baseline.write_text("changed", encoding="utf-8")

    with pytest.raises(ValueError, match="retained baseline checksum mismatch"):
        build_dynamic_registration_plan(
            protocol, manifest, tmp_path, model, g6b2, baseline
        )


def test_registration_evaluation_applies_all_frozen_thresholds(tmp_path):
    protocol, manifest, model, g6b2, baseline = _fixture(tmp_path)
    plan = build_dynamic_registration_plan(
        protocol, manifest, tmp_path, model, g6b2, baseline
    )
    registered = tmp_path / "registered.osim"
    registered.write_text("registered", encoding="utf-8")
    trials = {
        role: {
            "initial": {"ik_rms_error_m": 0.04, "ik_max_error_m": 0.08},
            "final": {"ik_rms_error_m": 0.01, "ik_max_error_m": 0.03},
        }
        for role in plan["calibration_roles"]
    }
    worker = {
        "opensim_version": "4.5.2-test",
        "input_sha256": plan["input_sha256"],
        "hidden_inputs_available": False,
        "iteration_count": 3,
        "output_model_sha256": _sha(registered),
        "trials": trials,
        "markers": [
            {
                "marker": "A",
                "total_displacement_m": 0.03,
                "within_trial_local_mad_m": {
                    role: 0.005 for role in plan["calibration_roles"]
                },
            }
        ],
    }

    result = evaluate_dynamic_registration(protocol, plan, worker, registered)

    assert result["pass"] is True
    assert all(result["checks"].values())
    assert result["summary"]["minimum_trial_rms_improvement_fraction"] == pytest.approx(
        0.75
    )


def test_registration_worker_maps_trc_columns_by_marker_name(tmp_path):
    namespace = runpy.run_path(
        str(ROOT / "scripts/paper/run_dynamic_registration_worker.py")
    )
    read_trc = namespace["_read_trc"]
    trc = tmp_path / "permuted.trc"
    trc.write_text(
        "PathFileType\t4\t(X/Y/Z)\ttest.trc\n"
        "DataRate\tCameraRate\tNumFrames\tNumMarkers\tUnits\tOrigDataRate\tOrigDataStartFrame\tOrigNumFrames\n"
        "100\t100\t2\t2\tm\t100\t0\t1\n"
        "Frame#\tTime\t\tB\t\t\tA\n"
        "\t\tX1\tY1\tZ1\tX2\tY2\tZ2\n\n"
        "0\t0\t1\t2\t3\t4\t5\t6\n"
        "1\t0.01\t7\t8\t9\t10\t11\t12\n",
        encoding="utf-8",
    )

    times, observations = read_trc(trc, ["A", "B"])

    assert times == [0.0, 0.01]
    assert observations["B"][0] == [1.0, 2.0, 3.0]
    assert observations["A"][1] == [10.0, 11.0, 12.0]


def test_registered_mechanics_plan_replaces_only_the_candidate_model(
    tmp_path, monkeypatch
):
    baseline_protocol = tmp_path / "baseline.yaml"
    baseline_protocol.write_text("baseline", encoding="utf-8")
    base_model = tmp_path / "base.osim"
    base_model.write_text("base", encoding="utf-8")
    registered_model = tmp_path / "registered.osim"
    registered_model.write_text("registered", encoding="utf-8")
    registration_result = tmp_path / "registration.json"
    registration_result.write_text(
        json.dumps(
            {
                "pass": False,
                "registered_model_sha256": _sha(registered_model),
                "worker": {"hidden_inputs_available": False},
            }
        ),
        encoding="utf-8",
    )
    wrapper = tmp_path / "wrapper.yaml"
    wrapper.write_text(
        yaml.safe_dump(
            {
                "schema_version": "1.0.0",
                "claim_boundary": "exploratory",
                "baseline_mechanics_protocol_sha256": _sha(baseline_protocol),
                "registration_result_sha256": _sha(registration_result),
                "registered_model_sha256": _sha(registered_model),
                "base_dynamic_model_sha256": _sha(base_model),
                "allow_exploratory_mechanics_after_failed_registration_gate": True,
            }
        ),
        encoding="utf-8",
    )

    def fake_baseline_plan(*_args):
        return {
            "input_paths": {
                "dynamic_derived_model": str(base_model),
                "heldout_markers": "heldout.trc",
            },
            "input_sha256": {
                "dynamic_derived_model": _sha(base_model),
                "heldout_markers": "heldout-hash",
            },
            "models": {"dynamic_derived": {}, "static_derived": {}},
        }

    monkeypatch.setattr(
        dynamic_registration, "build_dynamic_mechanics_plan", fake_baseline_plan
    )

    plan = build_registered_mechanics_plan(
        wrapper,
        baseline_protocol,
        tmp_path / "manifest.yaml",
        tmp_path,
        base_model,
        registered_model,
        tmp_path / "g6b2.json",
        registration_result,
    )

    assert plan["input_paths"]["dynamic_derived_model"] == str(registered_model)
    assert plan["input_sha256"]["dynamic_derived_model"] == _sha(registered_model)
    assert plan["input_paths"]["heldout_markers"] == "heldout.trc"
    assert plan["models"]["dynamic_derived"]["registration_applied"] is True
    assert plan["result_scope"] == "g6b-registered-heldout-mechanics-exploratory"
