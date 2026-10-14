import numpy as np
import pytest

from harmo.structural_contrast import (
    classify_structural_contrast,
    evaluate_structural_contrast,
    paired_upper_limb_structural_contrast,
)


def _synthetic_markers(time):
    count = len(time)
    origin = np.zeros((count, 3))
    markers = {
        "CentreOfMass": origin.copy(),
        "TRXO": origin.copy(),
        "TRXA": origin + np.array([1.0, 0.0, 0.0]),
        "TRXL": origin + np.array([0.0, 1.0, 0.0]),
        "TRXP": origin + np.array([0.0, 0.0, 1.0]),
    }
    motion = 0.10 * np.sin(2.0 * np.pi * time)
    for side, lateral, direction in (
        ("L", -0.20, 1.0),
        ("R", 0.20, -1.0),
    ):
        shoulder = np.column_stack(
            (
                direction * motion,
                np.full(count, lateral),
                np.full(count, 0.45),
            )
        )
        markers[side + "SHO"] = shoulder
        markers[side + "ELB"] = shoulder + np.array([0.0, 0.0, -0.25])
        markers[side + "WRA"] = shoulder + np.array([0.0, -0.01, -0.50])
        markers[side + "WRB"] = shoulder + np.array([0.0, 0.01, -0.50])
        markers[side + "FIN"] = shoulder + np.array([0.08, 0.0, -0.58])
    return markers


def _contrast():
    time = np.arange(0.0, 2.005, 0.005)
    return paired_upper_limb_structural_contrast(
        time,
        _synthetic_markers(time),
        subject_mass_kg=60.0,
        reference_total_mass_kg=75.0,
        reference_segment_masses_kg={
            "humerus": 2.0,
            "forearm": 1.2,
            "hand": 0.45,
        },
        reference_frame_rule="middle-sample",
        cutoff_hz=6.0,
        filter_order=4,
        edge_exclusion_s=0.1,
    )


def _thresholds():
    return {
        "maximum_torso_orthonormality_error": 1e-12,
        "maximum_reference_pose_error_m": 1e-12,
        "maximum_mass_difference_kg": 1e-12,
        "maximum_whole_body_com_vector_rms_m": 1e-12,
        "maximum_whole_body_com_maximum_m": 1e-12,
        "minimum_vertical_momentum_contrast_ratio": 0.99,
        "minimum_vertical_torque_contrast_ratio": 0.99,
    }


def test_paired_counterfactual_preserves_mass_and_reference_pose():
    observed, rows = _contrast()
    assert observed["mass"]["total_mass_preserved"] is True
    assert observed["mass"]["mass_difference_kg"] == 0.0
    assert (
        observed["reference_frame"]["maximum_segment_position_error_m"]
        < 1e-15
    )
    assert (
        observed["whole_body_com_perturbation"]["reference_pose_norm_m"]
        < 1e-15
    )
    assert observed["processing"]["retained_sample_count"] == len(rows)


def test_paired_counterfactual_recovers_material_articulation_effect():
    observed, _ = _contrast()
    assert observed["contrast"][
        "vertical_angular_momentum_peak_to_peak_ratio"
    ] == pytest.approx(1.0, abs=1e-10)
    assert observed["contrast"][
        "vertical_orbital_torque_rms_ratio"
    ] == pytest.approx(1.0, abs=1e-10)
    assert observed["whole_body_com_perturbation"]["maximum_norm_m"] < 1e-15
    diagnosis = classify_structural_contrast(
        observed, thresholds=_thresholds()
    )
    assert diagnosis["counterfactual_valid"] is True
    assert diagnosis["material_effect_detected"] is True
    assert diagnosis["study31_cause_identified"] is False
    assert diagnosis["opensim_rra_contrast_completed"] is False


def test_paired_counterfactual_rejects_unidentifiable_vertical_signal():
    time = np.arange(0.0, 2.005, 0.005)
    markers = _synthetic_markers(time)
    for name in (
        "LSHO",
        "LELB",
        "LWRA",
        "LWRB",
        "LFIN",
        "RSHO",
        "RELB",
        "RWRA",
        "RWRB",
        "RFIN",
    ):
        markers[name][:, 0] = markers[name][0, 0]
    with pytest.raises(ValueError, match="vertical signal is not identifiable"):
        paired_upper_limb_structural_contrast(
            time,
            markers,
            subject_mass_kg=60.0,
            reference_total_mass_kg=75.0,
            reference_segment_masses_kg={
                "humerus": 2.0,
                "forearm": 1.2,
                "hand": 0.45,
            },
            reference_frame_rule="middle-sample",
            cutoff_hz=6.0,
            filter_order=4,
            edge_exclusion_s=0.1,
        )


def test_evaluator_never_promotes_observational_contrast_to_mechanics_pass():
    plan = {
        "input_sha256": {"study1_c3d": "a" * 64},
        "prior_result_sha256": {"s6": "b" * 64},
        "thresholds": {"minimum_vertical_momentum_contrast_ratio": 0.5},
        "claim_boundary": "observational only",
    }
    worker = {
        "scope": "g6c-s6b-structural-contrast-worker",
        "input_sha256": plan["input_sha256"],
        "prior_result_sha256": plan["prior_result_sha256"],
        "analysis_complete": True,
        "artifacts_complete": True,
        "diagnosis": {
            "counterfactual_valid": True,
            "material_effect_detected": True,
        },
    }
    observed = evaluate_structural_contrast(plan, worker)
    assert observed["diagnostic_complete"] is True
    assert observed["counterfactual_valid"] is True
    assert observed["material_effect_detected"] is True
    assert observed["pass"] is False
    assert observed["mechanics_closed"] is False
    assert observed["study31_cause_identified"] is False
    assert observed["next_gate"] == (
        "g6c-s6c-subject-activity-matched-full-body-rra"
    )
