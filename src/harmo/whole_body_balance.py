"""RRA-independent whole-body Newton--Euler auditing for G6c-S.

The audit compares one kinematic demand against two independently traceable
external-wrench representations: the combined raw C3D force platform and the
recombined public MOT foot loads.  It does not tune a coordinate transform,
time shift, filter, or threshold against the observed residual.
"""

from __future__ import annotations

import json
import math
from pathlib import Path
from typing import Any, Dict, Mapping, Optional, Sequence, Tuple

import numpy as np
import pandas as pd
import yaml
from scipy.signal import butter, sosfiltfilt

from harmo.dynamic_calibration import sha256_file, validate_public_source_bundle
from harmo.ingest.readers.sto import read_opensim_sto_trc
from harmo.mechanics_audit import canonical_sha256, trc_time_grid


WHOLE_BODY_BALANCE_SCHEMA_VERSION = "1.0.0"


def _load_yaml(path: Path) -> Dict[str, Any]:
    value = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    if not isinstance(value, dict):
        raise ValueError("G6c-S YAML root must be a mapping")
    return value


def _finite_matrix(value: Sequence[Sequence[float]], name: str) -> np.ndarray:
    array = np.asarray(value, dtype=float)
    if array.ndim != 2 or array.shape[1] != 3:
        raise ValueError("{} must have shape (n, 3)".format(name))
    if not np.all(np.isfinite(array)):
        raise ValueError("{} must contain only finite values".format(name))
    return array


def validate_uniform_time_grid(
    time_s: Sequence[float], *, relative_tolerance: float = 1e-6
) -> Tuple[np.ndarray, float]:
    """Validate and return a uniformly sampled, strictly increasing grid."""
    time = np.asarray(time_s, dtype=float)
    if time.ndim != 1 or time.size < 8 or not np.all(np.isfinite(time)):
        raise ValueError("time grid must contain at least eight finite samples")
    steps = np.diff(time)
    if np.any(steps <= 0):
        raise ValueError("time grid must be strictly increasing")
    step = float(np.median(steps))
    if np.max(np.abs(steps - step)) > abs(step) * float(relative_tolerance):
        raise ValueError("whole-body balance requires a uniform time grid")
    return time, step


def zero_phase_lowpass(
    time_s: Sequence[float],
    values: Sequence[Sequence[float]],
    *,
    cutoff_hz: float,
    order: int = 4,
) -> np.ndarray:
    """Apply a predeclared Butterworth low-pass without phase displacement."""
    time, step = validate_uniform_time_grid(time_s)
    array = np.asarray(values, dtype=float)
    if array.shape[0] != time.size or array.ndim not in (1, 2):
        raise ValueError("low-pass values must share the time grid")
    if not np.all(np.isfinite(array)):
        raise ValueError("low-pass values must be finite")
    cutoff = float(cutoff_hz)
    sampling_hz = 1.0 / step
    if not math.isfinite(cutoff) or cutoff <= 0 or cutoff >= sampling_hz / 2.0:
        raise ValueError("low-pass cutoff must be between zero and Nyquist")
    if int(order) < 1:
        raise ValueError("low-pass order must be positive")
    sos = butter(int(order), cutoff, btype="low", fs=sampling_hz, output="sos")
    try:
        filtered = sosfiltfilt(sos, array, axis=0)
    except ValueError as exc:
        raise ValueError("time grid is too short for zero-phase filtering") from exc
    return np.asarray(filtered, dtype=float)


def time_derivative(time_s: Sequence[float], values: Sequence[Sequence[float]]) -> np.ndarray:
    """Differentiate a uniformly sampled vector using fixed second-order edges."""
    time, step = validate_uniform_time_grid(time_s)
    array = np.asarray(values, dtype=float)
    if array.shape[0] != time.size:
        raise ValueError("derivative values must share the time grid")
    if not np.all(np.isfinite(array)):
        raise ValueError("derivative values must be finite")
    return np.asarray(np.gradient(array, step, axis=0, edge_order=2), dtype=float)


