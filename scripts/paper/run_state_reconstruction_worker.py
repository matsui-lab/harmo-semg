#!/usr/bin/env python3
"""Run the checksum-bound G6c-S3 RRA-native state reconstruction audit."""

from __future__ import annotations

import argparse
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
from harmo.g6_execution import _rra_actuator_columns  # noqa: E402
from harmo.ingest.readers.sto import read_opensim_sto_trc  # noqa: E402
from harmo.state_reconstruction import (  # noqa: E402
    aggregate_rigid_body_dynamics,
    classify_state_reconstruction,
    external_source_grid_mask,
    vector_identity_metrics,
)
from harmo.whole_body_balance import mot_external_wrench  # noqa: E402


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--plan", required=True)
    parser.add_argument("--work-dir", required=True)
    parser.add_argument("--report", required=True)
    return parser


def _numeric_table(path: Path) -> tuple[pd.DataFrame, np.ndarray]:
    _, table = read_opensim_sto_trc(str(path))
    if "time" not in table:
        raise ValueError("G6c-S3 native storage lacks time")
    numeric = table.apply(pd.to_numeric, errors="coerce")
    time = numeric["time"].to_numpy(float)
    if time.size == 0 or np.any(np.diff(time) <= 0) or not np.all(
        np.isfinite(numeric.to_numpy(float))
    ):
        raise ValueError("G6c-S3 native storage must be finite and ordered")
    return numeric, time


def _same_grid(reference: np.ndarray, candidate: np.ndarray) -> bool:
    return reference.shape == candidate.shape and np.allclose(
        reference, candidate, atol=1e-12, rtol=0.0
    )


def _coordinate_u_indices(opensim, model) -> dict[str, int]:
    state = model.initSystem()
    coordinates = model.getCoordinateSet()
    result = {}
    for coordinate_index in range(coordinates.getSize()):
        for reset_index in range(coordinates.getSize()):
            coordinates.get(reset_index).setSpeedValue(state, 0.0)
        coordinate = coordinates.get(coordinate_index)
        coordinate.setSpeedValue(state, 1.0)
        values = np.asarray(
            [float(state.getU()[index]) for index in range(state.getU().size())]
        )
        matches = np.flatnonzero(np.abs(values) > 0.5)
        if matches.size == 1:
            result[str(coordinate.getName())] = int(matches[0])
    if len(result) != coordinates.getSize():
        raise ValueError("G6c-S3 could not map every coordinate to one mobility")
    return result


def _vec3(value) -> np.ndarray:
    return np.asarray([float(value.get(index)) for index in range(3)])


def _body_rows(opensim, model, state, known_udot) -> tuple[list[dict], float]:
    matter = model.getMatterSubsystem()
    body_accelerations = opensim.VectorOfSpatialVec()
    matter.calcBodyAccelerationFromUDot(state, known_udot, body_accelerations)
    rows = []
    maximum_h_error = 0.0
    bodies = model.getBodySet()
    for body_index in range(bodies.getSize()):
        body = bodies.get(body_index)
        mass = float(body.getMass())
        if mass <= 0:
            continue
        spatial = body_accelerations.get(int(body.getMobilizedBodyIndex()))
        alpha = _vec3(spatial.get(0))
        origin_acceleration = _vec3(spatial.get(1))
        origin = body.findStationLocationInGround(state, opensim.Vec3(0.0))
        center = body.findStationLocationInGround(state, body.getMassCenter())
        offset = _vec3(center) - _vec3(origin)
        omega = _vec3(body.getAngularVelocityInGround(state))
        center_acceleration = (
            origin_acceleration
            + np.cross(alpha, offset)
            + np.cross(omega, np.cross(omega, offset))
        )
        moments = _vec3(body.getInertia().getMoments())
        products = _vec3(body.getInertia().getProducts())
        inertia_body = np.asarray(
            [
                [moments[0], -products[0], -products[1]],
                [-products[0], moments[1], -products[2]],
                [-products[1], -products[2], moments[2]],
            ]
        )
        rotation_value = body.getTransformInGround(state).R().asMat33()
        rotation = np.asarray(
            [
                [float(rotation_value.get(row, column)) for column in range(3)]
                for row in range(3)
            ]
        )
        inertia_ground = rotation @ inertia_body @ rotation.T
        matrix_h = inertia_ground @ omega
        api_h = _vec3(body.calcAngularMomentumAboutMassCenter(state))
        maximum_h_error = max(
            maximum_h_error, float(np.linalg.norm(matrix_h - api_h))
        )
        rows.append(
            {
                "mass_kg": mass,
                "center_position_m": _vec3(center),
                "center_acceleration_m_s2": center_acceleration,
                "rotational_hdot_nm": inertia_ground @ alpha
                + np.cross(omega, matrix_h),
            }
        )
    return rows, maximum_h_error


