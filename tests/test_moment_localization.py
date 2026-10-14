from __future__ import annotations

import numpy as np
import pytest

from harmo.moment_localization import (
    classify_moment_localization,
    evaluate_moment_localization,
    moment_component_metrics,
    tracking_error_metrics,
    waveform_associations,
)


def _thresholds():
    return {
        "residual_moment_fraction": 0.01,
        "minimum_dominant_axis_energy_share": 0.50,
        "minimum_bias_energy_share": 0.50,
        "minimum_vertical_free_mean_opposition_fraction": 0.80,
        "maximum_pass_rms_relative_change": 0.10,
    }


def test_moment_components_separate_mean_and_zero_mean_energy():
    values = np.asarray([[3.0, 4.0, 0.0], [3.0, 6.0, 0.0]])
    result = moment_component_metrics(values, reference_nm=100.0)
    assert result["axis_mean_nm"] == pytest.approx([3.0, 5.0, 0.0])
    assert result["axis_zero_mean_rms_nm"] == pytest.approx([0.0, 1.0, 0.0])
    assert result["vector_rms_nm"] ** 2 == pytest.approx(
        result["mean_vector_magnitude_nm"] ** 2
        + result["zero_mean_vector_rms_nm"] ** 2
    )
    assert sum(result["axis_energy_share"]) == pytest.approx(1.0)


def test_tracking_metrics_keep_radians_and_metres_separate():
    result = tracking_error_metrics(
        {
            "pelvis_tilt": [0.0, np.pi / 180.0],
            "pelvis_tx": [0.0, 0.002],
            "pelvis_ty": [0.0, 0.004],
            "pelvis_tz": [0.0, 0.006],
        },
        translational_coordinates=["pelvis_tx", "pelvis_ty", "pelvis_tz"],
    )
    assert result["rotational_coordinate_rms_deg"]["pelvis_tilt"] == pytest.approx(
        1.0 / np.sqrt(2.0)
    )
    assert result["translational_coordinate_rms_mm"]["pelvis_tz"] == pytest.approx(
        6.0 / np.sqrt(2.0)
    )


def test_waveform_association_is_labeled_noncausal_and_thresholded():
    base = np.asarray([-2.0, -1.0, 0.0, 1.0, 2.0])
    moments = np.column_stack([base, base[::-1], np.asarray([0, 1, 0, 1, 0])])
    result = waveform_associations(
        moments,
        {"same": base, "weak": np.asarray([0, 0, 1, 0, 0])},
        minimum_absolute_correlation=0.7,
    )
    assert result["interpretation"] == "association-only-not-causal-attribution"
    assert result["axes"]["x"]["maximum"]["coordinate"] == "same"
    assert result["axes"]["x"]["maximum"]["correlation"] == pytest.approx(1.0)
    assert len(result["axes"]["x"]["prespecified_associations"]) == 1


def test_classifier_retains_multifactor_failure_after_single_factor_removal():
    pass1 = {
        "vector_rms_nm": 24.0,
        "fraction": 0.030,
        "axis_energy_share": [0.40, 0.58, 0.02],
        "mean_energy_share": 0.72,
        "zero_mean_fraction": 0.015,
    }
    pass2 = dict(pass1, vector_rms_nm=23.5, fraction=0.029)
    without_free = {"fraction": 0.022}
    result = classify_moment_localization(
        pass1=pass1,
        pass2=pass2,
        without_free_moment=without_free,
        vertical_free_mean_opposition_fraction=0.97,
        source_local_pass=True,
        thresholds=_thresholds(),
    )
    assert result["classification"] == (
        "bias-dominated-multifactor-rra-moment-failure"
    )
    assert result["mechanics_closed"] is False
    assert result["dominant_axis"] == "y"
    assert result["zero_mean_residual_still_fails"] is True
    assert result["free_moment_removal_still_fails"] is True


def test_evaluator_separates_complete_localization_from_mechanics_pass():
    plan = {
        "input_sha256": {"s3_timeseries": "a" * 64},
        "s3_result_sha256": "b" * 64,
        "expected_sample_count": 651,
        "thresholds": _thresholds(),
        "claim_boundary": "localization only",
    }
    worker = {
        "scope": "g6c-s4-moment-localization-worker",
        "input_sha256": plan["input_sha256"],
        "s3_result_sha256": plan["s3_result_sha256"],
        "sample_count": 651,
        "target_window_source_equivalence": {"pass": True},
        "pass_artifact_grids_exact": True,
        "s3_moment_reproduced": True,
        "decomposition_identity_pass": True,
        "diagnosis": {"mechanics_closed": False},
    }
    result = evaluate_moment_localization(plan, worker)
    assert result["diagnostic_complete"] is True
    assert result["pass"] is False
    assert result["next_gate"] == "blocked-at-g6c-s4"
    worker["diagnosis"]["mechanics_closed"] = True
    assert evaluate_moment_localization(plan, worker)["pass"] is True
