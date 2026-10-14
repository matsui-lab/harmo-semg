from pathlib import Path

import numpy as np
import pytest

from harmo.structural_adequacy import (
    build_stride_intervals,
    classify_structural_adequacy,
    compute_upper_limb_orbital_proxy,
    detect_sustained_contacts,
    evaluate_structural_adequacy,
    inspect_full_body_c3d,
    required_periodic_angular_momentum,
    summarize_study31_strides,
)


ROOT = Path(__file__).resolve().parents[1]


def test_sustained_contacts_reject_filter_ringing_and_short_pulses():
    time = np.arange(0, 2.001, 0.001)
    force = np.zeros_like(time)
    force[(time >= 0.10) & (time <= 0.13)] = 100.0
    force[(time >= 0.40) & (time <= 0.70)] = 500.0
    force[(time >= 1.10) & (time <= 1.40)] = 500.0
    observed = detect_sustained_contacts(
        time,
        force,
        threshold_n=20.0,
        minimum_duration_s=0.15,
        cutoff_hz=20.0,
        filter_order=4,
        window_s=(0.0, 2.0),
    )
    assert observed["residual_blind"] is True
    assert len(observed["contacts"]) == 2
    assert observed["contacts"][0]["start_s"] == pytest.approx(0.39, abs=0.03)
    assert observed["contacts"][1]["start_s"] == pytest.approx(1.09, abs=0.03)


def test_stride_intervals_use_consecutive_same_side_contacts():
    contacts = [{"start_s": value} for value in (0.1, 0.75, 1.4, 2.5)]
    strides = build_stride_intervals(
        contacts, minimum_stride_s=0.5, maximum_stride_s=0.8
    )
    assert [value["duration_s"] for value in strides] == pytest.approx(
        [0.65, 0.65]
    )


def test_required_momentum_separates_bias_and_closes_periodic_component():
    time = np.linspace(0.0, 1.0, 1001)
    moment = np.zeros((time.size, 3))
    moment[:, 1] = 10.0 + 4.0 * np.sin(2.0 * np.pi * time)
    observed = required_periodic_angular_momentum(time, moment)
    assert observed["mean_moment_nm"][1] == pytest.approx(10.0, abs=1e-10)
    assert observed["raw_angular_impulse_nms"][1] == pytest.approx(10.0)
    assert np.max(np.abs(observed["periodic_closure_error_nms"])) < 1e-12
    assert np.ptp(
        observed["required_periodic_angular_momentum_nms"][:, 1]
    ) == pytest.approx(4.0 / np.pi, rel=2e-4)


def test_stride_summary_recovers_repeatable_vertical_waveform():
    time = np.arange(0.0, 2.001, 0.001)
    moment = np.zeros((time.size, 3))
    moment[:, 1] = 8.0 + 5.0 * np.sin(2.0 * np.pi * time)
    strides = [
        {"stride_id": "a", "start_s": 0.0, "end_s": 1.0},
        {"stride_id": "b", "start_s": 1.0, "end_s": 2.0},
    ]
    observed, rows = summarize_study31_strides(
        time,
        moment,
        strides,
        phase_points=101,
        sample_interval_s=0.001,
    )
    assert observed["stride_count"] == 2
    assert observed["pairwise_phase_correlations"]["periodic_moment"]["y"][
        0
    ] == pytest.approx(1.0)
    assert observed["pairwise_phase_correlations"][
        "required_angular_momentum"
    ]["y"][0] == pytest.approx(1.0)
    assert len(rows) == 202


def _synthetic_upper_limb_markers(time):
    zeros = np.zeros((len(time), 3))
    values = {"CentreOfMass": zeros.copy()}
    for side, sign in (("L", -1.0), ("R", 1.0)):
        angle = 2.0 * np.pi * time
        shoulder = np.column_stack(
            (
                0.1 * np.sin(angle),
                np.full(len(time), sign * 0.2),
                0.1 * np.cos(angle),
            )
        )
        values[side + "SHO"] = shoulder
        values[side + "ELB"] = shoulder + np.array([0.0, 0.0, -0.3])
        values[side + "WRA"] = shoulder + np.array([0.0, 0.0, -0.55])
        values[side + "WRB"] = shoulder + np.array([0.0, 0.02, -0.55])
        values[side + "FIN"] = shoulder + np.array([0.1, 0.01, -0.65])
    return values


