from __future__ import annotations

from pathlib import Path

import numpy as np
import pytest

from harmo.mechanics_localization import (
    compare_model_inertial_properties,
    evaluate_mechanics_localization,
    factorial_effect_localization,
    optimal_scalar_mass_diagnostic,
    shift_wrench_on_grid,
    synchronization_cross_validation,
)
from harmo.whole_body_balance import time_derivative


def _value(force, moment):
    return {
        "residual_force_fraction": force,
        "residual_moment_fraction": moment,
    }


def test_shift_wrench_uses_declared_positive_lag():
    time = np.arange(0.0, 4.01, 0.01)
    force = np.column_stack([time, 2 * time, 3 * time])
    moment = -force
    shifted = shift_wrench_on_grid(time, force, moment, 0.02)
    assert shifted["force_n"][100] == pytest.approx(force[102])
    assert shifted["moment_origin_nm"][100] == pytest.approx(moment[102])


def test_synchronization_lag_is_selected_outside_target_and_recovers_known_shift():
    time = np.arange(0.0, 10.01, 0.01)
    mass = 70.0
    gravity = np.asarray([0.0, -9.80665, 0.0])
    omega = 2 * np.pi * 1.3
    com = np.column_stack(
        [
            0.04 * np.sin(omega * time),
            1.0 + 0.02 * np.cos(omega * time),
            np.zeros(time.size),
        ]
    )
    acceleration = time_derivative(time, time_derivative(time, com))
    true_force = mass * acceleration - mass * gravity[None, :]
    true_moment = np.cross(com, true_force)
    delay = 0.02
    observed_force = np.column_stack(
        [np.interp(time - delay, time, true_force[:, axis]) for axis in range(3)]
    )
    observed_moment = np.column_stack(
        [np.interp(time - delay, time, true_moment[:, axis]) for axis in range(3)]
    )
    result = synchronization_cross_validation(
        {
            "time_s": time,
            "com_position_m": com,
            "central_angular_momentum_nms": np.zeros_like(com),
            "mass_kg": mass,
            "gravity_m_s2": gravity,
            "vertical_axis": 1,
        },
        observed_force,
        observed_moment,
        calibration_windows_s=[[1.0, 3.0], [7.0, 9.0]],
        target_window_s=[4.0, 6.0],
        minimum_lag_s=-0.05,
        maximum_lag_s=0.05,
        lag_step_s=0.001,
        maximum_calibration_disagreement_s=0.005,
        minimum_target_force_reduction=0.5,
        force_fraction_threshold=0.05,
        moment_fraction_threshold=0.01,
        external_lowpass_cutoff_hz=6.0,
        lowpass_order=4,
    )
    assert result["selection_uses_target_window"] is False
    assert result["calibration_selected_lags_s"] == pytest.approx([delay, delay])
    assert result["selected_lag_s"] == pytest.approx(delay)
    assert result["target_selected_force_closed"] is True
    assert result["synchronization_sufficient"] is True


def test_synchronization_rejects_window_that_would_require_extrapolation():
    time = np.arange(0.0, 1.001, 0.01)
    zeros = np.zeros((time.size, 3))
    kinematics = {
        "time_s": time,
        "com_position_m": zeros,
        "central_angular_momentum_nms": zeros,
        "mass_kg": 70.0,
        "gravity_m_s2": [0.0, -9.80665, 0.0],
        "vertical_axis": 1,
    }
    with pytest.raises(ValueError, match="require extrapolation"):
        synchronization_cross_validation(
            kinematics,
            zeros,
            zeros,
            calibration_windows_s=[[0.01, 0.2], [0.4, 0.6]],
            target_window_s=[0.7, 0.9],
            minimum_lag_s=-0.05,
            maximum_lag_s=0.05,
            lag_step_s=0.001,
            maximum_calibration_disagreement_s=0.005,
            minimum_target_force_reduction=0.5,
            force_fraction_threshold=0.05,
            moment_fraction_threshold=0.01,
            external_lowpass_cutoff_hz=6.0,
            lowpass_order=4,
        )


def test_scalar_mass_diagnostic_recovers_mass_but_never_applies_it():
    time = np.arange(0.0, 5.01, 0.01)
    gravity = np.asarray([0.0, -9.80665, 0.0])
    com = np.column_stack(
        [
            0.02 * np.sin(2 * np.pi * time),
            1.0 + 0.01 * np.cos(2 * np.pi * time),
            np.zeros(time.size),
        ]
    )
    acceleration = time_derivative(time, time_derivative(time, com))
    force = 80.0 * (acceleration - gravity[None, :])
    result = optimal_scalar_mass_diagnostic(
        time,
        com,
        force,
        recorded_mass_kg=81.0,
        gravity_m_s2=gravity,
        evaluation_window_s=[1.0, 4.0],
        maximum_relative_mass_change=0.02,
        force_fraction_threshold=0.05,
    )
    assert result["least_squares_mass_kg"] == pytest.approx(80.0)
    assert result["scalar_mass_sufficient"] is True
    assert result["auto_apply"] is False


def test_factorial_localizes_force_to_kinematics_and_leaves_moment_unresolved():
    result = factorial_effect_localization(
        {
            "original_model_ik": _value(0.10, 0.05),
            "original_model_rra_kinematics": _value(0.03, 0.035),
            "adjusted_model_ik": _value(0.09, 0.048),
            "adjusted_model_rra_kinematics": _value(0.025, 0.03),
        },
        force_fraction_threshold=0.05,
        moment_fraction_threshold=0.01,
        minimum_relative_reduction=0.25,
    )
    assert result["force"]["classification"] == "rra-kinematics-sufficient"
    assert result["moment"]["classification"] == "kinematics-contributor-not-sufficient"
    assert result["combined_mechanics_closed"] is False


