from __future__ import annotations

import json
from pathlib import Path

import numpy as np
import pytest

from harmo.mechanics_audit import (
    assess_mass_recommendation,
    canonicalize_opensim_log,
    classify_ground_torque_semantics,
    compare_inverse_dynamics,
    diagnose_released_kinematics_closure,
    evaluate_mechanics_audit,
    parse_rra_mass_recommendation,
    pelvis_mass_center,
    reconstruct_cop_and_free_moment,
    resample_storage_to_grid,
    transform_wrench_to_point,
    verify_exact_grid,
)


def _storage(path: Path):
    path.write_text(
        "Coordinates\n"
        "version=1\n"
        "nRows=3\n"
        "nColumns=3\n"
        "inDegrees=yes\n"
        "endheader\n"
        "time\tq1\tq2\n"
        "0.0\t0\t10\n"
        "0.5\t5\t20\n"
        "1.0\t10\t30\n",
        encoding="utf-8",
    )


def test_force_plate_wrench_round_trip_and_free_moment_semantics():
    force = np.asarray([[100.0, 800.0, -30.0], [-50.0, 900.0, 20.0]])
    cop = np.asarray([[0.10, 0.0, -0.20], [-0.15, 0.0, 0.30]])
    free = np.asarray([[0.0, 12.0, 0.0], [0.0, -8.0, 0.0]])
    origin_moment = np.cross(cop, force) + free

    observed_cop, observed_free, contact = reconstruct_cop_and_free_moment(
        force, origin_moment, vertical_axis=1
    )

    assert contact.tolist() == [True, True]
    assert observed_cop == pytest.approx(cop, abs=1e-12)
    assert observed_free == pytest.approx(free, abs=1e-12)
    assert transform_wrench_to_point(force, origin_moment, cop) == pytest.approx(
        free, abs=1e-12
    )
    classification = classify_ground_torque_semantics(
        force, origin_moment, cop, free, atol_nm=1e-10
    )
    assert classification["semantics"] == "cop_free_moment"
    assert classification["pass"] is True


@pytest.mark.parametrize(
    "fault",
    ["moment-sign", "point-unit", "time-shift"],
)
def test_force_plate_negative_controls_do_not_round_trip(fault):
    force = np.asarray([[100.0, 800.0, -30.0], [120.0, 850.0, -20.0]])
    cop = np.asarray([[0.1, 0.0, -0.2], [0.12, 0.0, -0.18]])
    free = np.asarray([[0.0, 10.0, 0.0], [0.0, 11.0, 0.0]])
    moment = np.cross(cop, force) + free
    candidate_force = force.copy()
    candidate_moment = moment.copy()
    candidate_cop = cop.copy()
    if fault == "moment-sign":
        candidate_moment *= -1
    elif fault == "point-unit":
        candidate_cop *= 1000
    else:
        candidate_force = candidate_force[::-1]
    recovered = transform_wrench_to_point(
        candidate_force, candidate_moment, candidate_cop
    )
    assert not np.allclose(recovered, free, atol=1e-9, rtol=0.0)


def test_resample_storage_preserves_explicit_source_grid(tmp_path):
    source = tmp_path / "adaptive.sto"
    output = tmp_path / "source_grid.sto"
    _storage(source)
    target = [0.0, 0.25, 0.5, 0.75, 1.0]

    report = resample_storage_to_grid(source, target, output)

    assert report["source_row_count"] == 3
    assert report["output_row_count"] == 5
    assert report["exact_source_grid"] is True
    assert verify_exact_grid(output, target)


def test_pelvis_com_and_rra_mass_recommendation_are_explicit(tmp_path):
    model = tmp_path / "model.osim"
    model.write_text(
        '<OpenSimDocument><Model><BodySet><objects><Body name="pelvis">'
        "<mass_center>-0.07 0.01 0.02</mass_center>"
        "</Body></objects></BodySet></Model></OpenSimDocument>",
        encoding="utf-8",
    )
    assert pelvis_mass_center(model) == pytest.approx([-0.07, 0.01, 0.02])
    parsed = parse_rra_mass_recommendation(
        "Recommended total mass change: -4.106 kg"
    )
    assert parsed["recommended_mass_change_kg"] == pytest.approx(-4.106)
    assert parsed["auto_applied"] is False
    assessment = assess_mass_recommendation(-4.106, 81.0, 790.68)
    assert assessment["auto_apply"] is False
    assert assessment["independently_consistent"] is False


def test_two_pass_mass_recommendations_preserve_sequence():
    parsed = parse_rra_mass_recommendation(
        "Recommended total mass change: -4.106\n"
        "Recommended total mass change: -1.250 kg\n"
    )
    assert parsed["recommendation_sequence_kg"] == [-4.106, -1.25]
    assert parsed["recommended_mass_change_kg"] == pytest.approx(-1.25)


