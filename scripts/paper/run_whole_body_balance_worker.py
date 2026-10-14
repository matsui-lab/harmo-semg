#!/usr/bin/env python3
"""Run the checksum-bound G6c-S whole-body Newton--Euler audit."""

from __future__ import annotations

import argparse
import importlib.util
import json
import math
import sys
from pathlib import Path

import numpy as np
import pandas as pd


ROOT = Path(__file__).resolve().parents[2]
if str(ROOT / "src") not in sys.path:
    sys.path.insert(0, str(ROOT / "src"))

from harmo.dynamic_calibration import sha256_file  # noqa: E402
from harmo.ingest.readers.sto import read_opensim_sto_trc  # noqa: E402
from harmo.mechanics_audit import (  # noqa: E402
    canonical_sha256,
    resample_storage_to_grid,
    verify_exact_grid,
)
from harmo.whole_body_balance import (  # noqa: E402
    calculate_whole_body_balance,
    classify_balance_nonclosure,
    mot_external_wrench,
    raw_c3d_external_wrench,
    time_derivative,
    zero_phase_lowpass,
)


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--plan", required=True)
    parser.add_argument("--work-dir", required=True)
    parser.add_argument("--report", required=True)
    return parser


def _load_legacy_worker():
    path = ROOT / "scripts/paper/run_dynamic_mechanics_worker.py"
    spec = importlib.util.spec_from_file_location("harmo_g6cs_legacy_worker", str(path))
    if spec is None or spec.loader is None:
        raise RuntimeError("cannot load the OpenSim IK worker")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _header_in_degrees(header: pd.DataFrame) -> bool:
    if header.empty:
        return False
    lookup = {str(key).lower(): value for key, value in header.iloc[0].items()}
    return str(lookup.get("indegrees", "no")).strip().lower() in {
        "yes",
        "true",
        "1",
    }


def _motion_kinematics(opensim, model_path: Path, motion_path: Path, plan: dict):
    header, table = read_opensim_sto_trc(str(motion_path))
    time = pd.to_numeric(table["time"], errors="coerce").to_numpy(float)
    expected = np.asarray(plan["kinematics_time_grid_s"], dtype=float)
    if time.shape != expected.shape or not np.allclose(
        time, expected, atol=1e-10, rtol=0.0
    ):
        raise ValueError("OpenSim motion does not match the frozen kinematics grid")
    model = opensim.Model(str(model_path))
    state = model.initSystem()
    observed_gravity = np.asarray(
        [float(model.getGravity().get(index)) for index in range(3)], dtype=float
    )
    expected_gravity = np.asarray(plan["gravity_m_s2"], dtype=float)
    if not np.allclose(observed_gravity, expected_gravity, atol=1e-12, rtol=0.0):
        raise ValueError(
            "model gravity differs from the frozen G6c-S gravity: {}".format(
                observed_gravity.tolist()
            )
        )
    coordinates = model.getCoordinateSet()
    column_lookup = {str(column).lower(): str(column) for column in table.columns}
    in_degrees = _header_in_degrees(header)
    coordinate_rows = []
    filtered_values = {}
    filtered_speeds = {}
    filter_config = plan["filter"]
    cutoff = float(filter_config["cutoff_hz"])
    order = int(filter_config["order"])
    for index in range(coordinates.getSize()):
        coordinate = coordinates.get(index)
        name = str(coordinate.getName())
        column = column_lookup.get(name.lower())
        if column is None:
            if coordinate.getLocked(state):
                continue
            raise ValueError("IK motion lacks model coordinate: {}".format(name))
        values = pd.to_numeric(table[column], errors="coerce").to_numpy(float)
        rotational = int(coordinate.getMotionType()) == 1
        if in_degrees and rotational:
            values = np.deg2rad(values)
        filtered = zero_phase_lowpass(time, values, cutoff_hz=cutoff, order=order)
        speed = time_derivative(time, filtered)
        filtered_values[name] = filtered
        filtered_speeds[name] = speed
        coordinate_rows.append(
            {
                "name": name,
                "motion_type": int(coordinate.getMotionType()),
                "source_column": column,
                "source_in_degrees": bool(in_degrees and rotational),
            }
        )
    if not coordinate_rows:
        raise ValueError("no model coordinates were mapped from IK")

    com = np.empty((time.size, 3), dtype=float)
    angular_momentum = np.empty((time.size, 3), dtype=float)
    for row in range(time.size):
        state.setTime(float(time[row]))
        for item in coordinate_rows:
            coordinate = coordinates.get(str(item["name"]))
            coordinate.setValue(
                state, float(filtered_values[str(item["name"])][row]), False
            )
        model.realizePosition(state)
        for item in coordinate_rows:
            coordinate = coordinates.get(str(item["name"]))
            coordinate.setSpeedValue(
                state, float(filtered_speeds[str(item["name"])][row])
            )
        model.realizeVelocity(state)
        center = model.calcMassCenterPosition(state)
        momentum = model.getMatterSubsystem().calcSystemCentralMomentum(state)
        angular = momentum.get(0)
        com[row] = [float(center.get(axis)) for axis in range(3)]
        angular_momentum[row] = [float(angular.get(axis)) for axis in range(3)]
    mass = float(model.getTotalMass(state))
    if not math.isfinite(mass) or mass <= 0:
        raise ValueError("OpenSim model mass is not finite and positive")
    return {
        "time_s": time,
        "com_position_m": com,
        "central_angular_momentum_nms": angular_momentum,
        "mass_kg": mass,
        "gravity_m_s2": observed_gravity,
        "coordinate_mapping": coordinate_rows,
        "source_in_degrees": in_degrees,
        "filter": {
            "type": "butterworth-zero-phase-sosfiltfilt",
            "cutoff_hz": cutoff,
            "order": order,
            "applied_to": "coordinate-values-before-speed-and-state-realization",
            "selection": "protocol-frozen-not-residual-fitted",
        },
    }