def motion_whole_body_kinematics(
    opensim: Any,
    model_path: Path,
    motion_path: Path,
    expected_time_s: Sequence[float],
    *,
    gravity_m_s2: Sequence[float],
    cutoff_hz: float,
    lowpass_order: int,
) -> Dict[str, Any]:
    """Realize filtered OpenSim motion and return COM and central momentum."""
    header, table = read_opensim_sto_trc(str(motion_path))
    time = pd.to_numeric(table["time"], errors="coerce").to_numpy(float)
    expected = np.asarray(expected_time_s, dtype=float)
    if time.shape != expected.shape or not np.allclose(
        time, expected, atol=1e-10, rtol=0.0
    ):
        raise ValueError("OpenSim motion does not match the frozen kinematics grid")
    model = opensim.Model(str(model_path))
    state = model.initSystem()
    observed_gravity = np.asarray(
        [float(model.getGravity().get(index)) for index in range(3)], dtype=float
    )
    expected_gravity = np.asarray(gravity_m_s2, dtype=float)
    if not np.allclose(observed_gravity, expected_gravity, atol=1e-12, rtol=0.0):
        raise ValueError(
            "model gravity differs from the frozen gravity: {}".format(
                observed_gravity.tolist()
            )
        )
    in_degrees = False
    if not header.empty:
        lookup = {str(key).lower(): value for key, value in header.iloc[0].items()}
        in_degrees = str(lookup.get("indegrees", "no")).strip().lower() in {
            "yes",
            "true",
            "1",
        }
    coordinates = model.getCoordinateSet()
    column_lookup = {str(column).lower(): str(column) for column in table.columns}
    coordinate_rows = []
    filtered_values = {}
    filtered_speeds = {}
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
        filtered = zero_phase_lowpass(
            time, values, cutoff_hz=float(cutoff_hz), order=int(lowpass_order)
        )
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
        raise ValueError("no model coordinates were mapped from motion")

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
            "cutoff_hz": float(cutoff_hz),
            "order": int(lowpass_order),
            "applied_to": "coordinate-values-before-speed-and-state-realization",
            "selection": "protocol-frozen-not-residual-fitted",
        },
    }


