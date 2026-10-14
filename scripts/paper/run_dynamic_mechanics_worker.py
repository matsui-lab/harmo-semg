#!/usr/bin/env python3
"""Run both OpenSim pipelines for the checksum-bound G6b-3 pilot."""

from __future__ import annotations

import argparse
import hashlib
import json
import math
import re
import shutil
from pathlib import Path


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--plan", required=True)
    parser.add_argument("--work-dir", required=True)
    parser.add_argument("--report", required=True)
    return parser


def _only_match(directory: Path, pattern: str, label: str) -> Path:
    matches = sorted(directory.glob(pattern))
    if len(matches) != 1:
        raise RuntimeError(
            "{} expected one artifact, observed {}".format(label, len(matches))
        )
    return matches[0]


def _artifact(path: Path) -> dict:
    return {"file": path.name, "sha256": _sha256(path)}


def _portable_error(message: str, plan: dict, work_dir: Path) -> str:
    portable = str(message).replace(str(work_dir), "<work_dir>")
    for role, raw_path in plan.get("input_paths", {}).items():
        portable = portable.replace(str(Path(str(raw_path)).resolve()), "<{}>".format(role))
    return portable


def normalize_trc_time_header(source: Path, output: Path) -> dict:
    """Repair only the public file's nonstandard Times token for OpenSim."""
    content = source.read_bytes()
    needle = b"Frame#\tTimes\t"
    replacement = b"Frame#\tTime\t"
    count = content.count(needle)
    if count != 1:
        raise ValueError(
            "expected exactly one Frame#/Times TRC header, observed {}".format(count)
        )
    normalized = content.replace(needle, replacement, 1)
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_bytes(normalized)
    return {
        "operation": "replace-exact-Frame#-Times-header-token-with-Time",
        "replacement_count": count,
        "source_sha256": _sha256(source),
        "normalized_sha256": _sha256(output),
        "source_size_bytes": len(content),
        "normalized_size_bytes": len(normalized),
        "file": output.name,
    }


def _generate_external_loads(opensim, grf_path: Path, output: Path) -> None:
    loads = opensim.ExternalLoads()
    loads.setName("g6b3_external_loads")
    loads.setDataFileName(str(grf_path))
    for side, body in (("R", "calcn_r"), ("L", "calcn_l")):
        force = opensim.ExternalForce()
        force.setName("{}_GRF".format(side))
        force.setAppliedToBodyName(body)
        force.setForceExpressedInBodyName("ground")
        force.setPointExpressedInBodyName("ground")
        force.setForceIdentifier("{}_ground_force_v".format(side))
        force.setPointIdentifier("{}_ground_force_p".format(side))
        force.setTorqueIdentifier("{}_ground_torque_".format(side))
        loads.cloneAndAppend(force)
    loads.printToXML(str(output))


def _independent_coordinates(model, state):
    coordinates = []
    coordinate_set = model.getCoordinateSet()
    for index in range(coordinate_set.getSize()):
        coordinate = coordinate_set.get(index)
        if int(coordinate.getMotionType()) == 3 or coordinate.getLocked(state):
            continue
        coordinates.append((coordinate.getName(), int(coordinate.getMotionType())))
    return coordinates


