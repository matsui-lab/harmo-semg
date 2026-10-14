#!/usr/bin/env python3
"""Run the checksum-bound G6c-S2 mechanics-cause localization audit."""

from __future__ import annotations

import argparse
import importlib.util
import json
import shutil
import sys
from pathlib import Path

import numpy as np
import pandas as pd


ROOT = Path(__file__).resolve().parents[2]
if str(ROOT / "src") not in sys.path:
    sys.path.insert(0, str(ROOT / "src"))

from harmo.dynamic_calibration import sha256_file  # noqa: E402
from harmo.g6_execution import rra_residual_metrics  # noqa: E402
from harmo.mechanics_audit import (  # noqa: E402
    canonical_sha256,
    resample_storage_to_grid,
    verify_exact_grid,
)
from harmo.mechanics_localization import (  # noqa: E402
    compare_model_inertial_properties,
    factorial_effect_localization,
    optimal_scalar_mass_diagnostic,
    synchronization_cross_validation,
)
from harmo.whole_body_balance import (  # noqa: E402
    calculate_whole_body_balance,
    motion_whole_body_kinematics,
    mot_external_wrench,
    raw_c3d_external_wrench,
)


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--plan", required=True)
    parser.add_argument("--work-dir", required=True)
    parser.add_argument("--report", required=True)
    return parser


def _load_legacy_worker():
    path = ROOT / "scripts/paper/run_dynamic_mechanics_worker.py"
    spec = importlib.util.spec_from_file_location("harmo_g6cs2_legacy_worker", str(path))
    if spec is None or spec.loader is None:
        raise RuntimeError("cannot load the OpenSim mechanics worker")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _kinematics(opensim, plan: dict, model: Path, motion: Path) -> dict:
    return motion_whole_body_kinematics(
        opensim,
        model,
        motion,
        plan["kinematics_time_grid_s"],
        gravity_m_s2=plan["gravity_m_s2"],
        cutoff_hz=float(plan["filter"]["cutoff_hz"]),
        lowpass_order=int(plan["filter"]["order"]),
    )


def _balance(plan: dict, kinematics: dict, wrench: dict) -> dict:
    return calculate_whole_body_balance(
        kinematics["time_s"],
        kinematics["com_position_m"],
        kinematics["central_angular_momentum_nms"],
        wrench["force_n"],
        wrench["moment_at_ground_origin_nm"],
        mass_kg=kinematics["mass_kg"],
        gravity_m_s2=kinematics["gravity_m_s2"],
        vertical_axis=int(plan["vertical_axis"]),
        evaluation_window_s=plan["target_window_s"],
        external_lowpass_cutoff_hz=float(plan["filter"]["cutoff_hz"]),
        lowpass_order=int(plan["filter"]["order"]),
        external_preprocessed=True,
    )


def _summary(value: dict) -> tuple[dict, dict]:
    value = dict(value)
    timeseries = value.pop("timeseries")
    return value, timeseries


def _timeseries(
    time: np.ndarray,
    cells: dict,
    raw_series: dict,
    mot_series: dict,
) -> pd.DataFrame:
    payload = {"time_s": time}
    for cell, kinematics in cells.items():
        for axis, label in enumerate("xyz"):
            payload["{}_com_m_{}".format(cell, label)] = kinematics[
                "com_position_m"
            ][:, axis]
            payload["{}_H_nms_{}".format(cell, label)] = kinematics[
                "central_angular_momentum_nms"
            ][:, axis]
        for source, series in (("raw", raw_series[cell]), ("mot", mot_series[cell])):
            for key, short in (
                ("force_residual_n", "force_residual_n"),
                ("moment_residual_nm", "moment_residual_nm"),
            ):
                for axis, label in enumerate("xyz"):
                    payload["{}_{}_{}_{}".format(cell, source, short, label)] = series[
                        key
                    ][:, axis]
    return pd.DataFrame(payload)


