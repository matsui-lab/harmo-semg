#!/usr/bin/env python3
"""Run the checksum-bound G6c-S4 residual-moment localization audit."""

from __future__ import annotations

import argparse
import importlib.metadata
import json
import platform
import sys
from pathlib import Path

import numpy as np
import pandas as pd


ROOT = Path(__file__).resolve().parents[2]
if str(ROOT / "src") not in sys.path:
    sys.path.insert(0, str(ROOT / "src"))

from harmo.dynamic_calibration import sha256_file  # noqa: E402
from harmo.g6_execution import _rra_actuator_columns  # noqa: E402
from harmo.ingest.readers.sto import read_opensim_sto_trc  # noqa: E402
from harmo.moment_localization import (  # noqa: E402
    classify_moment_localization,
    moment_component_metrics,
    tracking_error_metrics,
    waveform_associations,
)
from harmo.whole_body_balance import zero_phase_lowpass  # noqa: E402


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--plan", required=True)
    parser.add_argument("--work-dir", required=True)
    parser.add_argument("--report", required=True)
    return parser


def _numeric_table(path: Path) -> pd.DataFrame:
    _, table = read_opensim_sto_trc(str(path))
    if "time" not in table:
        raise ValueError("G6c-S4 storage lacks time")
    numeric = table.apply(pd.to_numeric, errors="coerce")
    time = numeric["time"].to_numpy(float)
    if (
        time.size == 0
        or np.any(np.diff(time) <= 0)
        or not np.all(np.isfinite(numeric.to_numpy(float)))
    ):
        raise ValueError("G6c-S4 storage must be finite and ordered")
    return numeric


def _exact_rows(table: pd.DataFrame, target_time: np.ndarray) -> pd.DataFrame:
    source_time = table["time"].to_numpy(float)
    indices = np.searchsorted(source_time, target_time)
    if (
        np.any(indices >= source_time.size)
        or not np.allclose(source_time[indices], target_time, atol=1e-10, rtol=0.0)
    ):
        raise ValueError("G6c-S4 artifact does not contain the exact target grid")
    return table.iloc[indices].reset_index(drop=True)


def _vector_rms(values: np.ndarray) -> float:
    return float(np.sqrt(np.mean(np.sum(np.square(values), axis=1))))


def _interp_vector(
    table: pd.DataFrame, target_time: np.ndarray, columns: list[str]
) -> np.ndarray:
    source_time = table["time"].to_numpy(float)
    return np.column_stack(
        [
            np.interp(target_time, source_time, table[column].to_numpy(float))
            for column in columns
        ]
    )


def _mot_components(
    table: pd.DataFrame, target_time: np.ndarray, prefixes: list[str]
) -> dict[str, np.ndarray]:
    forces = []
    points = []
    free_moments = []
    for prefix in prefixes:
        forces.append(
            _interp_vector(
                table,
                target_time,
                ["{}_ground_force_v{}".format(prefix, axis) for axis in "xyz"],
            )
        )
        points.append(
            _interp_vector(
                table,
                target_time,
                ["{}_ground_force_p{}".format(prefix, axis) for axis in "xyz"],
            )
        )
        free_moments.append(
            _interp_vector(
                table,
                target_time,
                ["{}_ground_torque_{}".format(prefix, axis) for axis in "xyz"],
            )
        )
    return {
        "force_by_side": np.asarray(forces),
        "point_by_side": np.asarray(points),
        "free_moment_by_side": np.asarray(free_moments),
        "force": np.sum(forces, axis=0),
        "free_moment": np.sum(free_moments, axis=0),
    }


def _unit_scale(unit: str, quantity: str) -> float:
    normalized = str(unit).strip().lower().replace(" ", "")
    lookup = {
        "force": {"n": 1.0, "kn": 1000.0},
        "moment": {"nm": 1.0, "n*m": 1.0, "nmm": 0.001, "n*mm": 0.001},
        "position": {"m": 1.0, "mm": 0.001, "cm": 0.01},
    }
    if normalized not in lookup[quantity]:
        raise ValueError("unsupported C3D {} unit: {}".format(quantity, unit))
    return lookup[quantity][normalized]


