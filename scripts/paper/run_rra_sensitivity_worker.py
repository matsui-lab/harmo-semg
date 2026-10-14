#!/usr/bin/env python3
"""Run the checksum-bound G6c-S5 two-factor RRA sensitivity experiment."""

from __future__ import annotations

import argparse
import importlib.util
import json
import sys
from pathlib import Path

import numpy as np
import pandas as pd


ROOT = Path(__file__).resolve().parents[2]
if str(ROOT / "src") not in sys.path:
    sys.path.insert(0, str(ROOT / "src"))

from harmo.dynamic_calibration import sha256_file  # noqa: E402
from harmo.g6_execution import (  # noqa: E402
    _rra_actuator_columns,
    rra_angle_change_metrics,
)
from harmo.ingest.readers.sto import read_opensim_sto_trc  # noqa: E402
from harmo.mechanics_audit import resample_storage_to_grid, verify_exact_grid  # noqa: E402
from harmo.rra_sensitivity import (  # noqa: E402
    classify_rra_sensitivity,
    copy_task_weights,
    distribute_total_vertical_free_moment,
    filter_public_side_wrenches,
    modify_task_weights,
    read_opensim_column_spellings,
    summarize_actuator_window,
    write_opensim_mot,
    zero_free_moment,
)
from harmo.whole_body_balance import zero_phase_lowpass  # noqa: E402


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--plan", required=True)
    parser.add_argument("--work-dir", required=True)
    parser.add_argument("--report", required=True)
    return parser


def _load_legacy_worker():
    path = ROOT / "scripts/paper/run_dynamic_mechanics_worker.py"
    spec = importlib.util.spec_from_file_location("harmo_g6cs5_legacy_worker", str(path))
    if spec is None or spec.loader is None:
        raise RuntimeError("cannot load the OpenSim RRA worker")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _numeric_table(path: Path) -> pd.DataFrame:
    _, table = read_opensim_sto_trc(str(path))
    numeric = table.apply(pd.to_numeric, errors="coerce")
    if "time" not in numeric:
        raise ValueError("G6c-S5 storage lacks time")
    time = numeric["time"].to_numpy(float)
    if (
        time.size == 0
        or np.any(np.diff(time) <= 0)
        or not np.all(np.isfinite(numeric.to_numpy(float)))
    ):
        raise ValueError("G6c-S5 storage must be finite and ordered")
    return numeric


def _exact_native_window(
    table: pd.DataFrame, start: float, end: float
) -> tuple[pd.DataFrame, np.ndarray]:
    target = np.arange(round(start * 1000.0), round(end * 1000.0) + 1) / 1000.0
    source_time = table["time"].to_numpy(float)
    indices = np.searchsorted(source_time, target)
    if (
        np.any(indices >= source_time.size)
        or not np.allclose(source_time[indices], target, atol=1e-10, rtol=0.0)
    ):
        raise ValueError("G6c-S5 actuation does not contain the 1-ms target grid")
    return table.iloc[indices].reset_index(drop=True), target