def mot_external_wrench(
    mot_path: Path,
    target_time_s: Sequence[float],
    *,
    prefixes: Sequence[str],
    source_lowpass_cutoff_hz: Optional[float] = None,
    lowpass_order: int = 4,
) -> Dict[str, Any]:
    """Recombine public COP-based foot loads into a ground-origin wrench."""
    _, table = read_opensim_sto_trc(str(mot_path))
    source_time = pd.to_numeric(table["time"], errors="coerce").to_numpy(float)
    target = np.asarray(target_time_s, dtype=float)
    if source_time.size < 2 or np.any(np.diff(source_time) <= 0):
        raise ValueError("MOT time must be strictly increasing")

    lookup = {str(column).lower(): str(column) for column in table.columns}

    def vector(prefix: str, stem: str) -> np.ndarray:
        requested = ["{}_{}{}".format(prefix.lower(), stem, axis) for axis in "xyz"]
        missing = [name for name in requested if name not in lookup]
        if missing:
            raise ValueError("MOT lacks external-load columns: {}".format(missing))
        source = table[[lookup[name] for name in requested]].apply(
            pd.to_numeric, errors="coerce"
        ).to_numpy(float)
        if not np.all(np.isfinite(source)):
            raise ValueError("MOT external-load columns must be finite")
        return np.column_stack(
            [np.interp(target, source_time, source[:, axis]) for axis in range(3)]
        )

    forces = []
    moments_at_origin = []
    for prefix in prefixes:
        force = vector(str(prefix), "ground_force_v")
        point = vector(str(prefix), "ground_force_p")
        free_moment = vector(str(prefix), "ground_torque_")
        forces.append(force)
        moments_at_origin.append(np.cross(point, force) + free_moment)
    total_force = np.sum(forces, axis=0)
    total_moment = np.sum(moments_at_origin, axis=0)
    source_filter = None
    if source_lowpass_cutoff_hz is not None:
        # The vectors above are already on the target grid. Reconstruct the
        # combined source-grid wrench so filtering precedes downsampling.
        source_forces = []
        source_moments = []
        for prefix in prefixes:
            requested_force = [
                "{}_ground_force_v{}".format(str(prefix).lower(), axis)
                for axis in "xyz"
            ]
            requested_point = [
                "{}_ground_force_p{}".format(str(prefix).lower(), axis)
                for axis in "xyz"
            ]
            requested_torque = [
                "{}_ground_torque_{}".format(str(prefix).lower(), axis)
                for axis in "xyz"
            ]
            force_source = table[[lookup[name] for name in requested_force]].apply(
                pd.to_numeric, errors="coerce"
            ).to_numpy(float)
            point_source = table[[lookup[name] for name in requested_point]].apply(
                pd.to_numeric, errors="coerce"
            ).to_numpy(float)
            torque_source = table[[lookup[name] for name in requested_torque]].apply(
                pd.to_numeric, errors="coerce"
            ).to_numpy(float)
            source_forces.append(force_source)
            source_moments.append(np.cross(point_source, force_source) + torque_source)
        combined_force_source = np.sum(source_forces, axis=0)
        combined_moment_source = np.sum(source_moments, axis=0)
        combined_force_source = zero_phase_lowpass(
            source_time,
            combined_force_source,
            cutoff_hz=float(source_lowpass_cutoff_hz),
            order=int(lowpass_order),
        )
        combined_moment_source = zero_phase_lowpass(
            source_time,
            combined_moment_source,
            cutoff_hz=float(source_lowpass_cutoff_hz),
            order=int(lowpass_order),
        )
        total_force = np.column_stack(
            [
                np.interp(target, source_time, combined_force_source[:, axis])
                for axis in range(3)
            ]
        )
        total_moment = np.column_stack(
            [
                np.interp(target, source_time, combined_moment_source[:, axis])
                for axis in range(3)
            ]
        )
        source_filter = {
            "type": "butterworth-zero-phase-sosfiltfilt",
            "cutoff_hz": float(source_lowpass_cutoff_hz),
            "order": int(lowpass_order),
            "applied_before_downsampling": True,
        }
    return {
        "time_s": target,
        "force_n": total_force,
        "moment_at_ground_origin_nm": total_moment,
        "provenance": {
            "representation": "recombined-public-cop-free-moment",
            "prefixes": [str(value) for value in prefixes],
            "source_sha256": sha256_file(mot_path),
            "interpolation": "componentwise-linear-to-kinematics-grid",
            "source_filter": source_filter,
        },
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


def raw_c3d_external_wrench(
    c3d_path: Path,
    target_time_s: Sequence[float],
    *,
    platform_index: int,
    c3d_to_ground_rotation: Sequence[Sequence[float]],
    c3d_time_offset_s: float,
    vertical_axis: int,
    contact_threshold_n: float = 20.0,
    source_lowpass_cutoff_hz: Optional[float] = None,
    lowpass_order: int = 4,
) -> Dict[str, Any]:
    """Transform one combined raw force-platform wrench to ground coordinates."""
    try:
        import ezc3d  # type: ignore[import-not-found]
    except ModuleNotFoundError as exc:
        raise RuntimeError("G6c-S raw C3D audit requires ezc3d") from exc
    rotation = np.asarray(c3d_to_ground_rotation, dtype=float)
    if rotation.shape != (3, 3) or not np.allclose(
        rotation.T @ rotation, np.eye(3), atol=1e-8, rtol=0.0
    ):
        raise ValueError("C3D-to-ground rotation must be orthonormal")
    if vertical_axis not in (0, 1, 2):
        raise ValueError("vertical_axis must be 0, 1, or 2")
    c3d = ezc3d.c3d(str(c3d_path), extract_forceplat_data=True)
    platforms = c3d["data"].get("platform", [])
    if int(platform_index) < 0 or int(platform_index) >= len(platforms):
        raise ValueError("combined C3D platform index is out of range")
    platform = platforms[int(platform_index)]
    analog_rate = float(c3d["parameters"]["ANALOG"]["RATE"]["value"][0])
    force = (
        np.asarray(platform["force"], dtype=float).T
        * _unit_scale(platform["unit_force"], "force")
    ) @ rotation.T
    cop = (
        np.asarray(platform["center_of_pressure"], dtype=float).T
        * _unit_scale(platform["unit_position"], "position")
    ) @ rotation.T
    free = (
        np.asarray(platform["Tz"], dtype=float).T
        * _unit_scale(platform["unit_moment"], "moment")
    ) @ rotation.T
    source_time = (
        np.arange(force.shape[0], dtype=float) / analog_rate
        + float(c3d_time_offset_s)
    )
    contact = np.abs(force[:, int(vertical_axis)]) >= float(contact_threshold_n)
    if int(contact.sum()) < 2:
        raise ValueError("raw C3D platform has insufficient contact samples")
    moment_at_origin = np.zeros_like(force)
    finite_contact = contact & np.all(np.isfinite(cop), axis=1) & np.all(
        np.isfinite(free), axis=1
    )
    if not np.array_equal(finite_contact, contact):
        raise ValueError("raw C3D COP/free moment is nonfinite during contact")
    moment_at_origin[contact] = (
        np.cross(cop[contact], force[contact]) + free[contact]
    )
    if not np.all(np.isfinite(force)):
        raise ValueError("raw C3D force must be finite")
    source_filter = None
    if source_lowpass_cutoff_hz is not None:
        force = zero_phase_lowpass(
            source_time,
            force,
            cutoff_hz=float(source_lowpass_cutoff_hz),
            order=int(lowpass_order),
        )
        moment_at_origin = zero_phase_lowpass(
            source_time,
            moment_at_origin,
            cutoff_hz=float(source_lowpass_cutoff_hz),
            order=int(lowpass_order),
        )
        source_filter = {
            "type": "butterworth-zero-phase-sosfiltfilt",
            "cutoff_hz": float(source_lowpass_cutoff_hz),
            "order": int(lowpass_order),
            "applied_before_downsampling": True,
        }
    target = np.asarray(target_time_s, dtype=float)

    def align(values: np.ndarray) -> np.ndarray:
        return np.column_stack(
            [np.interp(target, source_time, values[:, axis]) for axis in range(3)]
        )

    return {
        "time_s": target,
        "force_n": align(force),
        "moment_at_ground_origin_nm": align(moment_at_origin),
        "provenance": {
            "representation": "raw-combined-platform-origin-wrench",
            "platform_index_zero_based": int(platform_index),
            "source_sha256": sha256_file(c3d_path),
            "rotation": rotation.tolist(),
            "time_offset_s": float(c3d_time_offset_s),
            "contact_threshold_n": float(contact_threshold_n),
            "interpolation": "componentwise-linear-to-kinematics-grid",
            "source_filter": source_filter,
        },
    }


def calculate_whole_body_balance(
    time_s: Sequence[float],
    com_position_m: Sequence[Sequence[float]],
    central_angular_momentum_nms: Sequence[Sequence[float]],
    external_force_n: Sequence[Sequence[float]],
    external_moment_at_ground_origin_nm: Sequence[Sequence[float]],
    *,
    mass_kg: float,
    gravity_m_s2: Sequence[float],
    vertical_axis: int,
    evaluation_window_s: Sequence[float],
    external_lowpass_cutoff_hz: float,
    lowpass_order: int = 4,
    external_preprocessed: bool = False,
) -> Dict[str, Any]:
    """Calculate whole-body force and moment nonclosure on a frozen window."""
    time, _ = validate_uniform_time_grid(time_s)
    com = _finite_matrix(com_position_m, "COM position")
    angular_momentum = _finite_matrix(
        central_angular_momentum_nms, "central angular momentum"
    )
    force = _finite_matrix(external_force_n, "external force")
    moment_origin = _finite_matrix(
        external_moment_at_ground_origin_nm, "external origin moment"
    )
    if any(array.shape[0] != time.size for array in (com, angular_momentum, force, moment_origin)):
        raise ValueError("all whole-body arrays must share the time grid")
    mass = float(mass_kg)
    gravity = np.asarray(gravity_m_s2, dtype=float)
    if not math.isfinite(mass) or mass <= 0:
        raise ValueError("model mass must be finite and positive")
    if gravity.shape != (3,) or not np.all(np.isfinite(gravity)):
        raise ValueError("gravity must be a finite three-vector")
    if vertical_axis not in (0, 1, 2):
        raise ValueError("vertical_axis must be 0, 1, or 2")
    if len(evaluation_window_s) != 2:
        raise ValueError("evaluation window must contain start and end")
    start, end = [float(value) for value in evaluation_window_s]
    evaluation = (time >= start - 1e-10) & (time <= end + 1e-10)
    if int(evaluation.sum()) < 3:
        raise ValueError("evaluation window has fewer than three samples")

    # Kinematics have already been filtered at the declared cutoff before the
    # OpenSim states are realized. External force and the ground-origin moment
    # receive the identical fixed filter here, before shifting the moment to COM.
    if external_preprocessed:
        filtered_force = force
        filtered_moment_origin = moment_origin
        filter_location = "source-grid-before-downsampling"
    else:
        filtered_force = zero_phase_lowpass(
            time, force, cutoff_hz=external_lowpass_cutoff_hz, order=lowpass_order
        )
        filtered_moment_origin = zero_phase_lowpass(
            time,
            moment_origin,
            cutoff_hz=external_lowpass_cutoff_hz,
            order=lowpass_order,
        )
        filter_location = "kinematics-grid-after-interpolation"
    com_acceleration = time_derivative(time, time_derivative(time, com))
    angular_momentum_rate = time_derivative(time, angular_momentum)
    external_moment_com = filtered_moment_origin - np.cross(com, filtered_force)
    force_residual = mass * com_acceleration - (
        filtered_force + mass * gravity[None, :]
    )
    moment_residual = angular_momentum_rate - external_moment_com

    selected_force = force_residual[evaluation]
    selected_moment = moment_residual[evaluation]
    force_rms = float(np.sqrt(np.mean(np.sum(np.square(selected_force), axis=1))))
    moment_rms = float(np.sqrt(np.mean(np.sum(np.square(selected_moment), axis=1))))
    body_weight = mass * float(np.linalg.norm(gravity))
    com_height = float(np.mean(com[evaluation, int(vertical_axis)]))
    if not math.isfinite(com_height) or com_height <= 0:
        raise ValueError("mean evaluation-window COM height must be positive")
    moment_reference = body_weight * com_height
    return {
        "residual_force_rms_n": force_rms,
        "residual_moment_rms_nm": moment_rms,
        "residual_force_fraction": force_rms / body_weight,
        "residual_moment_fraction": moment_rms / moment_reference,
        "residual_force_axis_rms_n": np.sqrt(
            np.mean(np.square(selected_force), axis=0)
        ).tolist(),
        "residual_moment_axis_rms_nm": np.sqrt(
            np.mean(np.square(selected_moment), axis=0)
        ).tolist(),
        "body_weight_n": body_weight,
        "mean_com_height_m": com_height,
        "moment_reference_nm": moment_reference,
        "evaluation_sample_count": int(evaluation.sum()),
        "evaluation_start_time_s": float(time[evaluation][0]),
        "evaluation_end_time_s": float(time[evaluation][-1]),
        "evaluation_grid_sha256": canonical_sha256(
            [float(value) for value in time[evaluation]]
        ),
        "external_lowpass": {
            "type": "butterworth-zero-phase-sosfiltfilt",
            "order": int(lowpass_order),
            "cutoff_hz": float(external_lowpass_cutoff_hz),
            "selection": "protocol-frozen-not-residual-fitted",
            "location": filter_location,
        },
        "equations": {
            "force": "m*a_com - (sum_external_force + m*gravity)",
            "moment": "dH_com/dt - (M_ground_origin - r_com_cross_F)",
        },
        "timeseries": {
            "time_s": time,
            "evaluation_mask": evaluation,
            "com_acceleration_m_s2": com_acceleration,
            "angular_momentum_rate_nm": angular_momentum_rate,
            "filtered_external_force_n": filtered_force,
            "filtered_external_moment_origin_nm": filtered_moment_origin,
            "external_moment_com_nm": external_moment_com,
            "force_residual_n": force_residual,
            "moment_residual_nm": moment_residual,
        },
    }


def classify_balance_nonclosure(
    raw_balance: Mapping[str, Any],
    mot_balance: Mapping[str, Any],
    rra_balance: Mapping[str, Any],
    *,
    force_fraction_threshold: float,
    moment_fraction_threshold: float,
) -> Dict[str, Any]:
    """Localize the common residual without changing the frozen thresholds."""
    force_limit = float(force_fraction_threshold)
    moment_limit = float(moment_fraction_threshold)

    def assessed(value: Mapping[str, Any]) -> Dict[str, bool]:
        return {
            "force": float(value.get("residual_force_fraction", math.inf))
            <= force_limit,
            "moment": float(value.get("residual_moment_fraction", math.inf))
            <= moment_limit,
        }

    raw_checks = assessed(raw_balance)
    mot_checks = assessed(mot_balance)
    rra_checks = assessed(rra_balance)
    raw_pass = all(raw_checks.values())
    mot_pass = all(mot_checks.values())
    rra_pass = all(rra_checks.values())
    if raw_pass and mot_pass and not rra_pass:
        classification = "rra-configuration-specific-nonclosure"
        next_action = "audit-rra-tasks-actuators-constraints-and-adjustment-policy"
    elif raw_pass and not mot_pass:
        classification = "published-mot-reconstruction-nonclosure"
        next_action = "audit-c3d-to-mot-side-split-cop-and-free-moment"
    elif not raw_pass and not mot_pass:
        classification = "kinematics-model-or-time-synchronization-nonclosure"
        next_action = "audit-kinematics-inertial-parameters-and-force-time-alignment"
    elif not raw_pass and mot_pass:
        classification = "raw-c3d-transform-nonclosure"
        next_action = "audit-raw-platform-transform-origin-and-time-offset"
    elif raw_pass and mot_pass and rra_pass:
        classification = "whole-body-and-rra-closed"
        next_action = "g6c-residual-subproblem-closed"
    else:
        classification = "unresolved-balance-nonclosure"
        next_action = "retain-failed-gate-and-expand-negative-controls"
    return {
        "classification": classification,
        "raw_checks": raw_checks,
        "mot_checks": mot_checks,
        "rra_checks": rra_checks,
        "raw_whole_body_closed": raw_pass,
        "mot_whole_body_closed": mot_pass,
        "rra_closed": rra_pass,
        "mechanics_closed": raw_pass and mot_pass and rra_pass,
        "next_action": next_action,
        "thresholds": {
            "residual_force_fraction": force_limit,
            "residual_moment_fraction": moment_limit,
        },
        "threshold_policy": "inherited-unchanged-from-g6c",
    }


def build_whole_body_balance_plan(
    protocol_path: Path,
    manifest_path: Path,
    data_root: Path,
    prior_g6c_result_path: Path,
) -> Dict[str, Any]:
    """Build a checksum-bound G6c-S plan from the released source bundle."""
    protocol_path = protocol_path.resolve()
    manifest_path = manifest_path.resolve()
    prior_path = prior_g6c_result_path.resolve()
    protocol = _load_yaml(protocol_path)
    if str(protocol.get("schema_version")) != WHOLE_BODY_BALANCE_SCHEMA_VERSION:
        raise ValueError("unsupported G6c-S protocol version")
    audit = validate_public_source_bundle(manifest_path, data_root)
    if not audit["pass"]:
        raise ValueError("G6c-S source bundle failed checksum verification")
    design = protocol.get("design") or {}
    role_map = design.get("roles") or {}
    required = ("model", "markers", "ground_reaction", "raw_c3d", "ik_setup")
    if any(not str(role_map.get(key, "")).strip() for key in required):
        raise ValueError("G6c-S protocol must declare every required source role")
    roles = audit["assets_by_role"]
    missing = sorted(set(str(role_map[key]) for key in required) - set(roles))
    if missing:
        raise ValueError("G6c-S source manifest lacks roles: {}".format(missing))
    paths = {
        key: str(Path(str(roles[str(role_map[key])]["absolute_path"])).resolve())
        for key in required
    }
    if not prior_path.is_file():
        raise ValueError("prior G6c result is absent")
    prior = json.loads(prior_path.read_text(encoding="utf-8"))
    if prior.get("scope") != "g6c-mechanics-audit":
        raise ValueError("prior result is not a G6c mechanics audit")
    prior_worker = prior.get("worker") or {}
    prior_hashes = prior_worker.get("input_sha256") or {}
    input_hashes = {key: sha256_file(Path(path)) for key, path in paths.items()}
    for key in ("model", "markers", "ground_reaction", "raw_c3d", "ik_setup"):
        if str(prior_hashes.get(key, "")) != input_hashes[key]:
            raise ValueError("G6c-S input differs from prior G6c: {}".format(key))
    thresholds = protocol.get("thresholds") or {}
    prior_thresholds = prior.get("thresholds") or {}
    for key in ("residual_force_fraction", "residual_moment_fraction"):
        if float(thresholds.get(key, math.nan)) != float(
            prior_thresholds.get(key, math.nan)
        ):
            raise ValueError("G6c-S residual thresholds must equal prior G6c")
    kinematics_window = [float(value) for value in design.get("kinematics_window_s", [])]
    evaluation_window = [float(value) for value in design.get("evaluation_window_s", [])]
    if (
        len(kinematics_window) != 2
        or len(evaluation_window) != 2
        or kinematics_window[0] >= evaluation_window[0]
        or kinematics_window[1] <= evaluation_window[1]
    ):
        raise ValueError("G6c-S evaluation window must be strictly inside its padded window")
    full_grid = trc_time_grid(
        Path(paths["markers"]), kinematics_window[0], kinematics_window[1]
    )
    evaluation_grid = full_grid[
        (full_grid >= evaluation_window[0] - 1e-10)
        & (full_grid <= evaluation_window[1] + 1e-10)
    ]
    expected_rows = int(design.get("expected_evaluation_rows", 0))
    if evaluation_grid.size != expected_rows:
        raise ValueError(
            "G6c-S evaluation grid expected {} rows, observed {}".format(
                expected_rows, evaluation_grid.size
            )
        )
    rra = prior_worker.get("static_reference_rra") or {}
    prior_rra = {
        "residual_force_fraction": float(rra.get("residual_force_fraction", math.inf)),
        "residual_moment_fraction": float(rra.get("residual_moment_fraction", math.inf)),
        "residual_force_rms_n": float(rra.get("residual_force_rms", math.inf)),
        "residual_moment_rms_nm": float(rra.get("residual_moment_rms", math.inf)),
        "source": "frozen-prior-g6c-final-rra-actuation",
        "actuation_sha256": str(rra.get("rra_actuation_sha256", "")),
    }
    if not all(
        math.isfinite(prior_rra[key])
        for key in (
            "residual_force_fraction",
            "residual_moment_fraction",
            "residual_force_rms_n",
            "residual_moment_rms_nm",
        )
    ):
        raise ValueError("prior G6c lacks finite RRA residual metrics")
    return {
        "schema_version": WHOLE_BODY_BALANCE_SCHEMA_VERSION,
        "scope": "g6c-s-whole-body-balance-plan",
        "protocol_sha256": sha256_file(protocol_path),
        "source_manifest_sha256": audit["manifest_sha256"],
        "prior_g6c_result_sha256": sha256_file(prior_path),
        "input_paths": paths,
        "input_sha256": input_hashes,
        "kinematics_window_s": kinematics_window,
        "evaluation_window_s": evaluation_window,
        "kinematics_time_grid_s": [float(value) for value in full_grid],
        "kinematics_time_grid_sha256": canonical_sha256(
            [float(value) for value in full_grid]
        ),
        "evaluation_time_grid_s": [float(value) for value in evaluation_grid],
        "evaluation_time_grid_sha256": canonical_sha256(
            [float(value) for value in evaluation_grid]
        ),
        "expected_evaluation_rows": expected_rows,
        "vertical_axis": int(design.get("vertical_axis", 1)),
        "gravity_m_s2": [float(value) for value in design.get("gravity_m_s2", [])],
        "external_loads": design.get("external_loads") or {},
        "filter": design.get("filter") or {},
        "opensim": protocol.get("opensim") or {},
        "thresholds": dict(thresholds),
        "prior_rra": prior_rra,
        "claim_boundary": str(protocol.get("claim_boundary", "")),
    }


def evaluate_whole_body_balance(
    plan: Mapping[str, Any], worker: Mapping[str, Any]
) -> Dict[str, Any]:
    """Fail closed on provenance while preserving a diagnostic classification."""
    diagnosis = worker.get("diagnosis") or {}
    checks = {
        "worker_scope": worker.get("scope") == "g6c-s-whole-body-balance-worker",
        "worker_input_hashes": worker.get("input_sha256") == plan.get("input_sha256"),
        "prior_g6c_hash": worker.get("prior_g6c_result_sha256")
        == plan.get("prior_g6c_result_sha256"),
        "kinematics_grid_exact": worker.get("kinematics_grid_exact") is True,
        "evaluation_grid_exact": worker.get("evaluation_grid_exact") is True,
        "raw_wrench_traceable": (
            (worker.get("raw_balance") or {}).get("source_provenance") or {}
        ).get("source_sha256")
        == (plan.get("input_sha256") or {}).get("raw_c3d"),
        "mot_wrench_traceable": (
            (worker.get("mot_balance") or {}).get("source_provenance") or {}
        ).get("source_sha256")
        == (plan.get("input_sha256") or {}).get("ground_reaction"),
        "thresholds_unchanged": diagnosis.get("thresholds") == plan.get("thresholds"),
        "diagnosis_complete": str(diagnosis.get("classification", "")).strip() != "",
    }
    diagnostic_complete = all(checks.values())
    mechanics_closed = diagnostic_complete and diagnosis.get("mechanics_closed") is True
    return {
        "schema_version": WHOLE_BODY_BALANCE_SCHEMA_VERSION,
        "scope": "g6c-s-whole-body-balance-audit",
        "pass": bool(mechanics_closed),
        "diagnostic_complete": bool(diagnostic_complete),
        "mechanics_closed": bool(mechanics_closed),
        "checks": checks,
        "diagnosis": dict(diagnosis),
        "thresholds": dict(plan.get("thresholds") or {}),
        "worker": dict(worker),
        "scientific_claim_boundary": (
            "G6c-S localizes whole-body mechanics nonclosure; it does not validate "
            "dynamic-only personalization or overwrite the released G6c result"
        ),
        "next_gate": "g6d" if mechanics_closed else "blocked-at-g6c-s",
    }


def write_whole_body_balance(result: Mapping[str, Any], output: Path) -> None:
    output = output.resolve()
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(
        json.dumps(result, indent=2, sort_keys=True) + "\n", encoding="utf-8"
    )