def _serialize_balance(balance: dict, provenance: dict) -> tuple[dict, dict]:
    timeseries = balance.pop("timeseries")
    return {**balance, "source_provenance": provenance}, timeseries


def _timeseries_frame(
    time: np.ndarray,
    com: np.ndarray,
    angular_momentum: np.ndarray,
    raw: dict,
    mot: dict,
) -> pd.DataFrame:
    payload = {"time_s": time}
    for prefix, values in (
        ("com_position_m", com),
        ("central_angular_momentum_nms", angular_momentum),
    ):
        for axis, label in enumerate("xyz"):
            payload["{}_{}".format(prefix, label)] = values[:, axis]
    for source, series in (("raw", raw), ("mot", mot)):
        payload["{}_evaluation".format(source)] = series["evaluation_mask"].astype(int)
        for key, unit_key in (
            ("com_acceleration_m_s2", "com_acceleration_m_s2"),
            ("angular_momentum_rate_nm", "angular_momentum_rate_nm"),
            ("filtered_external_force_n", "external_force_n"),
            ("filtered_external_moment_origin_nm", "external_moment_origin_nm"),
            ("external_moment_com_nm", "external_moment_com_nm"),
            ("force_residual_n", "force_residual_n"),
            ("moment_residual_nm", "moment_residual_nm"),
        ):
            values = series[key]
            for axis, label in enumerate("xyz"):
                payload["{}_{}_{}".format(source, unit_key, label)] = values[:, axis]
    return pd.DataFrame(payload)