def _raw_platform_components(path: Path, audit: dict) -> dict[str, np.ndarray]:
    import ezc3d

    c3d = ezc3d.c3d(str(path), extract_forceplat_data=True)
    platforms = c3d["data"].get("platform", [])
    index = int(audit["platform_index_zero_based"])
    if index < 0 or index >= len(platforms):
        raise ValueError("G6c-S4 raw platform index is out of range")
    platform = platforms[index]
    rotation = np.asarray(audit["c3d_to_ground_rotation"], dtype=float)
    if rotation.shape != (3, 3) or not np.allclose(
        rotation.T @ rotation, np.eye(3), atol=1e-8, rtol=0.0
    ):
        raise ValueError("G6c-S4 C3D rotation must be orthonormal")
    rate = float(c3d["parameters"]["ANALOG"]["RATE"]["value"][0])
    force = (
        np.asarray(platform["force"], dtype=float).T
        * _unit_scale(platform["unit_force"], "force")
    ) @ rotation.T
    point = (
        np.asarray(platform["center_of_pressure"], dtype=float).T
        * _unit_scale(platform["unit_position"], "position")
    ) @ rotation.T
    free = (
        np.asarray(platform["Tz"], dtype=float).T
        * _unit_scale(platform["unit_moment"], "moment")
    ) @ rotation.T
    source_time = (
        np.arange(force.shape[0], dtype=float) / rate
        + float(audit["c3d_time_offset_s"])
    )
    for values in (point, free):
        for axis in range(3):
            finite = np.isfinite(values[:, axis])
            if int(np.sum(finite)) < 2:
                raise ValueError("G6c-S4 raw platform vector is not recoverable")
            values[:, axis] = np.interp(
                source_time, source_time[finite], values[finite, axis]
            )
    if not np.all(np.isfinite(force)):
        raise ValueError("G6c-S4 raw platform force must be finite")
    return {"time": source_time, "force": force, "point": point, "free": free}