def _rms_vector(values: np.ndarray) -> float:
    return float(np.sqrt(np.mean(np.sum(np.square(values), axis=1))))


def main(argv=None) -> int:
    args = _parser().parse_args(argv)
    plan_path = Path(args.plan).resolve()
    work_dir = Path(args.work_dir).resolve()
    report_path = Path(args.report).resolve()
    plan = json.loads(plan_path.read_text(encoding="utf-8"))
    if plan.get("scope") != "g6c-s3-state-reconstruction-plan":
        raise ValueError("worker received an invalid G6c-S3 plan")
    paths = {name: Path(value).resolve() for name, value in plan["input_paths"].items()}
    for name, expected in plan["input_sha256"].items():
        if not paths[name].is_file() or sha256_file(paths[name]) != str(expected):
            raise ValueError("G6c-S3 worker input checksum mismatch: {}".format(name))

    import opensim

    version = str(opensim.GetVersion())
    required = str(plan["opensim_required_version"])
    if not version.startswith(required):
        raise RuntimeError(
            "G6c-S3 requires OpenSim {}, observed {}".format(required, version)
        )
    states, time = _numeric_table(paths["native_states"])
    q_table, q_time = _numeric_table(paths["native_q"])
    u_table, u_time = _numeric_table(paths["native_u"])
    udot_table, udot_time = _numeric_table(paths["native_udot"])
    actuation, actuation_time = _numeric_table(paths["rra_actuation"])
    native_grid_exact = all(
        _same_grid(time, candidate)
        for candidate in (q_time, u_time, udot_time, actuation_time)
    )
    if not native_grid_exact:
        raise ValueError("G6c-S3 native RRA artifacts do not share one grid")
    start, end = [float(value) for value in plan["comparison_window_s"]]
    mask = (time >= start - 1e-10) & (time <= end + 1e-10)
    selected_rows = np.flatnonzero(mask)
    if selected_rows.size < int(plan["expected_minimum_native_rows"]):
        raise ValueError("G6c-S3 native comparison window has too few rows")
    selected_time = time[mask]
    wrench = mot_external_wrench(
        paths["ground_reaction"], selected_time, prefixes=["l", "r"]
    )
    external_force = np.asarray(wrench["force_n"], dtype=float)
    external_moment_origin = np.asarray(
        wrench["moment_at_ground_origin_nm"], dtype=float
    )
    force_columns = _rra_actuator_columns(actuation, ("FX", "FY", "FZ"))
    moment_columns = _rra_actuator_columns(actuation, ("MX", "MY", "MZ"))
    residual_force = actuation.loc[mask, force_columns].to_numpy(float)
    residual_moment = actuation.loc[mask, moment_columns].to_numpy(float)

    model = opensim.Model(str(paths["adjusted_model"]))
    state = model.initSystem()
    coordinates = model.getCoordinateSet()
    u_indices = _coordinate_u_indices(opensim, model)
    state = model.initSystem()
    mapped_state_names = []
    for coordinate_index in range(coordinates.getSize()):
        coordinate = coordinates.get(coordinate_index)
        mapped_state_names.extend(
            [
                str(coordinate.getAbsolutePathString()) + "/value",
                str(coordinate.getAbsolutePathString()) + "/speed",
            ]
        )
    if any(name not in states.columns for name in mapped_state_names):
        raise ValueError("G6c-S3 native states do not contain every coordinate state")
    for coordinate_index in range(coordinates.getSize()):
        coordinate = coordinates.get(coordinate_index)
        name = str(coordinate.getName())
        if name not in udot_table.columns:
            raise ValueError("G6c-S3 native udot lacks coordinate {}".format(name))
    maximum_q_state_difference = 0.0
    maximum_u_state_difference = 0.0
    for coordinate_index in range(coordinates.getSize()):
        coordinate = coordinates.get(coordinate_index)
        name = str(coordinate.getName())
        value_path = str(coordinate.getAbsolutePathString()) + "/value"
        speed_path = str(coordinate.getAbsolutePathString()) + "/speed"
        if value_path not in states.columns or speed_path not in states.columns:
            raise ValueError("G6c-S3 native states lack coordinate {}".format(name))
        q_values = q_table[name].to_numpy(float)
        u_values = u_table[name].to_numpy(float)
        if int(coordinate.getMotionType()) == 1:
            q_values = np.deg2rad(q_values)
            u_values = np.deg2rad(u_values)
        maximum_q_state_difference = max(
            maximum_q_state_difference,
            float(
                np.max(
                    np.abs(q_values - states[value_path].to_numpy(float))
                )
            ),
        )
        maximum_u_state_difference = max(
            maximum_u_state_difference,
            float(
                np.max(
                    np.abs(u_values - states[speed_path].to_numpy(float))
                )
            ),
        )

    gravity = np.asarray(plan["gravity_m_s2"], dtype=float)
    application = plan["residual_force_application"]
    application_body = model.getBodySet().get(str(application["body"]))
    system_center = np.empty((selected_rows.size, 3))
    system_acceleration = np.empty_like(system_center)
    angular_momentum_rate = np.empty_like(system_center)
    application_point = np.empty_like(system_center)
    maximum_q_error = 0.0
    maximum_u_error = 0.0
    maximum_h_error = 0.0

    for output_row, source_row in enumerate(selected_rows):
        for state_name in mapped_state_names:
            model.setStateVariableValue(
                state, state_name, float(states.iloc[source_row][state_name])
            )
        state.setTime(float(time[source_row]))
        model.realizeVelocity(state)
        if state.getQErr().size():
            maximum_q_error = max(
                maximum_q_error,
                max(abs(float(state.getQErr()[i])) for i in range(state.getQErr().size())),
            )
        if state.getUErr().size():
            maximum_u_error = max(
                maximum_u_error,
                max(abs(float(state.getUErr()[i])) for i in range(state.getUErr().size())),
            )
        known_udot = opensim.Vector(state.getU().size(), 0.0)
        for coordinate_index in range(coordinates.getSize()):
            coordinate = coordinates.get(coordinate_index)
            name = str(coordinate.getName())
            value = float(udot_table.iloc[source_row][name])
            if int(coordinate.getMotionType()) == 1:
                value = math.radians(value)
            known_udot.set(u_indices[name], value)
        rows, h_error = _body_rows(opensim, model, state, known_udot)
        aggregate = aggregate_rigid_body_dynamics(rows)
        system_center[output_row] = aggregate["center_position_m"]
        system_acceleration[output_row] = aggregate["center_acceleration_m_s2"]
        angular_momentum_rate[output_row] = aggregate[
            "central_angular_momentum_rate_nm"
        ]
        application_point[output_row] = _vec3(
            application_body.findStationLocationInGround(
                state, application_body.getMassCenter()
            )
        )
        maximum_h_error = max(maximum_h_error, h_error)

    mass_kg = float(model.getTotalMass(state))
    body_weight = mass_kg * float(np.linalg.norm(gravity))
    mean_com_height = float(
        np.mean(system_center[:, int(plan["vertical_axis"])])
    )
    moment_reference = body_weight * mean_com_height
    observed_force_residual = mass_kg * system_acceleration - (
        external_force + mass_kg * gravity[None, :]
    )
    external_moment_com = external_moment_origin - np.cross(
        system_center, external_force
    )
    observed_moment_residual = angular_momentum_rate - external_moment_com
    predicted_moment_residual = residual_moment + np.cross(
        application_point - system_center, residual_force
    )
    source_grid_mask = external_source_grid_mask(
        selected_time, sampling_hz=1000.0, tolerance_s=1e-11
    )
    if int(np.sum(source_grid_mask)) < int(plan["expected_minimum_native_rows"]):
        raise ValueError("G6c-S3 external source grid has too few rows")
    adaptive_force_identity = vector_identity_metrics(
        observed_force_residual, residual_force, reference=body_weight
    )
    adaptive_moment_identity = vector_identity_metrics(
        observed_moment_residual,
        predicted_moment_residual,
        reference=moment_reference,
    )
    force_identity = vector_identity_metrics(
        observed_force_residual[source_grid_mask],
        residual_force[source_grid_mask],
        reference=body_weight,
    )
    moment_identity = vector_identity_metrics(
        observed_moment_residual[source_grid_mask],
        predicted_moment_residual[source_grid_mask],
        reference=moment_reference,
    )
    native_force_fraction = (
        _rms_vector(residual_force[source_grid_mask]) / body_weight
    )
    native_moment_fraction = (
        _rms_vector(residual_moment[source_grid_mask]) / moment_reference
    )
    total_native_moment_fraction = (
        _rms_vector(predicted_moment_residual[source_grid_mask]) / moment_reference
    )
    position_derived = plan["position_derived"]
    diagnosis = classify_state_reconstruction(
        force_identity=force_identity,
        moment_identity=moment_identity,
        native_force_fraction=native_force_fraction,
        native_moment_fraction=native_moment_fraction,
        position_force_fraction=float(position_derived["residual_force_fraction"]),
        position_moment_fraction=float(position_derived["residual_moment_fraction"]),
        thresholds=plan["thresholds"],
    )
    thresholds = plan["thresholds"]
    constraint_integrity_pass = bool(
        maximum_q_error <= float(thresholds["maximum_position_constraint_error"])
        and maximum_u_error
        <= float(thresholds["maximum_velocity_constraint_error"])
    )
    native_state_consistency_pass = bool(
        maximum_q_state_difference
        <= float(thresholds["maximum_q_state_difference"])
        and maximum_u_state_difference
        <= float(thresholds["maximum_u_state_difference"])
    )
    body_h_identity_pass = maximum_h_error <= float(
        thresholds["maximum_body_angular_momentum_error_nms"]
    )

    work_dir.mkdir(parents=True, exist_ok=True)
    timeseries_path = work_dir / "g6c_s3_native_identity_timeseries.csv"
    payload = {"time_s": selected_time}
    payload["external_source_grid"] = source_grid_mask.astype(int)
    for axis, label in enumerate("xyz"):
        payload["com_position_m_{}".format(label)] = system_center[:, axis]
        payload["com_acceleration_m_s2_{}".format(label)] = system_acceleration[
            :, axis
        ]
        payload["analytic_hdot_nm_{}".format(label)] = angular_momentum_rate[:, axis]
        payload["observed_force_residual_n_{}".format(label)] = (
            observed_force_residual[:, axis]
        )
        payload["rra_force_residual_n_{}".format(label)] = residual_force[:, axis]
        payload["observed_moment_residual_nm_{}".format(label)] = (
            observed_moment_residual[:, axis]
        )
        payload["rra_total_moment_residual_nm_{}".format(label)] = (
            predicted_moment_residual[:, axis]
        )
    pd.DataFrame(payload).to_csv(
        timeseries_path, index=False, float_format="%.12g", lineterminator="\n"
    )
    report = {
        "schema_version": "1.0.0",
        "scope": "g6c-s3-state-reconstruction-worker",
        "opensim_version": version,
        "input_sha256": plan["input_sha256"],
        "s2_result_sha256": plan["s2_result_sha256"],
        "native_grid_exact": bool(native_grid_exact),
        "native_sample_count": int(np.sum(source_grid_mask)),
        "adaptive_sample_count": int(selected_rows.size),
        "adaptive_substep_sample_count": int(np.sum(~source_grid_mask)),
        "native_window_s": [float(selected_time[0]), float(selected_time[-1])],
        "body_weight_n": body_weight,
        "mean_com_height_m": mean_com_height,
        "moment_reference_nm": moment_reference,
        "force_identity": force_identity,
        "moment_identity": moment_identity,
        "adaptive_all_identity": {
            "force": adaptive_force_identity,
            "moment": adaptive_moment_identity,
            "role": "interpolation-diagnostic-only",
        },
        "native_rra_quality": {
            "residual_force_rms_n": _rms_vector(residual_force[source_grid_mask]),
            "residual_force_fraction": native_force_fraction,
            "residual_moment_rms_nm": _rms_vector(
                residual_moment[source_grid_mask]
            ),
            "residual_moment_fraction": native_moment_fraction,
            "total_moment_with_force_lever_rms_nm": _rms_vector(
                predicted_moment_residual[source_grid_mask]
            ),
            "total_moment_with_force_lever_fraction": total_native_moment_fraction,
            "moment_quality_source": "MX-MY-MZ-residual-actuators",
            "assessment_grid": "external-load-native-1-ms-grid",
        },
        "position_derived_s2": position_derived,
        "diagnosis": diagnosis,
        "constraint_diagnostics": {
            "maximum_position_constraint_error": maximum_q_error,
            "maximum_velocity_constraint_error": maximum_u_error,
        },
        "constraint_integrity_pass": constraint_integrity_pass,
        "native_state_consistency": {
            "maximum_q_difference": maximum_q_state_difference,
            "maximum_u_difference": maximum_u_state_difference,
        },
        "native_state_consistency_pass": native_state_consistency_pass,
        "maximum_body_angular_momentum_error_nms": maximum_h_error,
        "body_angular_momentum_identity_pass": body_h_identity_pass,
        "external_load_provenance": wrench["provenance"],
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