def test_opensim_log_canonicalization_removes_only_runtime_fields():
    first = (
        "[2026-07-21 18:29:34.573] [info] Running tool fixed\n"
        "[2026-07-21 18:29:45.146] [info] InverseDynamicsTool: 6001 time frames in 7 second(s).\n"
        " -- Start time = Tue Jul 21 18:30:08 2026\n"
        " -- Elapsed time = 13.0 seconds.\n"
    )
    second = (
        "[2026-07-21 18:32:38.978] [info] Running tool fixed\n"
        "[2026-07-21 18:32:49.077] [info] InverseDynamicsTool: 6001 time frames in 6 second(s).\n"
        " -- Start time = Tue Jul 21 18:33:12 2026\n"
        " -- Elapsed time = 12.0 seconds.\n"
    )
    assert canonicalize_opensim_log(first) == canonicalize_opensim_log(second)
    assert "6001 time frames" in canonicalize_opensim_log(first)


def test_id_comparison_uses_only_common_time_window(tmp_path):
    reference = tmp_path / "reference.sto"
    candidate = tmp_path / "candidate.sto"
    reference.write_text(
        "endheader\ntime\tjoint_moment\n"
        "0\t1000\n1\t1\n2\t2\n3\t3\n4\t1000\n",
        encoding="utf-8",
    )
    candidate.write_text(
        "endheader\ntime\tjoint_moment\n1\t1\n2\t2\n3\t3\n",
        encoding="utf-8",
    )
    result = compare_inverse_dynamics(reference, candidate, ["joint_moment"])
    assert result["comparison_row_count"] == 3
    assert result["maximum_nrmse"] == pytest.approx(0.0)


def test_id_comparison_honors_declared_diagnostic_window(tmp_path):
    reference = tmp_path / "reference.sto"
    candidate = tmp_path / "candidate.sto"
    reference.write_text(
        "endheader\ntime\tjoint_moment\n"
        "0\t1000\n1\t1\n2\t2\n3\t3\n4\t1000\n",
        encoding="utf-8",
    )
    candidate.write_text(
        "endheader\ntime\tjoint_moment\n"
        "0\t-1000\n1\t1\n2\t2\n3\t3\n4\t-1000\n",
        encoding="utf-8",
    )
    result = compare_inverse_dynamics(
        reference,
        candidate,
        ["joint_moment"],
        comparison_window_s=[1.0, 3.0],
    )
    assert result["comparison_row_count"] == 3
    assert result["maximum_nrmse"] == pytest.approx(0.0)
    assert result["declared_comparison_window_s"] == [1.0, 3.0]


def test_kinematics_closure_localizes_proximal_released_artifact():
    released = {
        "coordinates": [
            {"coordinate": "knee_r", "nrmse": 0.001, "correlation": 1.0},
            {"coordinate": "ankle_r", "nrmse": 0.001, "correlation": 1.0},
            {"coordinate": "hip_r", "nrmse": 8.0, "correlation": 0.1},
        ],
        "median_nrmse": 0.001,
        "maximum_nrmse": 8.0,
        "minimum_correlation": 0.1,
    }
    marker_rederived = {
        "coordinates": [
            {"coordinate": "knee_r", "nrmse": 0.08, "correlation": 0.998},
            {"coordinate": "ankle_r", "nrmse": 0.09, "correlation": 0.998},
            {"coordinate": "hip_r", "nrmse": 0.1, "correlation": 0.995},
        ],
        "median_nrmse": 0.09,
        "maximum_nrmse": 0.1,
        "minimum_correlation": 0.995,
    }
    result = diagnose_released_kinematics_closure(
        released,
        marker_rederived,
        distal_coordinates=["knee_r", "ankle_r"],
        proximal_coordinates=["hip_r"],
        thresholds={
            "median_nrmse": 0.02,
            "maximum_nrmse": 0.05,
            "minimum_correlation": 0.99,
        },
        minimum_proximal_improvement_factor=5.0,
    )
    assert result["classification"] == "released-kinematics-to-id-nonclosure"
    assert result["public_bundle_closed"] is False
    assert result["released_distal_coordinates_pass"] is True
    assert result["released_proximal_coordinates_pass"] is False
    assert result["proximal_nrmse_improvement_factor"]["hip_r"] == pytest.approx(
        80.0
    )
    assert result["marker_rederived_reference_replacement_allowed"] is False
    assert result["marker_rederived_comparison_passes_frozen_thresholds"] is False


def test_g6c_gate_fails_closed_on_unexplained_common_residual():
    plan = {
        "input_sha256": {"model": "a" * 64},
        "thresholds": {
            "median_nrmse": 0.02,
            "maximum_nrmse": 0.05,
            "minimum_correlation": 0.99,
        },
    }
    worker = {
        "scope": "g6c-mechanics-audit-worker",
        "input_sha256": plan["input_sha256"],
        "source_grid_exact": True,
        "public_id_grid_exact": True,
        "residual_point_is_pelvis_com": True,
        "mass_recommendation": {"auto_applied": False},
        "external_load_audit": {"pass": True},
        "kinematics_id_closure": {"public_bundle_closed": False},
        "public_id_reproduction": {
            "median_nrmse": 0.0,
            "maximum_nrmse": 0.0,
            "minimum_correlation": 1.0,
        },
        "common_residual_explained": False,
    }

    result = evaluate_mechanics_audit(plan, worker)

    assert result["pass"] is False
    assert result["checks"]["common_residual_explained"] is False
    assert result["checks"]["released_source_bundle_closed"] is False
    assert result["next_gate"] == "blocked-at-g6c"