def _generate_rra_files(opensim, model_path: Path, config: dict, directory: Path):
    model = opensim.Model(str(model_path))
    state = model.initSystem()
    coordinates = _independent_coordinates(model, state)
    pelvis = model.getBodySet().get("pelvis")
    pelvis_com_vector = pelvis.getMassCenter()
    pelvis_com = [float(pelvis_com_vector.get(index)) for index in range(3)]
    if len(pelvis_com) != 3 or not all(math.isfinite(value) for value in pelvis_com):
        raise ValueError("pelvis COM is not a finite three-vector")
    actuator_path = directory / "rra_actuators.xml"
    task_path = directory / "rra_tasks.xml"

    force_set = opensim.ForceSet()
    force_set.setName("g6b3_rra_actuators")
    force_optimal = config["residual_force_optimal_force"]
    for name, axis in (("FX", 0), ("FY", 1), ("FZ", 2)):
        direction = [0.0, 0.0, 0.0]
        direction[axis] = 1.0
        actuator = opensim.PointActuator()
        actuator.setName(name)
        actuator.set_body("pelvis")
        # OpenSim RRA best practice: residual forces act at the scaled pelvis
        # mass center, not at an assumed pelvis-frame origin.
        actuator.set_point(opensim.Vec3(*pelvis_com))
        actuator.set_point_is_global(False)
        actuator.set_direction(opensim.Vec3(*direction))
        actuator.set_force_is_global(True)
        actuator.setOptimalForce(float(force_optimal[name]))
        actuator.setMinControl(float("-inf"))
        actuator.setMaxControl(float("inf"))
        force_set.cloneAndAppend(actuator)
    moment_optimal = config["residual_moment_optimal_force"]
    for name, axis in (("MX", 0), ("MY", 1), ("MZ", 2)):
        direction = [0.0, 0.0, 0.0]
        direction[axis] = 1.0
        actuator = opensim.TorqueActuator()
        actuator.setName(name)
        actuator.set_bodyA("pelvis")
        actuator.set_bodyB("ground")
        actuator.setAxis(opensim.Vec3(*direction))
        actuator.setTorqueIsGlobal(True)
        actuator.setOptimalForce(float(moment_optimal[name]))
        actuator.setMinControl(float("-inf"))
        actuator.setMaxControl(float("inf"))
        force_set.cloneAndAppend(actuator)
    for coordinate, motion_type in coordinates:
        if coordinate.startswith("pelvis_") or motion_type != 1:
            continue
        actuator = opensim.CoordinateActuator()
        actuator.setName("{}_reserve".format(coordinate))
        actuator.set_coordinate(coordinate)
        actuator.setOptimalForce(float(config["reserve_optimal_force"]))
        actuator.setMinControl(float("-inf"))
        actuator.setMaxControl(float("inf"))
        force_set.cloneAndAppend(actuator)
    force_set.printToXML(str(actuator_path))

    task_set = opensim.CMC_TaskSet()
    task_set.setName("g6b3_rra_tasks")
    for coordinate, _ in coordinates:
        task = opensim.CMC_Joint()
        task.setName(coordinate)
        task.setCoordinateName(coordinate)
        task.setOn(True)
        task.setActive(True, False, False)
        weight_key = "pelvis" if coordinate.startswith("pelvis_") else "other"
        task.setWeight(float(config["task_weight"][weight_key]))
        task.setKP(float(config["task_kp"]))
        task.setKV(float(config["task_kv"]))
        task.setKA(1.0)
        task_set.cloneAndAppend(task)
    task_set.printToXML(str(task_path))
    return actuator_path, task_path, pelvis_com


def _trc_times(path: Path, start_time: float, end_time: float) -> list[float]:
    lines = path.read_text(encoding="utf-8").splitlines()
    header = next(
        (index for index, line in enumerate(lines) if line.startswith("Frame#\tTime\t")),
        None,
    )
    if header is None:
        raise ValueError("normalized TRC has no Frame#/Time header")
    times = []
    for line in lines[header + 2 :]:
        if not line.strip():
            continue
        fields = line.split("\t")
        if len(fields) < 2:
            continue
        try:
            value = float(fields[1])
        except ValueError:
            continue
        if value >= start_time - 1e-10 and value <= end_time + 1e-10:
            times.append(value)
    if len(times) < 2 or any(right <= left for left, right in zip(times, times[1:])):
        raise ValueError("source marker time grid is not strictly increasing")
    return times