def _raw_vertical_free_moment(
    path: Path, target_time: np.ndarray, config: dict, vertical_axis: int
) -> tuple[np.ndarray, dict]:
    import ezc3d

    c3d = ezc3d.c3d(str(path), extract_forceplat_data=True)
    platforms = c3d["data"].get("platform", [])
    index = int(config["platform_index_zero_based"])
    if index < 0 or index >= len(platforms):
        raise ValueError("G6c-S5 raw C3D platform index is out of range")
    platform = platforms[index]
    rotation = np.asarray(config["c3d_to_ground_rotation"], dtype=float)
    if rotation.shape != (3, 3) or not np.allclose(
        rotation.T @ rotation, np.eye(3), atol=1e-8, rtol=0.0
    ):
        raise ValueError("G6c-S5 raw C3D rotation must be orthonormal")
    unit = str(platform["unit_moment"]).strip().lower().replace(" ", "")
    scale = {"nm": 1.0, "n*m": 1.0, "nmm": 0.001, "n*mm": 0.001}.get(unit)
    if scale is None:
        raise ValueError("G6c-S5 raw C3D moment unit is unsupported")
    free = np.asarray(platform["Tz"], dtype=float).T * scale @ rotation.T
    rate = float(c3d["parameters"]["ANALOG"]["RATE"]["value"][0])
    source_time = (
        np.arange(free.shape[0], dtype=float) / rate
        + float(config["c3d_time_offset_s"])
    )
    for axis in range(3):
        finite = np.isfinite(free[:, axis])
        if int(np.sum(finite)) < 2:
            raise ValueError("G6c-S5 raw free moment is not recoverable")
        free[:, axis] = np.interp(
            source_time, source_time[finite], free[finite, axis]
        )
    filtered = zero_phase_lowpass(
        source_time,
        free,
        cutoff_hz=float(config["lowpass_cutoff_hz"]),
        order=int(config["lowpass_order"]),
    )
    aligned = np.interp(target_time, source_time, filtered[:, int(vertical_axis)])
    return aligned, {
        "operation": "raw-c3d-combined-free-moment-lowpass",
        "platform_index_zero_based": index,
        "rotation": rotation.tolist(),
        "time_offset_s": float(config["c3d_time_offset_s"]),
        "cutoff_hz": float(config["lowpass_cutoff_hz"]),
        "order": int(config["lowpass_order"]),
        "source_sha256": sha256_file(path),
        "aligned_sample_count": int(aligned.size),
    }


def _external_variants(
    plan: dict, work_dir: Path
) -> tuple[dict[str, Path], dict[str, dict]]:
    paths = {name: Path(value).resolve() for name, value in plan["input_paths"].items()}
    source_path = paths["ground_reaction"]
    source = _numeric_table(source_path)
    source_spellings = read_opensim_column_spellings(source_path)
    prefixes = [str(value).lower() for value in plan["public_prefixes"]]
    vertical_axis = int(plan["vertical_axis"])
    config = plan["external_source"]
    variant_dir = work_dir / "external_variants"
    variant_dir.mkdir(parents=True, exist_ok=True)
    variant_paths = {"published_original": source_path}
    reports = {
        "published_original": {
            "operation": "byte-identical-public-mot",
            "source_sha256": sha256_file(source_path),
            "output_sha256": sha256_file(source_path),
            "byte_identical": True,
        }
    }

    filtered, filtered_report = filter_public_side_wrenches(
        source,
        prefixes=prefixes,
        vertical_axis=vertical_axis,
        cutoff_hz=float(config["lowpass_cutoff_hz"]),
        order=int(config["lowpass_order"]),
        contact_threshold_n=float(config["contact_threshold_n"]),
    )
    filtered_path = variant_dir / "published_wrench_6hz.mot"
    write_opensim_mot(
        filtered,
        filtered_path,
        name="g6c_s5_published_wrench_6hz",
        column_spellings=source_spellings,
    )
    filtered_report["output_sha256"] = sha256_file(filtered_path)
    filtered_report["source_column_spellings_reproduced"] = (
        read_opensim_column_spellings(filtered_path) == source_spellings
    )
    variant_paths["published_wrench_6hz"] = filtered_path
    reports["published_wrench_6hz"] = filtered_report

    source_time = source["time"].to_numpy(float)
    raw_free, raw_report = _raw_vertical_free_moment(
        paths["raw_c3d"], source_time, config, vertical_axis
    )
    raw_variant, allocation_report = distribute_total_vertical_free_moment(
        source,
        raw_free,
        prefixes=prefixes,
        vertical_axis=vertical_axis,
        contact_threshold_n=float(config["contact_threshold_n"]),
    )
    raw_path = variant_dir / "raw_free_6hz.mot"
    write_opensim_mot(
        raw_variant,
        raw_path,
        name="g6c_s5_raw_free_6hz",
        column_spellings=source_spellings,
    )
    variant_paths["raw_free_6hz"] = raw_path
    reports["raw_free_6hz"] = {
        "raw_source": raw_report,
        "allocation": allocation_report,
        "output_sha256": sha256_file(raw_path),
        "source_column_spellings_reproduced": (
            read_opensim_column_spellings(raw_path) == source_spellings
        ),
    }

    zero_variant, zero_report = zero_free_moment(source, prefixes=prefixes)
    zero_path = variant_dir / "zero_free.mot"
    write_opensim_mot(
        zero_variant,
        zero_path,
        name="g6c_s5_zero_free",
        column_spellings=source_spellings,
    )
    variant_paths["zero_free"] = zero_path
    zero_report["output_sha256"] = sha256_file(zero_path)
    zero_report["source_column_spellings_reproduced"] = (
        read_opensim_column_spellings(zero_path) == source_spellings
    )
    variant_paths["zero_free"] = zero_path
    reports["zero_free"] = zero_report
    return variant_paths, reports


