from __future__ import annotations

import numpy as np
import pytest

from harmo.whole_body_balance import (
    calculate_whole_body_balance,
    classify_balance_nonclosure,
    evaluate_whole_body_balance,
    mot_external_wrench,
    time_derivative,
    validate_uniform_time_grid,
    zero_phase_lowpass,
)


def test_mot_external_wrench_filters_on_source_grid_before_downsampling(tmp_path):
    source_time = np.arange(0.0, 2.001, 0.001)
    high_frequency = np.sin(2.0 * np.pi * 40.0 * source_time)
    path = tmp_path / "loads.mot"
    columns = [
        "time",
        "l_ground_force_vx",
        "l_ground_force_vy",
        "l_ground_force_vz",
        "l_ground_force_px",
        "l_ground_force_py",
        "l_ground_force_pz",
        "l_ground_torque_x",
        "l_ground_torque_y",
        "l_ground_torque_z",
    ]
    rows = []
    for time, value in zip(source_time, high_frequency):
        rows.append(
            "\t".join(
                "{:.12g}".format(item)
                for item in (time, value, 100.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0)
            )
        )
    path.write_text(
        "name loads\nnRows={}\nnColumns={}\ninDegrees=no\nendheader\n{}\n{}\n".format(
            len(rows), len(columns), "\t".join(columns), "\n".join(rows)
        ),
        encoding="utf-8",
    )
    target = np.arange(0.2, 1.801, 0.01)
    unfiltered = mot_external_wrench(path, target, prefixes=["l"])
    filtered = mot_external_wrench(
        path,
        target,
        prefixes=["l"],
        source_lowpass_cutoff_hz=6.0,
        lowpass_order=4,
    )
    assert np.std(unfiltered["force_n"][:, 0]) > 0.6
    assert np.std(filtered["force_n"][:, 0]) < 0.01
    assert filtered["provenance"]["source_filter"][
        "applied_before_downsampling"
    ] is True


def _stationary_balance():
    time = np.arange(0.0, 4.01, 0.01)
    mass = 10.0
    gravity = np.asarray([0.0, -9.80665, 0.0])
    com = np.tile([0.0, 1.0, 0.0], (time.size, 1))
    angular_momentum = np.column_stack(
        [2.0 * time, np.zeros(time.size), np.zeros(time.size)]
    )
    force = np.tile(-mass * gravity, (time.size, 1))
    moment_com = np.tile([2.0, 0.0, 0.0], (time.size, 1))
    moment_origin = moment_com + np.cross(com, force)
    return calculate_whole_body_balance(
        time,
        com,
        angular_momentum,
        force,
        moment_origin,
        mass_kg=mass,
        gravity_m_s2=gravity,
        vertical_axis=1,
        evaluation_window_s=[1.0, 3.0],
        external_lowpass_cutoff_hz=6.0,
        lowpass_order=4,
    )


def test_newton_euler_balance_closes_for_known_force_and_moment():
    result = _stationary_balance()
    assert result["residual_force_fraction"] == pytest.approx(0.0, abs=1e-12)
    assert result["residual_moment_fraction"] == pytest.approx(0.0, abs=1e-12)
    assert result["evaluation_sample_count"] == 201
    assert result["equations"]["force"].startswith("m*a_com")