def _negative_controls(
    plan: dict,
    kinematics: dict,
    raw_wrench: dict,
    raw_balance: dict,
) -> dict:
    kwargs = {
        "mass_kg": kinematics["mass_kg"],
        "gravity_m_s2": kinematics["gravity_m_s2"],
        "vertical_axis": int(plan["vertical_axis"]),
        "evaluation_window_s": plan["evaluation_window_s"],
        "external_lowpass_cutoff_hz": float(plan["filter"]["cutoff_hz"]),
        "lowpass_order": int(plan["filter"]["order"]),
    }

    def run(force, moment):
        value = calculate_whole_body_balance(
            kinematics["time_s"],
            kinematics["com_position_m"],
            kinematics["central_angular_momentum_nms"],
            force,
            moment,
            **kwargs,
        )
        value.pop("timeseries")
        return value

    baseline_force = float(raw_balance["residual_force_fraction"])
    baseline_moment = float(raw_balance["residual_moment_fraction"])
    source_force = np.asarray(raw_wrench["force_n"], dtype=float)
    source_moment = np.asarray(raw_wrench["moment_at_ground_origin_nm"], dtype=float)
    shifted = raw_c3d_external_wrench(
        Path(plan["input_paths"]["raw_c3d"]),
        np.asarray(kinematics["time_s"], dtype=float) + 0.03,
        platform_index=int(plan["external_loads"]["platform_index_zero_based"]),
        c3d_to_ground_rotation=plan["external_loads"]["c3d_to_ground_rotation"],
        c3d_time_offset_s=float(plan["external_loads"]["c3d_time_offset_s"]),
        vertical_axis=int(plan["vertical_axis"]),
        contact_threshold_n=float(plan["external_loads"]["contact_threshold_n"]),
    )
    controls = {
        "grf_time_shift_plus_30_ms": run(
            shifted["force_n"], shifted["moment_at_ground_origin_nm"]
        ),
        "ground_moment_sign_inversion": run(source_force, -source_moment),
        "ground_moment_unit_x1000": run(source_force, source_moment * 1000.0),
    }
    for value in controls.values():
        value["fault_detected"] = bool(
            float(value["residual_force_fraction"]) > baseline_force * 1.01
            or float(value["residual_moment_fraction"]) > baseline_moment * 1.01
        )
    return {
        "baseline_raw_force_fraction": baseline_force,
        "baseline_raw_moment_fraction": baseline_moment,
        "controls": controls,
        "all_faults_detected": all(
            bool(value["fault_detected"]) for value in controls.values()
        ),
        "fault_detection_rule": "either-normalized-residual-exceeds-baseline-by-1-percent",
    }


