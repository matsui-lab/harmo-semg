from __future__ import annotations

import numpy as np
import pytest

from harmo.state_reconstruction import (
    aggregate_rigid_body_dynamics,
    classify_state_reconstruction,
    evaluate_state_reconstruction,
    external_source_grid_mask,
    vector_identity_metrics,
)


def test_external_source_grid_excludes_adaptive_integrator_substeps():
    time = np.asarray([20.360000000001, 20.3604, 20.361000000002])
    mask = external_source_grid_mask(time, sampling_hz=1000.0, tolerance_s=1e-11)
    assert mask.tolist() == [True, False, True]


def _thresholds():
    return {
        "residual_force_fraction": 0.05,
        "residual_moment_fraction": 0.01,
        "maximum_force_identity_error_fraction": 1e-8,
        "maximum_moment_identity_error_fraction": 1e-8,
    }


def test_vector_identity_metrics_detect_exact_and_faulted_dynamics():
    observed = np.asarray([[1.0, 2.0, 3.0], [-1.0, 0.0, 2.0]])
    exact = vector_identity_metrics(observed, observed.copy(), reference=10.0)
    faulted = vector_identity_metrics(
        observed, observed + np.asarray([0.0, 1.0, 0.0]), reference=10.0
    )
    assert exact["difference_fraction"] == pytest.approx(0.0)
    assert faulted["difference_vector_rms"] == pytest.approx(1.0)
    assert faulted["difference_fraction"] == pytest.approx(0.1)


def test_rigid_body_aggregation_includes_orbital_and_rotational_hdot():
    result = aggregate_rigid_body_dynamics(
        [
            {
                "mass_kg": 1.0,
                "center_position_m": [-1.0, 0.0, 0.0],
                "center_acceleration_m_s2": [0.0, 1.0, 0.0],
                "rotational_hdot_nm": [0.0, 0.0, 1.0],
            },
            {
                "mass_kg": 1.0,
                "center_position_m": [1.0, 0.0, 0.0],
                "center_acceleration_m_s2": [0.0, -1.0, 0.0],
                "rotational_hdot_nm": [0.0, 0.0, 2.0],
            },
        ]
    )
    assert result["mass_kg"] == pytest.approx(2.0)
    assert result["center_position_m"] == pytest.approx([0.0, 0.0, 0.0])
    assert result["center_acceleration_m_s2"] == pytest.approx([0.0, 0.0, 0.0])
    assert result["central_angular_momentum_rate_nm"] == pytest.approx(
        [0.0, 0.0, 1.0]
    )


def test_classifier_localizes_estimator_bias_but_retains_moment_failure():
    identity = {"difference_fraction": 1e-12}
    result = classify_state_reconstruction(
        force_identity=identity,
        moment_identity=identity,
        native_force_fraction=0.01,
        native_moment_fraction=0.025,
        position_force_fraction=0.056,
        position_moment_fraction=0.037,
        thresholds=_thresholds(),
    )
    assert result["force_identity_pass"] is True
    assert result["native_force_pass"] is True
    assert result["native_moment_pass"] is False
    assert result["classification"] == (
        "state-reconstruction-bias-localized-common-rra-moment-failure-remains"
    )


def test_evaluator_separates_diagnostic_completion_from_native_mechanics_pass():
    plan = {
        "input_sha256": {"native_states": "a" * 64},
        "s2_result_sha256": "b" * 64,
        "expected_minimum_native_rows": 600,
        "thresholds": _thresholds(),
    }
    worker = {
        "scope": "g6c-s3-state-reconstruction-worker",
        "input_sha256": plan["input_sha256"],
        "s2_result_sha256": plan["s2_result_sha256"],
        "native_grid_exact": True,
        "native_sample_count": 765,
        "diagnosis": {
            "force_identity_pass": True,
            "moment_identity_pass": True,
            "native_mechanics_closed": False,
        },
        "constraint_integrity_pass": True,
        "native_state_consistency_pass": True,
        "body_angular_momentum_identity_pass": True,
    }
    result = evaluate_state_reconstruction(plan, worker)
    assert result["diagnostic_complete"] is True
    assert result["pass"] is False
    assert result["next_gate"] == "blocked-at-g6c-s3"
    worker["diagnosis"]["native_mechanics_closed"] = True
    passed = evaluate_state_reconstruction(plan, worker)
    assert passed["pass"] is True
    assert passed["next_gate"] == "g6d"
