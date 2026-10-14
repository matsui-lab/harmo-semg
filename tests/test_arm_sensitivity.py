import numpy as np
import pandas as pd
import pytest

from harmo.arm_sensitivity import (
    classify_bounded_sensitivity,
    evaluate_arm_sensitivity,
    harmonic_candidate,
    run_bounded_harmonic_sensitivity,
)


def test_harmonic_candidate_has_requested_amplitude_and_zero_mean():
    phase = np.linspace(0.0, 1.0, 101)
    momentum, torque = harmonic_candidate(
        phase,
        duration_s=0.65,
        reference_peak_to_peak_nms=2.0,
        amplitude_multiplier=1.0,
        phase_shift_deg=180.0,
    )
    assert np.ptp(momentum) == 2.0
    assert abs(np.trapz(momentum, phase)) < 1e-12
    assert np.all(np.isfinite(torque))


def _synthetic_stride_table():
    rows = []
    phase = np.linspace(0.0, 1.0, 101)
    for stride_id, duration in (("s1", 0.65), ("s2", 0.70)):
        momentum, torque = harmonic_candidate(
            phase,
            duration_s=duration,
            reference_peak_to_peak_nms=2.0,
            amplitude_multiplier=1.0,
            phase_shift_deg=180.0,
        )
        for index in range(phase.size):
            rows.append(
                {
                    "stride_id": stride_id,
                    "phase_percent": 100.0 * phase[index],
                    "h": momentum[index],
                    "t": torque[index],
                }
            )
    return pd.DataFrame(rows)


def test_cross_stride_grid_recovers_known_common_harmonic():
    summary, cells, waveforms = run_bounded_harmonic_sensitivity(
        _synthetic_stride_table(),
        strides=[
            {"stride_id": "s1", "duration_s": 0.65},
            {"stride_id": "s2", "duration_s": 0.70},
        ],
        reference_peak_to_peak_nms=2.0,
        amplitude_multipliers=[0.0, 0.5, 1.0, 1.5],
        phase_shifts_deg=[0, 45, 90, 135, 180, 225, 270, 315],
        phase_points=101,
        momentum_column="h",
        torque_column="t",
    )
    assert summary["cell_count"] == 25
    assert len(cells) == 50
    assert len(waveforms) == 25 * 2 * 101
    assert summary["pooled_best_cell"]["cell_id"] == "amp-1__phase-180"
    assert all(
        value["selected_cell_id"] == "amp-1__phase-180"
        for value in summary["cross_stride_evaluations"]
    )
    assert summary[
        "heldout_momentum_rms_relative_reduction_minimum"
    ] == pytest.approx(1.0)
    assert summary[
        "heldout_torque_rms_relative_reduction_minimum"
    ] == pytest.approx(1.0)


def test_descriptive_classifier_separates_momentum_from_torque():
    observed = classify_bounded_sensitivity(
        {
            "all_cells_retained": True,
            "heldout_momentum_rms_relative_reduction_minimum": 0.4,
            "heldout_torque_rms_relative_reduction_minimum": 0.1,
            "selected_amplitude_span": 0.5,
            "selected_circular_phase_span_deg": 0.0,
        },
        descriptive_thresholds={
            "momentum_relative_reduction_reference": 0.25,
            "torque_relative_reduction_reference": 0.25,
            "maximum_selected_amplitude_span": 0.5,
            "maximum_selected_circular_phase_span_deg": 45.0,
        },
    )
    assert observed["descriptive_pattern"] == (
        "momentum-compatible-torque-not-compatible"
    )
    assert observed["descriptive_checks"][
        "cross_stride_momentum_reference"
    ] is True
    assert observed["descriptive_checks"]["cross_stride_torque_reference"] is False
    assert observed["validation_pass"] is False


def test_evaluator_never_promotes_sensitivity_to_causal_pass():
    plan = {
        "input_sha256": {"table": "a" * 64},
        "prior_result_sha256": {"s6c": "b" * 64},
        "post_outcome_status": "exploratory",
        "claim_boundary": "noncausal",
    }
    worker = {
        "scope": "g6c-s6d-bounded-arm-sensitivity-worker",
        "input_sha256": plan["input_sha256"],
        "prior_result_sha256": plan["prior_result_sha256"],
        "analysis_complete": True,
        "artifacts_complete": True,
    }
    observed = evaluate_arm_sensitivity(plan, worker)
    assert observed["diagnostic_complete"] is True
    assert observed["pass"] is False
    assert observed["causal_structural_attribution"] is False
    assert observed["full_body_rra_authorized"] is False
    assert observed["next_gate"] == (
        "stop-g6c-structural-causal-branch-requires-new-observed-data"
    )