def main(argv=None) -> int:
    args = _parser().parse_args(argv)
    plan_path = Path(args.plan).resolve()
    work_dir = Path(args.work_dir).resolve()
    report_path = Path(args.report).resolve()
    plan = json.loads(plan_path.read_text(encoding="utf-8"))
    if plan.get("scope") != "g6c-s2-mechanics-localization-plan":
        raise ValueError("worker received an invalid G6c-S2 plan")
    for role, expected in plan["input_sha256"].items():
        path = Path(plan["input_paths"][role]).resolve()
        if not path.is_file() or sha256_file(path) != str(expected):
            raise ValueError("G6c-S2 worker input checksum mismatch: {}".format(role))

    import opensim

    version = str(opensim.GetVersion())
    required = str(plan["opensim"]["required_version"])
    if not version.startswith(required):
        raise RuntimeError("G6c-S2 requires OpenSim {}, observed {}".format(required, version))
    work_dir.mkdir(parents=True, exist_ok=True)
    legacy = _load_legacy_worker()
    paths = {key: Path(value).resolve() for key, value in plan["input_paths"].items()}

    normalized_markers = work_dir / "markers_opensim_compatible.trc"
    marker_normalization = legacy.normalize_trc_time_header(
        paths["markers"], normalized_markers
    )
    ik_dir = work_dir / "padded_ik"
    ik_dir.mkdir(parents=True, exist_ok=True)
    padded_ik, ik_errors = legacy._run_ik(
        opensim,
        paths["model"],
        normalized_markers,
        paths["ik_setup"],
        float(plan["kinematics_window_s"][0]),
        float(plan["kinematics_window_s"][1]),
        ik_dir,
        "g6cs2_padded",
    )
    ik_source_grid = work_dir / "ik_source_grid.mot"
    ik_resampling = resample_storage_to_grid(
        Path(padded_ik), plan["kinematics_time_grid_s"], ik_source_grid
    )

    loads_path = work_dir / "external_loads.xml"
    legacy._generate_external_loads(opensim, paths["ground_reaction"], loads_path)
    rra_dir = work_dir / "padded_rra"
    rra_dir.mkdir(parents=True, exist_ok=True)
    rra_config = plan["opensim"]["rra"]
    actuators, tasks, residual_point = legacy._generate_rra_files(
        opensim, paths["model"], rra_config, rra_dir
    )
    current_model = paths["model"]
    final_kinematics = None
    final_actuation = None
    for pass_number in (1, 2):
        current_model, final_kinematics, final_actuation = legacy._run_rra_pass(
            opensim,
            current_model,
            Path(padded_ik),
            loads_path,
            actuators,
            tasks,
            rra_config,
            float(plan["kinematics_window_s"][0]),
            float(plan["kinematics_window_s"][1]),
            rra_dir,
            pass_number,
        )
    adjusted_model = work_dir / "rra_adjusted_model.osim"
    adaptive_rra_kinematics = work_dir / "rra_kinematics_adaptive.sto"
    shutil.copy2(str(current_model), adjusted_model)
    shutil.copy2(str(final_kinematics), adaptive_rra_kinematics)
    rra_source_grid = work_dir / "rra_kinematics_source_grid.sto"
    rra_resampling = resample_storage_to_grid(
        adaptive_rra_kinematics, plan["kinematics_time_grid_s"], rra_source_grid
    )
    kinematics_grid_exact = verify_exact_grid(
        ik_source_grid, plan["kinematics_time_grid_s"]
    ) and verify_exact_grid(rra_source_grid, plan["kinematics_time_grid_s"])

    cell_paths = {
        "original_model_ik": (paths["model"], ik_source_grid),
        "original_model_rra_kinematics": (paths["model"], rra_source_grid),
        "adjusted_model_ik": (adjusted_model, ik_source_grid),
        "adjusted_model_rra_kinematics": (adjusted_model, rra_source_grid),
    }
    cells = {
        name: _kinematics(opensim, plan, model, motion)
        for name, (model, motion) in cell_paths.items()
    }
    reference_time = cells["original_model_ik"]["time_s"]
    external = plan["external_loads"]
    filter_cutoff = float(plan["filter"]["cutoff_hz"])
    filter_order = int(plan["filter"]["order"])
    raw_wrench = raw_c3d_external_wrench(
        paths["raw_c3d"],
        reference_time,
        platform_index=int(external["platform_index_zero_based"]),
        c3d_to_ground_rotation=external["c3d_to_ground_rotation"],
        c3d_time_offset_s=float(external["c3d_time_offset_s"]),
        vertical_axis=int(plan["vertical_axis"]),
        contact_threshold_n=float(external["contact_threshold_n"]),
        source_lowpass_cutoff_hz=filter_cutoff,
        lowpass_order=filter_order,
    )
    mot_wrench = mot_external_wrench(
        paths["ground_reaction"],
        reference_time,
        prefixes=external["published_prefixes"],
        source_lowpass_cutoff_hz=filter_cutoff,
        lowpass_order=filter_order,
    )

    raw_balances = {}
    mot_balances = {}
    raw_series = {}
    mot_series = {}
    for name, kinematics in cells.items():
        raw_balances[name], raw_series[name] = _summary(
            _balance(plan, kinematics, raw_wrench)
        )
        mot_balances[name], mot_series[name] = _summary(
            _balance(plan, kinematics, mot_wrench)
        )
    thresholds = plan["thresholds"]
    factorial_config = plan["factorial"]
    raw_factorial = factorial_effect_localization(
        raw_balances,
        force_fraction_threshold=float(thresholds["residual_force_fraction"]),
        moment_fraction_threshold=float(thresholds["residual_moment_fraction"]),
        minimum_relative_reduction=float(
            factorial_config["minimum_contributor_relative_reduction"]
        ),
    )
    mot_factorial = factorial_effect_localization(
        mot_balances,
        force_fraction_threshold=float(thresholds["residual_force_fraction"]),
        moment_fraction_threshold=float(thresholds["residual_moment_fraction"]),
        minimum_relative_reduction=float(
            factorial_config["minimum_contributor_relative_reduction"]
        ),
    )

    baseline_kinematics = cells["original_model_ik"]
    sync_kinematics = {
        **baseline_kinematics,
        "vertical_axis": int(plan["vertical_axis"]),
    }
    synchronization_config = plan["synchronization"]
    synchronization = synchronization_cross_validation(
        sync_kinematics,
        raw_wrench["force_n"],
        raw_wrench["moment_at_ground_origin_nm"],
        calibration_windows_s=synchronization_config["calibration_windows_s"],
        target_window_s=plan["target_window_s"],
        minimum_lag_s=float(synchronization_config["minimum_lag_s"]),
        maximum_lag_s=float(synchronization_config["maximum_lag_s"]),
        lag_step_s=float(synchronization_config["lag_step_s"]),
        maximum_calibration_disagreement_s=float(
            synchronization_config["maximum_calibration_disagreement_s"]
        ),
        minimum_target_force_reduction=float(
            synchronization_config["minimum_target_force_relative_reduction"]
        ),
        force_fraction_threshold=float(thresholds["residual_force_fraction"]),
        moment_fraction_threshold=float(thresholds["residual_moment_fraction"]),
        external_lowpass_cutoff_hz=filter_cutoff,
        lowpass_order=filter_order,
    )
    mass_config = plan["mass_diagnostic"]
    scalar_mass = optimal_scalar_mass_diagnostic(
        reference_time,
        baseline_kinematics["com_position_m"],
        raw_wrench["force_n"],
        recorded_mass_kg=float(baseline_kinematics["mass_kg"]),
        gravity_m_s2=baseline_kinematics["gravity_m_s2"],
        evaluation_window_s=plan["target_window_s"],
        maximum_relative_mass_change=float(
            mass_config["maximum_plausible_relative_mass_change"]
        ),
        force_fraction_threshold=float(thresholds["residual_force_fraction"]),
    )
    inertial_changes = compare_model_inertial_properties(
        paths["model"], adjusted_model
    )
    both_factorials_closed = bool(
        raw_factorial["combined_mechanics_closed"]
        and mot_factorial["combined_mechanics_closed"]
    )
    kinematics_is_consistent_force_contributor = all(
        value["force"]["classification"]
        == "kinematics-contributor-not-sufficient"
        for value in (raw_factorial, mot_factorial)
    )
    if synchronization["synchronization_sufficient"]:
        primary_interpretation = "constant-time-synchronization-sufficient"
    elif both_factorials_closed:
        primary_interpretation = "rra-kinematics-and-model-close-mechanics"
    elif kinematics_is_consistent_force_contributor:
        primary_interpretation = (
            "rra-kinematics-primary-force-contributor-but-insufficient"
        )
    else:
        primary_interpretation = "common-mechanics-nonclosure-remains-unresolved"
    cause_summary = {
        "synchronization_sufficient": synchronization["synchronization_sufficient"],
        "scalar_mass_sufficient": scalar_mass["scalar_mass_sufficient"],
        "raw_force_classification": raw_factorial["force"]["classification"],
        "raw_moment_classification": raw_factorial["moment"]["classification"],
        "mot_force_classification": mot_factorial["force"]["classification"],
        "mot_moment_classification": mot_factorial["moment"]["classification"],
        "primary_interpretation": primary_interpretation,
    }
    timeseries_path = work_dir / "g6c_s2_factorial_timeseries.csv"
    _timeseries(reference_time, cells, raw_series, mot_series).to_csv(
        timeseries_path, index=False, float_format="%.12g", lineterminator="\n"
    )
    target = reference_time[
        (reference_time >= float(plan["target_window_s"][0]) - 1e-10)
        & (reference_time <= float(plan["target_window_s"][1]) + 1e-10)
    ]
    target_grid_exact = (
        target.size == int(plan["expected_target_rows"])
        and canonical_sha256([float(value) for value in target])
        == plan["target_time_grid_sha256"]
    )
    report = {
        "schema_version": "1.0.0",
        "scope": "g6c-s2-mechanics-localization-worker",
        "opensim_version": version,
        "input_sha256": plan["input_sha256"],
        "prior_g6cs_result_sha256": plan["prior_g6cs_result_sha256"],
        "kinematics_grid_exact": bool(kinematics_grid_exact),
        "target_grid_exact": bool(target_grid_exact),
        "rra_pass_count": 2,
        "marker_normalization": marker_normalization,
        "source_grid_resampling": {
            "ik": ik_resampling,
            "rra": rra_resampling,
        },
        "residual_force_application_point": {
            "body": "pelvis",
            "frame": "pelvis",
            "point_m": residual_point,
            "source": "model-pelvis-mass-center",
        },
        "raw_external_provenance": raw_wrench["provenance"],
        "mot_external_provenance": mot_wrench["provenance"],
        "balances": {"raw": raw_balances, "mot": mot_balances},
        "raw_factorial": raw_factorial,
        "mot_factorial": mot_factorial,
        "synchronization": synchronization,
        "scalar_mass_diagnostic": scalar_mass,
        "rra_model_inertial_changes": inertial_changes,
        "padded_rra_residuals": rra_residual_metrics(Path(final_actuation)),
        "prior_rra": plan["prior_rra"],
        "cause_summary": cause_summary,
        "artifacts": {
            "plan_sha256": sha256_file(plan_path),
            "ik_source_grid_sha256": sha256_file(ik_source_grid),
            "rra_source_grid_sha256": sha256_file(rra_source_grid),
            "rra_adjusted_model_sha256": sha256_file(adjusted_model),
            "rra_actuation_sha256": sha256_file(Path(final_actuation)),
            "timeseries_sha256": sha256_file(timeseries_path),
            "actuators_sha256": sha256_file(actuators),
            "tasks_sha256": sha256_file(tasks),
            "loads_sha256": sha256_file(loads_path),
            "ik_errors_sha256": sha256_file(Path(ik_errors)),
        },
    }
    report_path.parent.mkdir(parents=True, exist_ok=True)
    report_path.write_text(
        json.dumps(report, indent=2, sort_keys=True) + "\n", encoding="utf-8"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