def _tracking_variants(
    plan: dict, work_dir: Path
) -> tuple[dict[str, Path], dict[str, dict]]:
    source = Path(plan["input_paths"]["rra_tasks"]).resolve()
    task_dir = work_dir / "tracking_variants"
    task_dir.mkdir(parents=True, exist_ok=True)
    paths = {}
    reports = {}
    by_id = {str(value["id"]): value for value in plan["tracking_conditions"]}
    standard = task_dir / "standard_tasks.xml"
    reports["standard"] = copy_task_weights(source, standard)
    paths["standard"] = standard
    relaxed_config = by_id["frontal_relaxed_5x"]
    relaxed = task_dir / "frontal_relaxed_5x_tasks.xml"
    reports["frontal_relaxed_5x"] = modify_task_weights(
        source,
        relaxed,
        coordinates=relaxed_config["coordinates"],
        multiplier=float(relaxed_config["multiplier"]),
    )
    paths["frontal_relaxed_5x"] = relaxed
    return paths, reports


def _effects(cells: dict[str, dict]) -> dict:
    external_ids = sorted({str(cell["external_condition"]) for cell in cells.values()})
    tracking_ids = sorted({str(cell["tracking_condition"]) for cell in cells.values()})
    tracking_effects = {}
    for external in external_ids:
        standard = cells["{}__standard".format(external)]
        relaxed = cells["{}__frontal_relaxed_5x".format(external)]
        if any(cell.get("status") != "completed" for cell in (standard, relaxed)):
            tracking_effects[external] = {
                "status": "unavailable-due-to-cell-failure",
                "failed_cells": [
                    cell_id
                    for cell_id in (
                        "{}__standard".format(external),
                        "{}__frontal_relaxed_5x".format(external),
                    )
                    if cells[cell_id].get("status") != "completed"
                ],
            }
            continue
        tracking_effects[external] = {
            "status": "available",
            "moment_fraction_change": float(
                relaxed["residuals"]["moment"]["fraction"]
                - standard["residuals"]["moment"]["fraction"]
            ),
            "force_fraction_change": float(
                relaxed["residuals"]["force"]["fraction"]
                - standard["residuals"]["force"]["fraction"]
            ),
            "angle_rms_change_deg": float(
                relaxed["rra_angle_change_rms_deg"]
                - standard["rra_angle_change_rms_deg"]
            ),
        }
    external_effects = {}
    for tracking in tracking_ids:
        baseline = cells["published_original__{}".format(tracking)]
        external_effects[tracking] = {}
        for external in external_ids:
            cell = cells["{}__{}".format(external, tracking)]
            if any(
                value.get("status") != "completed" for value in (baseline, cell)
            ):
                external_effects[tracking][external] = {
                    "status": "unavailable-due-to-cell-failure",
                    "failed_cells": [
                        cell_id
                        for cell_id in (
                            "published_original__{}".format(tracking),
                            "{}__{}".format(external, tracking),
                        )
                        if cells[cell_id].get("status") != "completed"
                    ],
                }
                continue
            external_effects[tracking][external] = {
                "status": "available",
                "moment_fraction_change_from_published_original": float(
                    cell["residuals"]["moment"]["fraction"]
                    - baseline["residuals"]["moment"]["fraction"]
                ),
                "force_fraction_change_from_published_original": float(
                    cell["residuals"]["force"]["fraction"]
                    - baseline["residuals"]["force"]["fraction"]
                ),
            }
    return {
        "tracking_relaxation_within_external_condition": tracking_effects,
        "external_condition_within_tracking_condition": external_effects,
        "interpretation": "complete-factorial-descriptive-effects-not-refitting",
    }