def test_upper_limb_proxy_is_finite_and_explicitly_omits_spin():
    time = np.arange(0.0, 2.001, 0.005)
    report, rows = compute_upper_limb_orbital_proxy(
        time,
        _synthetic_upper_limb_markers(time),
        subject_mass_kg=60.0,
        reference_total_mass_kg=75.0,
        reference_segment_masses_kg={
            "humerus": 2.0,
            "forearm": 1.2,
            "hand": 0.45,
        },
        cutoff_hz=6.0,
        filter_order=4,
        edge_exclusion_s=0.1,
    )
    assert report["spin_angular_momentum_included"] is False
    assert report["scaled_bilateral_upper_limb_mass_kg"] == pytest.approx(5.84)
    assert report["retained_sample_count"] == len(rows)
    assert np.all(np.isfinite(report["orbital_torque_axis_rms_nm"]))


def test_classifier_keeps_observational_plausibility_noncausal():
    study31 = {
        "stride_count": 2,
        "pairwise_phase_correlations": {
            "periodic_moment": {"y": [0.91]},
            "required_angular_momentum": {"y": [0.98]},
        },
        "vertical_required_angular_momentum_peak_to_peak_cv": 0.1,
        "vertical_required_angular_momentum_peak_to_peak_mean_nms": 3.0,
        "vertical_periodic_moment_rms_mean_nm": 12.0,
        "strides": [
            {"periodic_closure_error_nms": [0.0, 0.0, 0.0]},
            {"periodic_closure_error_nms": [0.0, 0.0, 0.0]},
        ],
    }
    study1 = {
        "full_body_markers_ready": True,
        "external_load_roundtrip_ready": True,
        "paired_full_body_rra_ready": False,
        "upper_limb_proxy": {
            "retained_sample_count": 100,
            "angular_momentum_peak_to_peak_nms": [0.0, 0.0, 1.8],
            "orbital_torque_axis_rms_nm": [0.0, 0.0, 3.5],
        },
    }
    observed = classify_structural_adequacy(
        study31,
        study1,
        thresholds={
            "minimum_complete_strides": 2,
            "minimum_vertical_phase_correlation": 0.8,
            "maximum_vertical_peak_to_peak_cv": 0.3,
            "maximum_periodic_closure_error_nms": 1e-10,
            "descriptive_same_order_ratio_minimum": 0.25,
            "descriptive_same_order_ratio_maximum": 4.0,
        },
    )
    assert observed["study31_repeatability_pass"] is True
    assert observed["structural_explanation_status"] == "plausible-not-identified"
    assert observed["paired_full_body_rra_ready"] is False
    assert observed["mechanics_closed"] is False
    assert observed["descriptive_cross_dataset_comparison"]["causal_test"] is False


def test_evaluator_never_promotes_plausibility_to_mechanics_pass():
    plan = {
        "input_sha256": {"a": "b" * 64},
        "prior_result_sha256": {"s2": "c" * 64, "s5": "d" * 64},
        "thresholds": {"residual_moment_fraction": 0.01},
        "claim_boundary": "descriptive only",
    }
    worker = {
        "scope": "g6c-s6-structural-adequacy-worker",
        "input_sha256": plan["input_sha256"],
        "prior_result_sha256": plan["prior_result_sha256"],
        "baseline_actuation_verified": True,
        "contact_selection_residual_blind": True,
        "study31_analysis_complete": True,
        "study1_analysis_complete": True,
        "artifacts_complete": True,
        "diagnosis": {"structural_explanation_status": "plausible-not-identified"},
    }
    observed = evaluate_structural_adequacy(plan, worker)
    assert observed["diagnostic_complete"] is True
    assert observed["pass"] is False
    assert observed["mechanics_closed"] is False
    assert observed["next_gate"] == (
        "g6c-s6b-paired-full-body-structural-contrast"
    )


def test_repository_study1_c3d_has_full_body_markers_but_no_plate_geometry():
    pytest.importorskip("ezc3d")
    path = ROOT / "studies/study_1/notebook/Ref01_sss_le_shoes05.c3d"
    report, markers, time = inspect_full_body_c3d(
        path,
        marker_groups={
            "arms": [
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
            ]
        },
        calculated_markers=["CentreOfMass"],
        minimum_valid_fraction=0.99,
        expected_analog_channels=["Force.Fx1", "Moment.Mz3"],
        maximum_platform_roundtrip_error_nmm=1e-6,
    )
    assert report["full_body_markers_ready"] is True
    assert report["raw_force_moment_analog_ready"] is True
    assert report["force_platform_geometry_ready"] is True
    assert report["external_load_roundtrip_ready"] is True
    assert max(
        value["origin_moment_roundtrip_maximum_error_nmm"]
        for value in report["force_platform_reports"]
    ) < 1e-6
    assert markers["CentreOfMass"].shape == (len(time), 3)