def main(argv=None) -> int:
    args = _parser().parse_args(argv)
    plan_path = Path(args.plan).resolve()
    work_dir = Path(args.work_dir).resolve()
    report_path = Path(args.report).resolve()
    plan = json.loads(plan_path.read_text(encoding="utf-8"))
    if plan.get("scope") != "g6c-s-whole-body-balance-plan":
        raise ValueError("worker received an invalid G6c-S plan")
    for role, expected in plan.get("input_sha256", {}).items():
        path = Path(str(plan["input_paths"][role])).resolve()
        if not path.is_file() or sha256_file(path) != str(expected):
            raise ValueError("G6c-S worker input checksum mismatch: {}".format(role))

    import opensim

    version = str(opensim.GetVersion())
    required = str(plan["opensim"].get("required_version", "4.5.2"))
    if not version.startswith(required):
        raise RuntimeError("G6c-S requires OpenSim {}, observed {}".format(required, version))
    work_dir.mkdir(parents=True, exist_ok=True)
    legacy = _load_legacy_worker()
    paths = {key: Path(value).resolve() for key, value in plan["input_paths"].items()}
    normalized_markers = work_dir / "markers_opensim_compatible.trc"
    marker_normalization = legacy.normalize_trc_time_header(
        paths["markers"], normalized_markers
    )
    ik_dir = work_dir / "padded_marker_ik"
    ik_dir.mkdir(parents=True, exist_ok=True)
    padded_ik, ik_errors = legacy._run_ik(
        opensim,
        paths["model"],
        normalized_markers,
        paths["ik_setup"],
        float(plan["kinematics_window_s"][0]),
        float(plan["kinematics_window_s"][1]),
        ik_dir,
        "g6cs_whole_body",
    )
    source_grid_motion = work_dir / "kinematics_source_grid.mot"
    resampling = resample_storage_to_grid(
        Path(padded_ik), plan["kinematics_time_grid_s"], source_grid_motion
    )
    kinematics_grid_exact = verify_exact_grid(
        source_grid_motion, plan["kinematics_time_grid_s"]
    )
    kinematics = _motion_kinematics(
        opensim, paths["model"], source_grid_motion, plan
    )

    external = plan["external_loads"]
    raw_wrench = raw_c3d_external_wrench(
        paths["raw_c3d"],
        kinematics["time_s"],
        platform_index=int(external["platform_index_zero_based"]),
        c3d_to_ground_rotation=external["c3d_to_ground_rotation"],
        c3d_time_offset_s=float(external["c3d_time_offset_s"]),
        vertical_axis=int(plan["vertical_axis"]),
        contact_threshold_n=float(external["contact_threshold_n"]),
    )
    mot_wrench = mot_external_wrench(
        paths["ground_reaction"],
        kinematics["time_s"],
        prefixes=external["published_prefixes"],
    )
    common = {
        "mass_kg": kinematics["mass_kg"],
        "gravity_m_s2": kinematics["gravity_m_s2"],
        "vertical_axis": int(plan["vertical_axis"]),
        "evaluation_window_s": plan["evaluation_window_s"],
        "external_lowpass_cutoff_hz": float(plan["filter"]["cutoff_hz"]),
        "lowpass_order": int(plan["filter"]["order"]),
    }
    raw_balance_full = calculate_whole_body_balance(
        kinematics["time_s"],
        kinematics["com_position_m"],
        kinematics["central_angular_momentum_nms"],
        raw_wrench["force_n"],
        raw_wrench["moment_at_ground_origin_nm"],
        **common,
    )
    mot_balance_full = calculate_whole_body_balance(
        kinematics["time_s"],
        kinematics["com_position_m"],
        kinematics["central_angular_momentum_nms"],
        mot_wrench["force_n"],
        mot_wrench["moment_at_ground_origin_nm"],
        **common,
    )
    raw_balance, raw_timeseries = _serialize_balance(
        raw_balance_full, raw_wrench["provenance"]
    )
    mot_balance, mot_timeseries = _serialize_balance(
        mot_balance_full, mot_wrench["provenance"]
    )
    diagnosis = classify_balance_nonclosure(
        raw_balance,
        mot_balance,
        plan["prior_rra"],
        force_fraction_threshold=float(plan["thresholds"]["residual_force_fraction"]),
        moment_fraction_threshold=float(plan["thresholds"]["residual_moment_fraction"]),
    )
    negative_controls = _negative_controls(plan, kinematics, raw_wrench, raw_balance)
    timeseries_path = work_dir / "whole_body_balance_timeseries.csv"
    _timeseries_frame(
        kinematics["time_s"],
        kinematics["com_position_m"],
        kinematics["central_angular_momentum_nms"],
        raw_timeseries,
        mot_timeseries,
    ).to_csv(timeseries_path, index=False, float_format="%.12g", lineterminator="\n")
    observed_evaluation = np.asarray(kinematics["time_s"])[
        (np.asarray(kinematics["time_s"]) >= float(plan["evaluation_window_s"][0]) - 1e-10)
        & (np.asarray(kinematics["time_s"]) <= float(plan["evaluation_window_s"][1]) + 1e-10)
    ]
    evaluation_grid_exact = (
        observed_evaluation.size == int(plan["expected_evaluation_rows"])
        and canonical_sha256([float(value) for value in observed_evaluation])
        == plan["evaluation_time_grid_sha256"]
    )
    report = {
        "schema_version": "1.0.0",
        "scope": "g6c-s-whole-body-balance-worker",
        "opensim_version": version,
        "input_sha256": plan["input_sha256"],
        "prior_g6c_result_sha256": plan["prior_g6c_result_sha256"],
        "kinematics_grid_exact": bool(kinematics_grid_exact),
        "evaluation_grid_exact": bool(evaluation_grid_exact),
        "marker_normalization": marker_normalization,
        "kinematics": {
            "mass_kg": kinematics["mass_kg"],
            "gravity_m_s2": kinematics["gravity_m_s2"].tolist(),
            "coordinate_mapping": kinematics["coordinate_mapping"],
            "filter": kinematics["filter"],
            "source_grid_resampling": resampling,
            "padded_row_count": int(kinematics["time_s"].size),
            "evaluation_row_count": int(observed_evaluation.size),
        },
        "raw_balance": raw_balance,
        "mot_balance": mot_balance,
        "prior_rra": plan["prior_rra"],
        "diagnosis": diagnosis,
        "negative_controls": negative_controls,
        "artifacts": {
            "plan_sha256": sha256_file(plan_path),
            "padded_ik_sha256": sha256_file(Path(padded_ik)),
            "ik_errors_sha256": sha256_file(Path(ik_errors)),
            "source_grid_motion_sha256": sha256_file(source_grid_motion),
            "timeseries_sha256": sha256_file(timeseries_path),
        },
    }
    report_path.parent.mkdir(parents=True, exist_ok=True)
    report_path.write_text(
        json.dumps(report, indent=2, sort_keys=True) + "\n", encoding="utf-8"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