def main(argv=None) -> int:
    args = _parser().parse_args(argv)
    plan_path = Path(args.plan).resolve()
    work_dir = Path(args.work_dir).resolve()
    report_path = Path(args.report).resolve()
    plan = json.loads(plan_path.read_text(encoding="utf-8"))
    if plan.get("scope") != "g6c-s5-rra-sensitivity-plan":
        raise ValueError("worker received an invalid G6c-S5 plan")
    failure_policy = plan.get("execution_failure_policy") or {}
    if (
        failure_policy.get("action")
        != "record-cell-failure-and-continue-remaining-frozen-cells"
        or failure_policy.get("imputation") != "prohibited"
        or failure_policy.get("rescue_eligibility_if_any_cell_fails") is not False
    ):
        raise ValueError("worker received an unsafe G6c-S5 failure policy")
    paths = {name: Path(value).resolve() for name, value in plan["input_paths"].items()}
    for name, expected in plan["input_sha256"].items():
        if not paths[name].is_file() or sha256_file(paths[name]) != str(expected):
            raise ValueError("G6c-S5 worker input checksum mismatch: {}".format(name))

    import opensim

    version = str(opensim.GetVersion())
    required = str(plan["opensim"]["required_version"])
    opensim_version_pass = version.startswith(required)
    if not opensim_version_pass:
        raise RuntimeError(
            "G6c-S5 requires OpenSim {}, observed {}".format(required, version)
        )
    work_dir.mkdir(parents=True, exist_ok=True)
    legacy = _load_legacy_worker()
    external_paths, external_reports = _external_variants(plan, work_dir)
    task_paths, task_reports = _tracking_variants(plan, work_dir)
    source_external = {str(value["id"]): value for value in plan["external_conditions"]}
    source_tracking = {str(value["id"]): value for value in plan["tracking_conditions"]}

    thresholds = plan["thresholds"]
    external_transform_integrity = {
        "published_original": bool(
            external_reports["published_original"]["byte_identical"]
        ),
        "published_wrench_6hz": bool(
            external_reports["published_wrench_6hz"][
                "source_column_spellings_reproduced"
            ]
        )
        and float(
            external_reports["published_wrench_6hz"][
                "maximum_origin_wrench_roundtrip_error_nm"
            ]
        )
        <= float(thresholds["maximum_wrench_roundtrip_error"]),
        "raw_free_6hz": bool(
            external_reports["raw_free_6hz"][
                "source_column_spellings_reproduced"
            ]
        )
        and float(
            external_reports["raw_free_6hz"]["allocation"][
                "maximum_total_reconstruction_error_nm"
            ]
        )
        <= float(thresholds["maximum_raw_total_reconstruction_error_nm"]),
        "zero_free": bool(
            external_reports["zero_free"]["source_column_spellings_reproduced"]
        )
        and float(external_reports["zero_free"]["maximum_absolute_torque_nm"])
        == 0.0,
    }
    external_transform_integrity_pass = all(external_transform_integrity.values())
    tracking_transform_integrity = {
        "standard_byte_identical": bool(task_reports["standard"]["byte_identical"]),
        "relaxed_exact_coordinates": set(
            task_reports["frontal_relaxed_5x"]["coordinates"]
        )
        == set(source_tracking["frontal_relaxed_5x"]["coordinates"]),
        "relaxed_exact_multiplier": float(
            task_reports["frontal_relaxed_5x"]["multiplier"]
        )
        == float(source_tracking["frontal_relaxed_5x"]["multiplier"]),
    }
    tracking_transform_integrity_pass = all(tracking_transform_integrity.values())

    angle_grid = [float(value) for value in plan["angle_time_grid_s"]]
    desired_target = work_dir / "desired_kinematics_target_grid.mot"
    resample_storage_to_grid(paths["desired_kinematics"], angle_grid, desired_target)
    target_grids_exact = verify_exact_grid(desired_target, angle_grid)
    rra_config = plan["opensim"]
    start, end = [float(value) for value in plan["rra_window_s"]]
    native_start, native_end = [float(value) for value in plan["native_target_window_s"]]
    cells = {}
    timeseries_payload: dict[str, np.ndarray] = {}
    common_time = None
    for cell_config in plan["cells"]:
        cell_id = str(cell_config["id"])
        external_id = str(cell_config["external"])
        tracking_id = str(cell_config["tracking"])
        cell_dir = work_dir / "cells" / cell_id
        cell_dir.mkdir(parents=True, exist_ok=True)
        loads = cell_dir / "external_loads.xml"
        legacy._generate_external_loads(opensim, external_paths[external_id], loads)
        current_model = paths["model"]
        final_kinematics = None
        final_actuation = None
        completed_passes = 0
        failure = None
        for pass_number in range(1, int(rra_config["passes"]) + 1):
            try:
                current_model, final_kinematics, final_actuation = (
                    legacy._run_rra_pass(
                        opensim,
                        current_model,
                        paths["desired_kinematics"],
                        loads,
                        paths["rra_actuators"],
                        task_paths[tracking_id],
                        rra_config,
                        start,
                        end,
                        cell_dir,
                        pass_number,
                    )
                )
                completed_passes = pass_number
            except Exception as exc:
                failure = {
                    "stage": "rra-pass-{}".format(pass_number),
                    "exception_type": type(exc).__name__,
                    "message": str(exc),
                    "completed_passes": completed_passes,
                    "retention_policy": str(failure_policy["action"]),
                    "imputation": str(failure_policy["imputation"]),
                }
                break
        admissible = bool(
            source_external[external_id]["admissible_for_mechanics_rescue"]
            and source_tracking[tracking_id]["admissible_for_mechanics_rescue"]
        )
        common_artifacts = {
            "external_mot_sha256": sha256_file(external_paths[external_id]),
            "external_loads_sha256": sha256_file(loads),
            "task_set_sha256": sha256_file(task_paths[tracking_id]),
        }
        if failure is not None:
            cells[cell_id] = {
                "status": "failed",
                "external_condition": external_id,
                "tracking_condition": tracking_id,
                "admissible_for_mechanics_rescue": admissible,
                "failure": failure,
                "artifacts": common_artifacts,
            }
            continue
        if final_kinematics is None or final_actuation is None:
            raise RuntimeError("G6c-S5 cell did not produce final RRA artifacts")
        q_target = cell_dir / "rra_kinematics_target_grid.sto"
        resample_storage_to_grid(Path(final_kinematics), angle_grid, q_target)
        target_grids_exact = target_grids_exact and verify_exact_grid(
            q_target, angle_grid
        )
        angle = rra_angle_change_metrics(desired_target, q_target)
        actuation = _numeric_table(Path(final_actuation))
        native, native_time = _exact_native_window(
            actuation, native_start, native_end
        )
        force_columns = _rra_actuator_columns(native, ("FX", "FY", "FZ"))
        moment_columns = _rra_actuator_columns(native, ("MX", "MY", "MZ"))
        force = native[force_columns].to_numpy(float)
        moment = native[moment_columns].to_numpy(float)
        residuals = summarize_actuator_window(
            force,
            moment,
            body_weight_n=float(plan["normalization"]["body_weight_n"]),
            moment_reference_nm=float(plan["normalization"]["moment_reference_nm"]),
        )
        cells[cell_id] = {
            "status": "completed",
            "external_condition": external_id,
            "tracking_condition": tracking_id,
            "admissible_for_mechanics_rescue": admissible,
            "residuals": residuals,
            **angle,
            "artifacts": {
                **common_artifacts,
                "final_actuation_sha256": sha256_file(Path(final_actuation)),
                "final_kinematics_sha256": sha256_file(Path(final_kinematics)),
                "target_kinematics_sha256": sha256_file(q_target),
                "adjusted_model_sha256": sha256_file(Path(current_model)),
            },
        }
        if common_time is None:
            common_time = native_time
            timeseries_payload["time_s"] = native_time
        elif not np.array_equal(common_time, native_time):
            raise ValueError("G6c-S5 cells do not share the native target grid")
        for axis, label in enumerate("xyz"):
            timeseries_payload["{}_force_n_{}".format(cell_id, label)] = force[
                :, axis
            ]
            timeseries_payload["{}_moment_nm_{}".format(cell_id, label)] = moment[
                :, axis
            ]

    baseline = cells[plan["baseline_cell"]]
    expected = plan["expected_baseline"]
    baseline_completed = baseline.get("status") == "completed"
    force_difference = (
        abs(
            float(baseline["residuals"]["force"]["vector_rms"])
            - float(expected["residual_force_rms_n"])
        )
        if baseline_completed
        else None
    )
    moment_difference = (
        abs(
            float(baseline["residuals"]["moment"]["vector_rms"])
            - float(expected["residual_moment_rms_nm"])
        )
        if baseline_completed
        else None
    )
    baseline_reproduced = bool(
        baseline_completed
        and float(force_difference)
        <= float(thresholds["maximum_baseline_force_rms_difference_n"])
        and float(moment_difference)
        <= float(thresholds["maximum_baseline_moment_rms_difference_nm"])
    )
    diagnosis = classify_rra_sensitivity(
        cells,
        baseline_cell=str(plan["baseline_cell"]),
        thresholds=thresholds,
        baseline_reproduced=baseline_reproduced,
    )
    timeseries_path = work_dir / "g6c_s5_rra_sensitivity_timeseries.csv"
    pd.DataFrame(timeseries_payload).to_csv(
        timeseries_path, index=False, float_format="%.12g", lineterminator="\n"
    )
    report = {
        "schema_version": "1.0.0",
        "scope": "g6c-s5-rra-sensitivity-worker",
        "opensim_version": version,
        "opensim_version_pass": opensim_version_pass,
        "input_sha256": plan["input_sha256"],
        "prior_result_sha256": plan["prior_result_sha256"],
        "cell_count": len(cells),
        "all_cells_complete": len(cells) == int(thresholds["expected_cell_count"]),
        "successful_cell_count": sum(
            cell.get("status") == "completed" for cell in cells.values()
        ),
        "failed_cell_count": sum(
            cell.get("status") == "failed" for cell in cells.values()
        ),
        "target_grids_exact": bool(target_grids_exact),
        "external_variants": external_reports,
        "external_transform_integrity": external_transform_integrity,
        "external_transform_integrity_pass": external_transform_integrity_pass,
        "tracking_variants": task_reports,
        "tracking_transform_integrity": tracking_transform_integrity,
        "tracking_transform_integrity_pass": tracking_transform_integrity_pass,
        "cells": cells,
        "factorial_effects": _effects(cells),
        "baseline_reproduction": {
            "expected": expected,
            "observed_force_rms_n": (
                baseline["residuals"]["force"]["vector_rms"]
                if baseline_completed
                else None
            ),
            "observed_moment_rms_nm": (
                baseline["residuals"]["moment"]["vector_rms"]
                if baseline_completed
                else None
            ),
            "force_rms_difference_n": force_difference,
            "moment_rms_difference_nm": moment_difference,
            "pass": baseline_reproduced,
        },
        "baseline_reproduced": baseline_reproduced,
        "diagnosis": diagnosis,
        "artifacts": {
            "plan_sha256": sha256_file(plan_path),
            "timeseries_sha256": sha256_file(timeseries_path),
            "desired_target_sha256": sha256_file(desired_target),
        },
    }
    report_path.parent.mkdir(parents=True, exist_ok=True)
    report_path.write_text(
        json.dumps(report, indent=2, sort_keys=True) + "\n", encoding="utf-8"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
