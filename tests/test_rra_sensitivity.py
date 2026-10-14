from __future__ import annotations

from pathlib import Path

import numpy as np
import pandas as pd
import pytest

from harmo.ingest.readers.sto import read_opensim_sto_trc
from harmo.rra_sensitivity import (
    classify_rra_sensitivity,
    copy_task_weights,
    distribute_total_vertical_free_moment,
    evaluate_rra_sensitivity,
    filter_public_side_wrenches,
    modify_task_weights,
    read_opensim_column_spellings,
    summarize_actuator_window,
    write_opensim_mot,
    zero_free_moment,
)


def _wrench_frame(rows: int = 200) -> pd.DataFrame:
    time = np.arange(rows, dtype=float) / 100.0
    frame = pd.DataFrame({"time": time})
    for side, prefix in enumerate(("l", "r")):
        force = np.column_stack(
            [
                10.0 * np.sin(2.0 * np.pi * time),
                np.full(rows, 300.0 + 25.0 * side),
                5.0 * np.cos(2.0 * np.pi * time),
            ]
        )
        point = np.column_stack(
            [np.full(rows, -0.1 + 0.2 * side), np.zeros(rows), 0.02 * time]
        )
        free = np.column_stack(
            [np.zeros(rows), 2.0 * np.sin(np.pi * time), np.zeros(rows)]
        )
        for axis, label in enumerate("xyz"):
            frame[f"{prefix}_ground_force_v{label}"] = force[:, axis]
            frame[f"{prefix}_ground_force_p{label}"] = point[:, axis]
            frame[f"{prefix}_ground_torque_{label}"] = free[:, axis]
    return frame


def _thresholds() -> dict:
    return {
        "expected_cell_count": 8,
        "residual_force_fraction": 0.05,
        "residual_moment_fraction": 0.01,
        "rra_angle_change_rms_deg": 5.0,
        "minimum_meaningful_relative_reduction": 0.25,
    }


def _cells() -> dict:
    cells = {}
    for external in (
        "published_original",
        "published_wrench_6hz",
        "raw_free_6hz",
        "zero_free",
    ):
        for tracking in ("standard", "frontal_relaxed_5x"):
            cell_id = f"{external}__{tracking}"
            cells[cell_id] = {
                "admissible_for_mechanics_rescue": external
                not in {"raw_free_6hz", "zero_free"},
                "residuals": {
                    "force": {"fraction": 0.02},
                    "moment": {"fraction": 0.02},
                },
                "rra_angle_change_rms_deg": 2.0,
            }
    return cells


def test_mot_writer_round_trips_deterministically(tmp_path: Path):
    source = _wrench_frame(20)
    first = tmp_path / "first.mot"
    second = tmp_path / "second.mot"
    write_opensim_mot(source, first, name="synthetic")
    write_opensim_mot(source, second, name="synthetic")
    _, observed = read_opensim_sto_trc(str(first))
    assert first.read_bytes() == second.read_bytes()
    assert observed.to_numpy(float) == pytest.approx(source.to_numpy(float))


def test_mot_writer_restores_source_column_case_for_opensim(tmp_path: Path):
    source = _wrench_frame(20)
    spellings = {
        column: (
            column
            if column == "time"
            else column[0].upper() + column[1:]
        )
        for column in source.columns
    }
    output = tmp_path / "case_preserved.mot"
    write_opensim_mot(
        source,
        output,
        name="case_preserved",
        column_spellings=spellings,
    )
    observed = read_opensim_column_spellings(output)
    assert observed["l_ground_force_vx"] == "L_ground_force_vx"
    assert observed["r_ground_torque_z"] == "R_ground_torque_z"


def test_side_wrench_filter_preserves_reconstructed_origin_wrench():
    filtered, report = filter_public_side_wrenches(
        _wrench_frame(),
        prefixes=("l", "r"),
        vertical_axis=1,
        cutoff_hz=6.0,
        order=4,
        contact_threshold_n=20.0,
    )
    assert np.all(np.isfinite(filtered.to_numpy(float)))
    assert report["maximum_origin_wrench_roundtrip_error_nm"] < 1e-10
    assert all(
        side["contact_sample_count"] == len(filtered)
        for side in report["side_reports"].values()
    )


def test_combined_free_moment_allocation_is_exact_and_marked_constructed():
    source = _wrench_frame(20)
    target = np.linspace(-3.0, 4.0, len(source))
    observed, report = distribute_total_vertical_free_moment(
        source,
        target,
        prefixes=("l", "r"),
        vertical_axis=1,
        contact_threshold_n=20.0,
    )
    combined = (
        observed["l_ground_torque_y"] + observed["r_ground_torque_y"]
    ).to_numpy(float)
    assert combined == pytest.approx(target)
    assert report["maximum_total_reconstruction_error_nm"] < 1e-12
    assert report["side_resolution"] == "constructed-not-observed"