def _read_storage(path: Path):
    lines = path.read_text(encoding="utf-8").splitlines()
    end = next(
        (index for index, line in enumerate(lines) if line.strip() == "endheader"),
        None,
    )
    if end is None or end + 1 >= len(lines):
        raise ValueError("OpenSim storage has no complete header")
    columns = lines[end + 1].split()
    if not columns or columns[0].lower() != "time":
        raise ValueError("OpenSim storage first column must be time")
    rows = []
    for line in lines[end + 2 :]:
        fields = line.split()
        if len(fields) != len(columns):
            continue
        rows.append([float(value) for value in fields])
    if len(rows) < 2 or any(rows[index + 1][0] <= rows[index][0] for index in range(len(rows) - 1)):
        raise ValueError("OpenSim storage has no ordered data")
    return lines[: end + 1], columns, rows


def _interpolate(x: list[float], y: list[float], target: float) -> float:
    import bisect

    right = bisect.bisect_left(x, target)
    if right == 0:
        return y[0]
    if right == len(x):
        return y[-1]
    left = right - 1
    fraction = (target - x[left]) / (x[right] - x[left])
    return y[left] + fraction * (y[right] - y[left])


def resample_storage_to_source_grid(
    source: Path, target_times: list[float], output: Path
) -> dict:
    """Resample RRA kinematics to the checksum-bound marker time grid."""
    header, columns, rows = _read_storage(source)
    source_times = [row[0] for row in rows]
    if target_times[0] < source_times[0] - 1e-10 or target_times[-1] > source_times[-1] + 1e-10:
        raise ValueError("source-grid resampling would require extrapolation")
    values = [[row[index] for row in rows] for index in range(1, len(columns))]
    filtered = [
        line
        for line in header[:-1]
        if not re.match(r"^(nRows|nColumns|datarows|datacolumns)\s*=", line, re.I)
    ]
    filtered.extend(
        [
            "nRows={}".format(len(target_times)),
            "nColumns={}".format(len(columns)),
            "endheader",
        ]
    )
    output.parent.mkdir(parents=True, exist_ok=True)
    with output.open("w", encoding="utf-8", newline="\n") as stream:
        stream.write("\n".join(filtered) + "\n")
        stream.write("\t".join(columns) + "\n")
        for target in target_times:
            row = [target] + [
                _interpolate(source_times, column, target) for column in values
            ]
            stream.write("\t".join("{:.12g}".format(value) for value in row) + "\n")
    _, _, observed_rows = _read_storage(output)
    observed = [row[0] for row in observed_rows]
    exact = len(observed) == len(target_times) and all(
        abs(left - right) <= 1e-10 for left, right in zip(observed, target_times)
    )
    if not exact:
        raise RuntimeError("resampled RRA kinematics do not match the source grid")
    return {
        "source_row_count": len(rows),
        "output_row_count": len(target_times),
        "start_time_s": target_times[0],
        "end_time_s": target_times[-1],
        "exact_source_grid": True,
        "output_sha256": _sha256(output),
    }


def parse_mass_recommendation(log_text: str) -> dict:
    patterns = (
        re.compile(
            r"(?:recommended|recommend(?:ed)?)[^\n]{0,80}?mass(?:\s+change)?"
            r"[^+\-\d]{0,20}([+\-]?\d+(?:\.\d+)?(?:[eE][+\-]?\d+)?)\s*kg",
            re.IGNORECASE,
        ),
        re.compile(
            r"(?:total\s+mass\s+change|mass\s+change)\s*[:=]\s*"
            r"([+\-]?\d+(?:\.\d+)?(?:[eE][+\-]?\d+)?)(?:\s*kg)?",
            re.IGNORECASE,
        ),
    )
    values = []
    for line in log_text.splitlines():
        for pattern in patterns:
            match = pattern.search(line)
            if match is not None:
                values.append(float(match.group(1)))
                break
    return {
        "found": bool(values),
        "recommended_mass_change_kg": values[-1] if values else None,
        "recommendation_sequence_kg": values,
        "auto_applied": False,
        "policy": "record-only-unless-independently-consistent",
    }