def _model(path: Path, center: str):
    path.write_text(
        "<OpenSimDocument><Model><BodySet><objects>"
        '<Body name="pelvis"><mass>10</mass><mass_center>{}</mass_center>'
        "<inertia_xx>1</inertia_xx><inertia_yy>2</inertia_yy>"
        "<inertia_zz>3</inertia_zz><inertia_xy>0</inertia_xy>"
        "<inertia_xz>0</inertia_xz><inertia_yz>0</inertia_yz>"
        "</Body></objects></BodySet></Model></OpenSimDocument>".format(center),
        encoding="utf-8",
    )


def test_inertial_comparison_reports_only_changed_body(tmp_path):
    original = tmp_path / "original.osim"
    adjusted = tmp_path / "adjusted.osim"
    _model(original, "0 0 0")
    _model(adjusted, "0.01 0 0")
    result = compare_model_inertial_properties(original, adjusted)
    assert result["changed_body_count"] == 1
    assert result["changed_bodies"][0]["body"] == "pelvis"
    assert result["changed_bodies"][0]["mass_center_delta_m"] == pytest.approx(
        [0.01, 0.0, 0.0]
    )


def test_inertial_comparison_ignores_ground_and_opensim_roundtrip_noise(tmp_path):
    original = tmp_path / "original.osim"
    adjusted = tmp_path / "adjusted.osim"
    original.write_text(
        "<OpenSimDocument><Model><BodySet><objects>"
        '<Body name="ground"><mass>0</mass><mass_center>0 0 0</mass_center>'
        "<inertia_xx>0</inertia_xx><inertia_yy>0</inertia_yy>"
        "<inertia_zz>0</inertia_zz><inertia_xy>0</inertia_xy>"
        "<inertia_xz>0</inertia_xz><inertia_yz>0</inertia_yz></Body>"
        '<Body name="pelvis"><mass>10</mass><mass_center>0 0 0</mass_center>'
        "<inertia_xx>1.5553351490437</inertia_xx>"
        "<inertia_yy>0.796918077383872</inertia_yy>"
        "<inertia_zz>1.5098723176271</inertia_zz>"
        "<inertia_xy>0</inertia_xy><inertia_xz>0</inertia_xz>"
        "<inertia_yz>0</inertia_yz></Body>"
        "</objects></BodySet></Model></OpenSimDocument>",
        encoding="utf-8",
    )
    adjusted.write_text(
        "<OpenSimDocument><Model><BodySet><objects>"
        '<Body name="pelvis"><mass>10</mass><mass_center>0 0 0</mass_center>'
        "<inertia>1.55534 0.796918 1.50987 0 0 0</inertia></Body>"
        "</objects></BodySet></Model></OpenSimDocument>",
        encoding="utf-8",
    )
    result = compare_model_inertial_properties(original, adjusted)
    assert result["body_count"] == 1
    assert result["changed_body_count"] == 0
    assert result["all_bodies"][0]["inertia_delta_kg_m2"] != [0.0] * 6


def test_g6cs2_evaluator_stays_blocked_when_localization_is_complete():
    plan = {
        "input_sha256": {"model": "a" * 64},
        "prior_g6cs_result_sha256": "b" * 64,
        "thresholds": {
            "residual_force_fraction": 0.05,
            "residual_moment_fraction": 0.01,
        },
    }
    worker = {
        "scope": "g6c-s2-mechanics-localization-worker",
        "input_sha256": plan["input_sha256"],
        "prior_g6cs_result_sha256": plan["prior_g6cs_result_sha256"],
        "kinematics_grid_exact": True,
        "target_grid_exact": True,
        "synchronization": {"selection_uses_target_window": False},
        "raw_factorial": {"combined_mechanics_closed": False},
        "mot_factorial": {"combined_mechanics_closed": False},
        "rra_pass_count": 2,
        "scalar_mass_diagnostic": {"auto_apply": False},
        "prior_rra": {"residual_moment_fraction": 0.022},
    }
    result = evaluate_mechanics_localization(plan, worker)
    assert result["diagnostic_complete"] is True
    assert result["pass"] is False
    assert result["next_gate"] == "blocked-at-g6c-s2"


def test_g6cs2_evaluator_advances_only_when_both_factorials_and_prior_rra_close():
    plan = {
        "input_sha256": {"model": "a" * 64},
        "prior_g6cs_result_sha256": "b" * 64,
        "thresholds": {
            "residual_force_fraction": 0.05,
            "residual_moment_fraction": 0.01,
        },
    }
    worker = {
        "scope": "g6c-s2-mechanics-localization-worker",
        "input_sha256": plan["input_sha256"],
        "prior_g6cs_result_sha256": plan["prior_g6cs_result_sha256"],
        "kinematics_grid_exact": True,
        "target_grid_exact": True,
        "synchronization": {"selection_uses_target_window": False},
        "raw_factorial": {"combined_mechanics_closed": True},
        "mot_factorial": {"combined_mechanics_closed": True},
        "rra_pass_count": 2,
        "scalar_mass_diagnostic": {"auto_apply": False},
        "prior_rra": {"residual_moment_fraction": 0.005},
    }
    result = evaluate_mechanics_localization(plan, worker)
    assert result["diagnostic_complete"] is True
    assert result["pass"] is True
    assert result["next_gate"] == "g6d"