def test_zero_free_moment_changes_only_torque_columns():
    source = _wrench_frame(20)
    observed, report = zero_free_moment(source, prefixes=("l", "r"))
    torque = [column for column in source if "ground_torque" in column]
    untouched = [column for column in source if column not in torque]
    assert np.max(np.abs(observed[torque].to_numpy(float))) == 0.0
    pd.testing.assert_frame_equal(observed[untouched], source[untouched])
    assert report["role"] == "negative-control-only-physically-invalid"


def test_task_weight_variants_are_exact_and_auditable(tmp_path: Path):
    source = tmp_path / "tasks.xml"
    source.write_text(
        """<?xml version='1.0'?>
<OpenSimDocument><CMC_TaskSet><objects>
<CMC_Joint name='a'><coordinate>pelvis_list</coordinate><weight>10</weight></CMC_Joint>
<CMC_Joint name='b'><coordinate>lumbar_bending</coordinate><weight>5</weight></CMC_Joint>
<CMC_Joint name='c'><coordinate>hip_flexion_r</coordinate><weight>7</weight></CMC_Joint>
</objects></CMC_TaskSet></OpenSimDocument>
""",
        encoding="utf-8",
    )
    copied = tmp_path / "standard.xml"
    relaxed = tmp_path / "relaxed.xml"
    copy_report = copy_task_weights(source, copied)
    report = modify_task_weights(
        source,
        relaxed,
        coordinates=("pelvis_list", "lumbar_bending"),
        multiplier=0.2,
    )
    assert copy_report["byte_identical"] is True
    assert report["coordinates"]["pelvis_list"] == {"before": 10.0, "after": 2.0}
    assert report["coordinates"]["lumbar_bending"] == {
        "before": 5.0,
        "after": 1.0,
    }
    assert "<weight>7</weight>" in relaxed.read_text(encoding="utf-8")


def test_actuator_summary_uses_vector_rms_and_frozen_references():
    force = np.asarray([[3.0, 4.0, 0.0], [3.0, 4.0, 0.0]])
    moment = np.asarray([[0.0, 0.0, 2.0], [0.0, 0.0, 2.0]])
    result = summarize_actuator_window(
        force, moment, body_weight_n=100.0, moment_reference_nm=20.0
    )
    assert result["force"]["vector_rms"] == pytest.approx(5.0)
    assert result["force"]["fraction"] == pytest.approx(0.05)
    assert result["moment"]["fraction"] == pytest.approx(0.1)


def test_classifier_separates_admissible_and_diagnostic_only_rescues():
    cells = _cells()
    cells["published_wrench_6hz__standard"]["residuals"]["moment"][
        "fraction"
    ] = 0.009
    cells["zero_free__standard"]["residuals"]["moment"]["fraction"] = 0.005
    result = classify_rra_sensitivity(
        cells,
        baseline_cell="published_original__standard",
        thresholds=_thresholds(),
        baseline_reproduced=True,
    )
    assert result["classification"] == (
        "source-faithful-sensitivity-rescue-requires-confirmation"
    )
    assert result["admissible_rescue_cells"] == [
        "published_wrench_6hz__standard"
    ]
    assert result["diagnostic_only_rescue_cells"] == ["zero_free__standard"]
    assert result["selected_pipeline"] is None


def test_classifier_retains_failed_cells_and_disallows_partial_rescue():
    cells = _cells()
    cells["published_wrench_6hz__standard"]["residuals"]["moment"][
        "fraction"
    ] = 0.009
    failed = cells["published_original__frontal_relaxed_5x"]
    failed["status"] = "failed"
    failed["failure"] = {"stage": "rra-pass-1"}
    failed.pop("residuals")
    failed.pop("rra_angle_change_rms_deg")
    result = classify_rra_sensitivity(
        cells,
        baseline_cell="published_original__standard",
        thresholds=_thresholds(),
        baseline_reproduced=True,
    )
    assert result["classification"] == "partial-nonconvergence-rescue-not-eligible"
    assert result["failed_cells"] == [
        "published_original__frontal_relaxed_5x"
    ]
    assert result["complete_factorial"] is False
    assert result["sensitivity_rescue_candidate"] is False
    assert result["cell_checks"][
        "published_original__frontal_relaxed_5x"
    ]["failure"] == {"stage": "rra-pass-1"}


def test_evaluator_fails_closed_when_baseline_is_not_reproduced():
    plan = {
        "input_sha256": {"model": "a" * 64},
        "prior_result_sha256": {"s4": "b" * 64},
        "thresholds": _thresholds(),
        "claim_boundary": "sensitivity only",
    }
    worker = {
        "scope": "g6c-s5-rra-sensitivity-worker",
        "input_sha256": plan["input_sha256"],
        "prior_result_sha256": plan["prior_result_sha256"],
        "opensim_version_pass": True,
        "all_cells_complete": True,
        "baseline_reproduced": False,
        "external_transform_integrity_pass": True,
        "tracking_transform_integrity_pass": True,
        "target_grids_exact": True,
        "diagnosis": {"sensitivity_rescue_candidate": True},
    }
    result = evaluate_rra_sensitivity(plan, worker)
    assert result["diagnostic_complete"] is False
    assert result["pass"] is False
    assert result["next_gate"] == "blocked-at-g6c-s5"