def test_time_shift_fault_increases_whole_body_force_nonclosure():
    time = np.arange(0.0, 6.01, 0.01)
    mass = 70.0
    gravity = np.asarray([0.0, -9.80665, 0.0])
    omega = 2.0 * np.pi * 2.0
    com = np.column_stack(
        [
            0.05 * np.sin(omega * time),
            1.0 + 0.02 * np.cos(omega * time),
            np.zeros(time.size),
        ]
    )
    acceleration = np.column_stack(
        [
            -0.05 * omega**2 * np.sin(omega * time),
            -0.02 * omega**2 * np.cos(omega * time),
            np.zeros(time.size),
        ]
    )
    force = mass * acceleration - mass * gravity[None, :]
    momentum = np.zeros_like(com)
    moment_origin = np.cross(com, force)

    def run(candidate_force, candidate_moment):
        return calculate_whole_body_balance(
            time,
            com,
            momentum,
            candidate_force,
            candidate_moment,
            mass_kg=mass,
            gravity_m_s2=gravity,
            vertical_axis=1,
            evaluation_window_s=[1.0, 5.0],
            external_lowpass_cutoff_hz=6.0,
        )

    baseline = run(force, moment_origin)
    shifted_force = np.column_stack(
        [np.interp(time + 0.03, time, force[:, axis]) for axis in range(3)]
    )
    shifted_moment = np.column_stack(
        [np.interp(time + 0.03, time, moment_origin[:, axis]) for axis in range(3)]
    )
    shifted = run(shifted_force, shifted_moment)
    assert shifted["residual_force_fraction"] > baseline["residual_force_fraction"] * 5


def test_lowpass_and_derivative_reject_nonuniform_or_short_grids():
    time = np.arange(0.0, 1.0, 0.01)
    values = np.sin(2 * np.pi * time)
    assert zero_phase_lowpass(time, values, cutoff_hz=6.0).shape == values.shape
    assert time_derivative(time, values).shape == values.shape
    broken = time.copy()
    broken[20] += 0.002
    with pytest.raises(ValueError, match="uniform"):
        validate_uniform_time_grid(broken)
    with pytest.raises(ValueError, match="too short"):
        zero_phase_lowpass(np.arange(8.0), np.arange(8.0), cutoff_hz=0.1)


@pytest.mark.parametrize(
    ("raw", "mot", "rra", "expected"),
    [
        ((0.01, 0.005), (0.01, 0.005), (0.03, 0.022), "rra-configuration-specific-nonclosure"),
        ((0.01, 0.005), (0.06, 0.02), (0.03, 0.022), "published-mot-reconstruction-nonclosure"),
        ((0.06, 0.02), (0.07, 0.03), (0.03, 0.022), "kinematics-model-or-time-synchronization-nonclosure"),
        ((0.06, 0.02), (0.01, 0.005), (0.03, 0.022), "raw-c3d-transform-nonclosure"),
        ((0.01, 0.005), (0.01, 0.005), (0.03, 0.005), "whole-body-and-rra-closed"),
    ],
)
def test_balance_classification_is_threshold_frozen(raw, mot, rra, expected):
    def value(pair):
        return {
            "residual_force_fraction": pair[0],
            "residual_moment_fraction": pair[1],
        }

    result = classify_balance_nonclosure(
        value(raw),
        value(mot),
        value(rra),
        force_fraction_threshold=0.05,
        moment_fraction_threshold=0.01,
    )
    assert result["classification"] == expected
    assert result["threshold_policy"] == "inherited-unchanged-from-g6c"


def test_g6cs_evaluator_distinguishes_diagnostic_completion_from_mechanics_pass():
    plan = {
        "input_sha256": {"raw_c3d": "a" * 64, "ground_reaction": "b" * 64},
        "prior_g6c_result_sha256": "c" * 64,
        "thresholds": {
            "residual_force_fraction": 0.05,
            "residual_moment_fraction": 0.01,
        },
    }
    worker = {
        "scope": "g6c-s-whole-body-balance-worker",
        "input_sha256": plan["input_sha256"],
        "prior_g6c_result_sha256": plan["prior_g6c_result_sha256"],
        "kinematics_grid_exact": True,
        "evaluation_grid_exact": True,
        "raw_balance": {"source_provenance": {"source_sha256": "a" * 64}},
        "mot_balance": {"source_provenance": {"source_sha256": "b" * 64}},
        "diagnosis": {
            "classification": "rra-configuration-specific-nonclosure",
            "mechanics_closed": False,
            "thresholds": plan["thresholds"],
        },
    }
    result = evaluate_whole_body_balance(plan, worker)
    assert result["diagnostic_complete"] is True
    assert result["pass"] is False
    assert result["next_gate"] == "blocked-at-g6c-s"