def _run_ik(
    opensim,
    model_path: Path,
    marker_path: Path,
    setup_path: Path,
    start_time: float,
    end_time: float,
    directory: Path,
    model_key: str,
):
    tool = opensim.InverseKinematicsTool(str(setup_path))
    tool.setName("g6b3_{}_ik".format(model_key))
    tool.set_model_file(str(model_path))
    tool.set_results_directory(str(directory))
    tool.set_marker_file(str(marker_path))
    tool.setStartTime(start_time)
    tool.setEndTime(end_time)
    motion = directory / "ik_motion.mot"
    tool.set_output_motion_file(str(motion))
    tool.set_report_errors(True)
    setup = directory / "ik_setup.xml"
    tool.printToXML(str(setup))
    if not tool.run():
        raise RuntimeError("IK returned failure")
    if not motion.is_file():
        raise RuntimeError("IK did not create the motion output")
    marker_errors = _only_match(directory, "*marker_errors.sto", "IK marker errors")
    retained_errors = directory / "ik_marker_errors.sto"
    if marker_errors != retained_errors:
        shutil.copy2(marker_errors, retained_errors)
    return motion, retained_errors


def _run_rra_pass(
    opensim,
    model_path: Path,
    ik_path: Path,
    loads_path: Path,
    actuator_path: Path,
    task_path: Path,
    config: dict,
    start_time: float,
    end_time: float,
    directory: Path,
    pass_number: int,
):
    pass_dir = directory / "rra_pass{}".format(pass_number)
    pass_dir.mkdir(parents=True, exist_ok=True)
    adjusted_model = directory / "rra_adjusted_pass{}.osim".format(pass_number)
    force_files = opensim.ArrayStr()
    force_files.append(str(actuator_path))
    tool = opensim.RRATool()
    tool.setName("g6b3_rra_pass{}".format(pass_number))
    tool.setModelFilename(str(model_path))
    tool.setReplaceForceSet(True)
    tool.setForceSetFiles(force_files)
    tool.setResultsDir(str(pass_dir))
    tool.setOutputPrecision(12)
    tool.setInitialTime(start_time)
    tool.setFinalTime(end_time)
    tool.setSolveForEquilibrium(False)
    tool.setMaximumNumberOfSteps(20000)
    tool.setMaxDT(0.001)
    tool.setMinDT(1e-8)
    tool.setErrorTolerance(1e-5)
    tool.setExternalLoadsFileName(str(loads_path))
    tool.setDesiredKinematicsFileName(str(ik_path))
    tool.setTaskSetFileName(str(task_path))
    tool.setLowpassCutoffFrequency(float(config["lowpass_cutoff_hz"]))
    tool.setAdjustCOMToReduceResiduals(bool(config["adjust_com_to_reduce_residuals"]))
    tool.setAdjustedCOMBody(str(config["adjusted_com_body"]))
    tool.setOutputModelFileName(str(adjusted_model))
    setup = directory / "rra_setup_pass{}.xml".format(pass_number)
    tool.printToXML(str(setup))
    # Reconstruct from the serialized setup before execution.  With the
    # OpenSim 4.5 Python bindings, setModelFilename() updates the XML property
    # but does not attach a Model instance to this in-memory RRATool.  Loading
    # the setup follows the documented command-line execution path and makes
    # the model_file property effective.
    executable_tool = opensim.RRATool(str(setup))
    if not executable_tool.run():
        raise RuntimeError("RRA pass {} returned failure".format(pass_number))
    if not adjusted_model.is_file():
        raise RuntimeError("RRA pass {} did not create adjusted model".format(pass_number))
    kinematics = _only_match(pass_dir, "*Kinematics_q.sto", "RRA kinematics")
    actuation = _only_match(pass_dir, "*Actuation_force.sto", "RRA actuation")
    return adjusted_model, kinematics, actuation