def _source_equivalence(
    mot: pd.DataFrame,
    raw: dict[str, np.ndarray],
    target_time: np.ndarray,
    prefixes: list[str],
    audit: dict,
    vertical_axis: int,
) -> tuple[dict, dict[str, np.ndarray]]:
    source_time = mot["time"].to_numpy(float)
    public_full = _mot_components(mot, source_time, prefixes)
    public_force = public_full["force"]
    public_free = public_full["free_moment"]
    side_force = public_full["force_by_side"]
    side_point = public_full["point_by_side"]
    vertical = np.sum(side_force[:, :, vertical_axis], axis=0)
    weighted_point = np.sum(
        side_point * side_force[:, :, vertical_axis, None], axis=0
    )
    public_point = weighted_point / np.where(
        np.abs(vertical[:, None]) < 1e-12, 1.0, vertical[:, None]
    )

    cutoff = float(audit["lowpass_cutoff_hz"])
    order = int(audit["lowpass_order"])
    filtered_public_force = zero_phase_lowpass(
        source_time, public_force, cutoff_hz=cutoff, order=order
    )
    filtered_public_free = zero_phase_lowpass(
        source_time, public_free, cutoff_hz=cutoff, order=order
    )
    filtered_raw_force = zero_phase_lowpass(
        raw["time"], raw["force"], cutoff_hz=cutoff, order=order
    )
    filtered_raw_free = zero_phase_lowpass(
        raw["time"], raw["free"], cutoff_hz=cutoff, order=order
    )

    def align(values: np.ndarray, time: np.ndarray) -> np.ndarray:
        return np.column_stack(
            [np.interp(target_time, time, values[:, axis]) for axis in range(3)]
        )

    public_force_target = align(filtered_public_force, source_time)
    raw_force_target = align(filtered_raw_force, raw["time"])
    public_free_target = align(filtered_public_free, source_time)
    raw_free_target = align(filtered_raw_free, raw["time"])
    public_point_target = align(public_point, source_time)
    raw_point_target = align(raw["point"], raw["time"])
    public_force_unfiltered = align(public_force, source_time)
    raw_force_unfiltered = align(raw["force"], raw["time"])
    contact = (
        np.abs(public_force_unfiltered[:, vertical_axis])
        >= float(audit["contact_threshold_n"])
    ) & (
        np.abs(raw_force_unfiltered[:, vertical_axis])
        >= float(audit["contact_threshold_n"])
    )
    if int(np.sum(contact)) < int(audit["minimum_contact_samples"]):
        raise ValueError("G6c-S4 target window has insufficient common contact")

    force_difference = public_force_target - raw_force_target
    denominator = float(np.sqrt(np.mean(np.square(public_force_target))))
    force_nrmse = float(np.sqrt(np.mean(np.square(force_difference))) / denominator)
    force_correlations = [
        float(np.corrcoef(public_force_target[:, axis], raw_force_target[:, axis])[0, 1])
        for axis in range(3)
    ]
    cop_rmse = float(
        np.sqrt(
            np.mean(
                np.square(public_point_target[contact] - raw_point_target[contact])
            )
        )
    )
    free_correlation = float(
        np.corrcoef(
            public_free_target[:, vertical_axis], raw_free_target[:, vertical_axis]
        )[0, 1]
    )
    free_rmse = float(
        np.sqrt(
            np.mean(
                np.square(
                    public_free_target[:, vertical_axis]
                    - raw_free_target[:, vertical_axis]
                )
            )
        )
    )
    checks = {
        "force_nrmse": force_nrmse <= float(audit["force_nrmse_tolerance"]),
        "force_component_correlation": min(force_correlations)
        >= float(audit["minimum_component_correlation"]),
        "cop_rmse": cop_rmse <= float(audit["cop_rmse_tolerance_m"]),
        "vertical_free_moment_correlation": free_correlation
        >= float(audit["minimum_free_moment_correlation"]),
    }
    report = {
        "filter": {
            "type": "butterworth-zero-phase-sosfiltfilt",
            "cutoff_hz": cutoff,
            "order": order,
            "selection": "inherited-rra-cutoff-not-residual-fitted",
            "force_and_free_moment_only": True,
        },
        "contact_sample_count": int(np.sum(contact)),
        "force_nrmse": force_nrmse,
        "force_component_correlations": force_correlations,
        "cop_rmse_m": cop_rmse,
        "vertical_free_moment_correlation": free_correlation,
        "vertical_free_moment_rmse_nm": free_rmse,
        "published_vertical_free_moment_mean_nm": float(
            np.mean(public_free_target[:, vertical_axis])
        ),
        "raw_vertical_free_moment_mean_nm": float(
            np.mean(raw_free_target[:, vertical_axis])
        ),
        "checks": checks,
        "pass": all(checks.values()),
        "role": "target-window-source-equivalence-not-source-selection",
    }
    arrays = {
        "filtered_public_force": public_force_target,
        "filtered_raw_force": raw_force_target,
        "filtered_public_free": public_free_target,
        "filtered_raw_free": raw_free_target,
        "public_point": public_point_target,
        "raw_point": raw_point_target,
    }
    return report, arrays


def main(argv=None) -> int:
    args = _parser().parse_args(argv)
    plan_path = Path(args.plan).resolve()
    work_dir = Path(args.work_dir).resolve()
    report_path = Path(args.report).resolve()
    plan = json.loads(plan_path.read_text(encoding="utf-8"))
    if plan.get("scope") != "g6c-s4-moment-localization-plan":
        raise ValueError("worker received an invalid G6c-S4 plan")
    paths = {name: Path(value).resolve() for name, value in plan["input_paths"].items()}
    for name, expected in plan["input_sha256"].items():
        if not paths[name].is_file() or sha256_file(paths[name]) != str(expected):
            raise ValueError("G6c-S4 worker input checksum mismatch: {}".format(name))

    s3 = pd.read_csv(paths["s3_timeseries"])
    if not np.all(np.isfinite(s3.select_dtypes(include=[np.number]).to_numpy(float))):
        raise ValueError("G6c-S4 S3 timeseries must be finite")
    start, end = [float(value) for value in plan["comparison_window_s"]]
    selected = s3.loc[
        (s3["external_source_grid"] == 1)
        & (s3["time_s"] >= start - 1e-10)
        & (s3["time_s"] <= end + 1e-10)
    ].reset_index(drop=True)
    target_time = selected["time_s"].to_numpy(float)
    if target_time.size != int(plan["expected_sample_count"]):
        raise ValueError("G6c-S4 target sample count differs from the protocol")

    pass1_table = _exact_rows(_numeric_table(paths["pass1_actuation"]), target_time)
    pass2_table = _exact_rows(_numeric_table(paths["pass2_actuation"]), target_time)
    pass1_error = _exact_rows(
        _numeric_table(paths["pass1_position_error"]), target_time
    )
    pass2_error = _exact_rows(
        _numeric_table(paths["pass2_position_error"]), target_time
    )
    pass_artifact_grids_exact = True
    pass1_columns = _rra_actuator_columns(pass1_table, ("MX", "MY", "MZ"))
    pass2_columns = _rra_actuator_columns(pass2_table, ("MX", "MY", "MZ"))
    pass1_moment = pass1_table[pass1_columns].to_numpy(float)
    pass2_moment = pass2_table[pass2_columns].to_numpy(float)

    reference = float(plan["source_s3_native_quality"].get("residual_moment_rms_nm"))
    source_fraction = float(
        plan["source_s3_native_quality"].get("residual_moment_fraction")
    )
    reference /= source_fraction
    pass1_metrics = moment_component_metrics(pass1_moment, reference_nm=reference)
    pass2_metrics = moment_component_metrics(pass2_moment, reference_nm=reference)
    source_rms = float(
        plan["source_s3_native_quality"].get("residual_moment_rms_nm")
    )
    s3_moment_reproduced = abs(pass2_metrics["vector_rms_nm"] - source_rms) <= float(
        plan["thresholds"]["maximum_s3_moment_reproduction_error_nm"]
    )

    observed = selected[
        ["observed_moment_residual_nm_{}".format(axis) for axis in "xyz"]
    ].to_numpy(float)
    predicted_total = selected[
        ["rra_total_moment_residual_nm_{}".format(axis) for axis in "xyz"]
    ].to_numpy(float)
    maximum_identity_difference = float(np.max(np.abs(observed - predicted_total)))
    decomposition_energy_error = abs(
        pass2_metrics["vector_rms_nm"] ** 2
        - (
            pass2_metrics["mean_vector_magnitude_nm"] ** 2
            + pass2_metrics["zero_mean_vector_rms_nm"] ** 2
        )
    )
    decomposition_identity_pass = bool(
        maximum_identity_difference
        <= float(plan["thresholds"]["maximum_timeseries_identity_error_nm"])
        and decomposition_energy_error
        <= float(plan["thresholds"]["maximum_energy_identity_error_nm2"])
    )

    mot = _numeric_table(paths["ground_reaction"])
    prefixes = [str(value).lower() for value in plan["public_prefixes"]]
    public = _mot_components(mot, target_time, prefixes)
    com = selected[["com_position_m_{}".format(axis) for axis in "xyz"]].to_numpy(
        float
    )
    public_cop_lever = np.sum(
        np.cross(public["point_by_side"] - com[None, :, :], public["force_by_side"]),
        axis=0,
    )
    public_free = public["free_moment"]
    without_free = pass2_moment + public_free
    without_free_metrics = moment_component_metrics(
        without_free, reference_nm=reference
    )
    external_components = {
        "cop_force_lever_about_com": moment_component_metrics(
            public_cop_lever, reference_nm=reference
        ),
        "free_moment": moment_component_metrics(public_free, reference_nm=reference),
        "total_about_com": moment_component_metrics(
            public_cop_lever + public_free, reference_nm=reference
        ),
    }
    vertical_axis = int(plan["vertical_axis"])
    residual_vertical_mean = float(pass2_metrics["axis_mean_nm"][vertical_axis])
    free_vertical_mean = float(np.mean(public_free[:, vertical_axis]))
    opposite = residual_vertical_mean * free_vertical_mean < 0
    opposition_fraction = (
        min(abs(free_vertical_mean) / abs(residual_vertical_mean), 1.0)
        if opposite and abs(residual_vertical_mean) > 1e-12
        else 0.0
    )

    raw = _raw_platform_components(paths["raw_c3d"], plan["external_source_audit"])
    source_equivalence, source_arrays = _source_equivalence(
        mot,
        raw,
        target_time,
        prefixes,
        plan["external_source_audit"],
        vertical_axis,
    )

    translational = plan["translational_coordinates"]
    pass1_tracking_values = {
        str(column): pass1_error[column].to_numpy(float)
        for column in pass1_error.columns
        if column != "time"
    }
    pass2_tracking_values = {
        str(column): pass2_error[column].to_numpy(float)
        for column in pass2_error.columns
        if column != "time"
    }
    tracking = {
        "pass1": tracking_error_metrics(
            pass1_tracking_values, translational_coordinates=translational
        ),
        "pass2": tracking_error_metrics(
            pass2_tracking_values, translational_coordinates=translational
        ),
        "pass2_waveform_associations": waveform_associations(
            pass2_moment,
            pass2_tracking_values,
            minimum_absolute_correlation=float(
                plan["thresholds"]["minimum_waveform_association_correlation"]
            ),
        ),
    }

    diagnosis = classify_moment_localization(
        pass1=pass1_metrics,
        pass2=pass2_metrics,
        without_free_moment=without_free_metrics,
        vertical_free_mean_opposition_fraction=opposition_fraction,
        source_local_pass=bool(source_equivalence["pass"]),
        thresholds=plan["thresholds"],
    )
    work_dir.mkdir(parents=True, exist_ok=True)
    timeseries_path = work_dir / "g6c_s4_moment_localization_timeseries.csv"
    payload: dict[str, np.ndarray] = {"time_s": target_time}
    for axis, label in enumerate("xyz"):
        payload["pass1_residual_moment_nm_{}".format(label)] = pass1_moment[:, axis]
        payload["pass2_residual_moment_nm_{}".format(label)] = pass2_moment[:, axis]
        payload["public_cop_force_lever_nm_{}".format(label)] = public_cop_lever[
            :, axis
        ]
        payload["public_free_moment_nm_{}".format(label)] = public_free[:, axis]
        payload["without_free_counterfactual_nm_{}".format(label)] = without_free[
            :, axis
        ]
        payload["filtered_raw_free_moment_nm_{}".format(label)] = source_arrays[
            "filtered_raw_free"
        ][:, axis]
    for coordinate, values in pass2_tracking_values.items():
        payload["pass2_tracking_error_{}".format(coordinate)] = values
    pd.DataFrame(payload).to_csv(
        timeseries_path, index=False, float_format="%.12g", lineterminator="\n"
    )

    report = {
        "schema_version": "1.0.0",
        "scope": "g6c-s4-moment-localization-worker",
        "analysis_runtime": {
            "implementation": platform.python_implementation(),
            "python": platform.python_version(),
            "numpy": np.__version__,
            "pandas": pd.__version__,
            "scipy": importlib.metadata.version("scipy"),
            "ezc3d": importlib.metadata.version("ezc3d"),
        },
        "input_sha256": plan["input_sha256"],
        "s3_result_sha256": plan["s3_result_sha256"],
        "sample_count": int(target_time.size),
        "window_s": [float(target_time[0]), float(target_time[-1])],
        "moment_reference_nm": reference,
        "pass_artifact_grids_exact": pass_artifact_grids_exact,
        "s3_moment_reproduced": bool(s3_moment_reproduced),
        "maximum_s3_timeseries_identity_difference_nm": maximum_identity_difference,
        "decomposition_energy_identity_error_nm2": decomposition_energy_error,
        "decomposition_identity_pass": decomposition_identity_pass,
        "residual_moment": {"pass1": pass1_metrics, "pass2": pass2_metrics},
        "external_moment_components": external_components,
        "public_free_moment_counterfactual": {
            "residual_if_removed": without_free_metrics,
            "vertical_axis": vertical_axis,
            "residual_vertical_mean_nm": residual_vertical_mean,
            "public_free_vertical_mean_nm": free_vertical_mean,
            "signed_means_opposed": bool(opposite),
            "mean_opposition_fraction": opposition_fraction,
            "role": "diagnostic-only-physically-invalid-source-removal",
        },
        "target_window_source_equivalence": source_equivalence,
        "tracking_error": tracking,
        "diagnosis": diagnosis,
        "artifacts": {
            "plan_sha256": sha256_file(plan_path),
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