def _run_id(
    opensim,
    model_path: Path,
    kinematics_path: Path,
    loads_path: Path,
    setup_path: Path,
    cutoff: float,
    start_time: float,
    end_time: float,
    directory: Path,
):
    tool = opensim.InverseDynamicsTool(str(setup_path))
    tool.setName("g6b3_inverse_dynamics")
    tool.setModelFileName(str(model_path))
    tool.setCoordinatesFileName(str(kinematics_path))
    tool.setExternalLoadsFileName(str(loads_path))
    tool.setStartTime(start_time)
    tool.setEndTime(end_time)
    tool.setLowpassCutoffFrequency(float(cutoff))
    tool.setResultsDir(str(directory))
    output = directory / "inverse_dynamics.sto"
    tool.setOutputGenForceFileName(output.name)
    setup = directory / "id_setup.xml"
    tool.printToXML(str(setup))
    tool.run()
    if not output.is_file():
        raise RuntimeError("ID did not create generalized forces")
    return output


def _com_height(opensim, model_path: Path, kinematics_path: Path) -> float:
    model = opensim.Model(str(model_path))
    state = model.initSystem()
    storage = opensim.Storage(str(kinematics_path))
    if storage.getSize() < 1:
        raise ValueError("RRA kinematics are empty")
    labels = storage.getColumnLabels()
    vector = storage.getStateVector(storage.getSize() // 2)
    values = vector.getData()
    coordinates = model.getCoordinateSet()
    in_degrees = bool(storage.isInDegrees())
    for index in range(1, labels.getSize()):
        name = str(labels.get(index))
        coordinate_index = coordinates.getIndex(name)
        if coordinate_index < 0:
            continue
        coordinate = coordinates.get(coordinate_index)
        value = float(values.get(index - 1))
        if in_degrees and int(coordinate.getMotionType()) == 1:
            value = math.radians(value)
        coordinate.setValue(state, value, False)
    model.realizePosition(state)
    height = float(model.calcMassCenterPosition(state).get(1))
    if not math.isfinite(height) or height <= 0:
        raise ValueError("calculated COM height is not finite and positive")
    return height


def _run_model(
    opensim,
    plan: dict,
    model_key: str,
    directory: Path,
    normalized_marker_path: Path,
) -> dict:
    directory.mkdir(parents=True, exist_ok=True)
    stage = "initialization"
    try:
        paths = plan["input_paths"]
        path_role = plan["models"][model_key]["path_role"]
        model_path = Path(paths[path_role]).resolve()
        marker_path = normalized_marker_path.resolve()
        grf_path = Path(paths["heldout_ground_reaction"]).resolve()
        ik_setup = Path(paths["public_ik_setup"]).resolve()
        id_setup = Path(paths["public_id_setup"]).resolve()
        start_time = float(plan["start_time_s"])
        end_time = float(plan["end_time_s"])
        rra_config = plan["opensim"]["rra"]

        stage = "external-loads"
        loads = directory / "external_loads.xml"
        _generate_external_loads(opensim, grf_path, loads)
        stage = "inverse-kinematics"
        ik_motion, ik_errors = _run_ik(
            opensim,
            model_path,
            marker_path,
            ik_setup,
            start_time,
            end_time,
            directory,
            model_key,
        )
        stage = "rra-input-generation"
        actuators, tasks, residual_point = _generate_rra_files(
            opensim, model_path, rra_config, directory
        )
        current_model = model_path
        final_kinematics = None
        final_actuation = None
        for pass_number in (1, 2):
            stage = "rra-pass-{}".format(pass_number)
            current_model, final_kinematics, final_actuation = _run_rra_pass(
                opensim,
                current_model,
                ik_motion,
                loads,
                actuators,
                tasks,
                rra_config,
                start_time,
                end_time,
                directory,
                pass_number,
            )
        retained_adaptive_kinematics = directory / "rra_kinematics_adaptive.sto"
        retained_kinematics = directory / "rra_kinematics.sto"
        retained_actuation = directory / "rra_actuation.sto"
        retained_model = directory / "rra_adjusted_model.osim"
        shutil.copy2(final_kinematics, retained_adaptive_kinematics)
        source_grid = _trc_times(
            normalized_marker_path, start_time, end_time
        )
        grid_report = resample_storage_to_source_grid(
            retained_adaptive_kinematics, source_grid, retained_kinematics
        )
        shutil.copy2(final_actuation, retained_actuation)
        shutil.copy2(current_model, retained_model)
        stage = "inverse-dynamics"
        inverse_dynamics = _run_id(
            opensim,
            retained_model,
            retained_kinematics,
            loads,
            id_setup,
            float(plan["opensim"]["id"]["lowpass_cutoff_hz"]),
            start_time,
            end_time,
            directory,
        )
        stage = "com-height"
        com_height = _com_height(opensim, retained_model, retained_kinematics)
        _, _, id_rows = _read_storage(inverse_dynamics)
        id_times = [row[0] for row in id_rows]
        id_grid_exact = len(id_times) == len(source_grid) and all(
            abs(left - right) <= 1e-10
            for left, right in zip(id_times, source_grid)
        )
        if not id_grid_exact:
            raise ValueError("inverse dynamics did not preserve the source marker grid")
        artifacts = {
            "ik_motion": _artifact(ik_motion),
            "ik_marker_errors": _artifact(ik_errors),
            "rra_kinematics": _artifact(retained_kinematics),
            "rra_kinematics_adaptive": _artifact(retained_adaptive_kinematics),
            "rra_actuation": _artifact(retained_actuation),
            "rra_adjusted_model": _artifact(retained_model),
            "inverse_dynamics": _artifact(inverse_dynamics),
        }
        return {
            "success": True,
            "rra_pass_count": 2,
            "com_height_m": com_height,
            "residual_force_application_point": {
                "body": "pelvis",
                "frame": "pelvis",
                "point_m": residual_point,
                "source": "model-pelvis-mass-center",
            },
            "source_grid_resampling": grid_report,
            "id_source_grid_exact": id_grid_exact,
            "mass_recommendation": {
                "found": False,
                "recommended_mass_change_kg": None,
                "auto_applied": False,
                "policy": "record-only-unless-independently-consistent",
                "note": "OpenSim file-sink log was not available to this legacy worker",
            },
            "artifacts": artifacts,
        }
    except Exception as exc:  # Preserve negative OpenSim outcomes as evidence.
        return {
            "success": False,
            "failed_stage": stage,
            "error_type": type(exc).__name__,
            "error_message": _portable_error(str(exc), plan, directory.parent),
        }


def main(argv=None) -> int:
    args = _parser().parse_args(argv)
    plan_path = Path(args.plan).resolve()
    work_dir = Path(args.work_dir).resolve()
    report_path = Path(args.report).resolve()
    plan = json.loads(plan_path.read_text(encoding="utf-8"))
    if plan.get("scope") != "g6b-heldout-mechanics-plan":
        raise ValueError("worker received an invalid G6b-3 plan")
    for role, expected in plan.get("input_sha256", {}).items():
        path = Path(str(plan["input_paths"][role])).resolve()
        if not path.is_file() or _sha256(path) != str(expected):
            raise ValueError("G6b-3 worker input checksum mismatch: {}".format(role))

    import opensim

    version = str(opensim.GetVersion())
    if not version.startswith(str(plan["required_opensim_version"])):
        raise RuntimeError(
            "G6b-3 requires OpenSim {}, observed {}".format(
                plan["required_opensim_version"], version
            )
        )
    work_dir.mkdir(parents=True, exist_ok=True)
    normalized_markers = work_dir / "Sub_02_4ms_opensim_compatible.trc"
    marker_normalization = normalize_trc_time_header(
        Path(str(plan["input_paths"]["heldout_markers"])).resolve(),
        normalized_markers,
    )
    models = {
        key: _run_model(opensim, plan, key, work_dir / key, normalized_markers)
        for key in ("dynamic_derived", "static_derived")
    }
    report = {
        "schema_version": "1.0.0",
        "scope": "g6b-heldout-mechanics-worker",
        "opensim_version": version,
        "input_sha256": plan["input_sha256"],
        "marker_normalization": marker_normalization,
        "models": models,
    }
    report_path.parent.mkdir(parents=True, exist_ok=True)
    report_path.write_text(
        json.dumps(report, indent=2, sort_keys=True) + "\n", encoding="utf-8"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
