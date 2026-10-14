#!/usr/bin/env python3
"""Estimate target-specific marker locations from dynamic calibration trials."""

from __future__ import annotations

import argparse
import bisect
import hashlib
import json
import math
import statistics
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
if str(ROOT / "src") not in sys.path:
    sys.path.insert(0, str(ROOT / "src"))

from harmo.dynamic_personalization import regularized_marker_update  # noqa: E402


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
    parser.add_argument("--output-model", required=True)
    parser.add_argument("--report", required=True)
    return parser


def _vec(value) -> list[float]:
    return [float(value[index]) for index in range(3)]


def _normalize_trc(source: Path, output: Path) -> dict:
    content = source.read_bytes()
    nonstandard = b"Frame#\tTimes\t"
    standard = b"Frame#\tTime\t"
    nonstandard_count = content.count(nonstandard)
    standard_count = content.count(standard)
    if nonstandard_count == 1 and standard_count == 0:
        normalized = content.replace(nonstandard, standard, 1)
        operation = "replace-Times-with-Time"
    elif nonstandard_count == 0 and standard_count == 1:
        normalized = content
        operation = "already-standard-Time"
    else:
        raise ValueError(
            "expected one unambiguous Frame#/Time(s) header, observed Times={} Time={}".format(
                nonstandard_count, standard_count
            )
        )
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_bytes(normalized)
    return {
        "file": output.name,
        "replacement_count": nonstandard_count,
        "operation": operation,
        "source_sha256": _sha256(source),
        "normalized_sha256": _sha256(output),
        "source_size_bytes": len(content),
        "normalized_size_bytes": len(normalized),
    }


def _read_trc(path: Path, expected_markers: list[str]):
    lines = path.read_text(encoding="utf-8", errors="strict").splitlines()
    header_index = next(
        (index for index, line in enumerate(lines) if line.startswith("Frame#\tTime\t")),
        None,
    )
    if header_index is None:
        raise ValueError("normalized TRC has no Frame#/Time header")
    names = [value.strip() for value in lines[header_index].split("\t")[2:] if value.strip()]
    missing = sorted(set(expected_markers) - set(names))
    if missing:
        raise ValueError("TRC lacks frozen active markers: {}".format(missing))
    times = []
    observations = {name: [] for name in expected_markers}
    for line in lines[header_index + 2 :]:
        if not line.strip():
            continue
        fields = line.split("\t")
        if len(fields) < 2 + 3 * len(names):
            continue
        try:
            time = float(fields[1])
            values = [float(value) for value in fields[2 : 2 + 3 * len(names)]]
        except ValueError:
            continue
        times.append(time)
        for name in expected_markers:
            index = names.index(name)
            observations[name].append(values[index * 3 : index * 3 + 3])
    if len(times) < 2 or any(right <= left for left, right in zip(times, times[1:])):
        raise ValueError("TRC timebase is not strictly increasing")
    return times, observations


def _only_match(directory: Path, pattern: str, label: str) -> Path:
    matches = sorted(directory.glob(pattern))
    if len(matches) != 1:
        raise RuntimeError(
            "{} expected one artifact, observed {}".format(label, len(matches))
        )
    return matches[0]


def _run_ik(opensim, model_path, marker_path, setup_path, start, end, directory):
    directory.mkdir(parents=True, exist_ok=True)
    tool = opensim.InverseKinematicsTool(str(setup_path))
    tool.setName("g6b3r_ik")
    tool.set_model_file(str(model_path))
    tool.set_results_directory(str(directory))
    tool.set_marker_file(str(marker_path))
    tool.setStartTime(float(start))
    tool.setEndTime(float(end))
    motion = directory / "ik_motion.mot"
    tool.set_output_motion_file(str(motion))
    tool.set_report_errors(True)
    tool.printToXML(str(directory / "ik_setup.xml"))
    if not tool.run() or not motion.is_file():
        raise RuntimeError("registration IK returned failure")
    errors = _only_match(directory, "*marker_errors.sto", "IK marker errors")
    return motion, errors


def _error_metrics(path: Path) -> dict:
    lines = path.read_text(encoding="utf-8").splitlines()
    end = next(index for index, line in enumerate(lines) if line.strip() == "endheader")
    columns = lines[end + 1].split()
    rms_index = next(index for index, value in enumerate(columns) if "marker_error_RMS" in value)
    max_index = next(index for index, value in enumerate(columns) if "marker_error_max" in value)
    rms_values = []
    max_values = []
    for line in lines[end + 2 :]:
        fields = line.split()
        if len(fields) != len(columns):
            continue
        rms_values.append(float(fields[rms_index]))
        max_values.append(float(fields[max_index]))
    if not rms_values:
        raise ValueError("IK marker error artifact has no rows")
    return {
        "ik_rms_error_m": math.sqrt(
            sum(value * value for value in rms_values) / len(rms_values)
        ),
        "ik_max_error_m": max(max_values),
        "sample_count": len(rms_values),
        "artifact_sha256": _sha256(path),
    }


def _set_state_from_storage(model, state, storage, row_index):
    labels = storage.getColumnLabels()
    vector = storage.getStateVector(row_index)
    values = vector.getData()
    coordinates = model.getCoordinateSet()
    in_degrees = bool(storage.isInDegrees())
    for index in range(1, labels.getSize()):
        name = str(labels.get(index)).replace("\\", "/").split("/")[-1]
        coordinate_index = coordinates.getIndex(name)
        if coordinate_index < 0:
            continue
        coordinate = coordinates.get(coordinate_index)
        value = float(values.get(index - 1))
        if in_degrees and int(coordinate.getMotionType()) == 1:
            value = math.radians(value)
        coordinate.setValue(state, value, False)
    model.realizePosition(state)
    return float(vector.getTime())


def _robust_local(values: list[list[float]], mad_threshold: float):
    if not values:
        raise ValueError("marker has no local-position samples")
    center = [statistics.median(value[axis] for value in values) for axis in range(3)]
    distances = [math.dist(value, center) for value in values]
    median_distance = statistics.median(distances)
    absolute = [abs(value - median_distance) for value in distances]
    mad = statistics.median(absolute)
    if mad == 0:
        retained = values
    else:
        retained = [
            value
            for value, distance in zip(values, distances)
            if 0.6744897501960817 * abs(distance - median_distance) / mad
            <= mad_threshold
        ]
    if not retained:
        raise ValueError("marker robust aggregation retained no samples")
    estimate = [statistics.median(value[axis] for value in retained) for axis in range(3)]
    local_mad = statistics.median(math.dist(value, estimate) for value in retained)
    return estimate, local_mad, len(retained)


def _trial_local_estimates(
    opensim,
    model_path: Path,
    motion_path: Path,
    marker_path: Path,
    markers: list[str],
    start: float,
    end: float,
    stride: int,
    mad_threshold: float,
):
    times, observations = _read_trc(marker_path, markers)
    model = opensim.Model(str(model_path))
    state = model.initSystem()
    ground = model.getGround()
    marker_set = model.getMarkerSet()
    storage = opensim.Storage(str(motion_path))
    candidates = {name: [] for name in markers}
    for row_index in range(0, storage.getSize(), stride):
        time = _set_state_from_storage(model, state, storage, row_index)
        if time < start or time > end:
            continue
        observed_index = bisect.bisect_left(times, time)
        observed_index = min(max(observed_index, 1), len(times) - 1)
        if abs(times[observed_index - 1] - time) <= abs(times[observed_index] - time):
            observed_index -= 1
        if abs(times[observed_index] - time) > 0.0051:
            raise ValueError("IK and TRC timebases are not aligned")
        for name in markers:
            observed = observations[name][observed_index]
            if not all(math.isfinite(value) for value in observed):
                continue
            marker = marker_set.get(name)
            parent = marker.getParentFrame()
            local = ground.findStationLocationInAnotherFrame(
                state, opensim.Vec3(*observed), parent
            )
            candidates[name].append(_vec(local))
    estimates = {}
    diagnostics = {}
    for name in markers:
        estimate, local_mad, retained = _robust_local(
            candidates[name], mad_threshold
        )
        estimates[name] = estimate
        diagnostics[name] = {
            "sample_count": len(candidates[name]),
            "inlier_count": retained,
            "local_mad_m": local_mad,
        }
    return estimates, diagnostics


def _update_model(
    opensim,
    source: Path,
    output: Path,
    markers: list[str],
    trial_estimates: dict,
    initial_locations: dict,
    relaxation: float,
    maximum_displacement: float,
    marker_prior: dict | None = None,
    marker_classes: dict | None = None,
    objective_config: dict | None = None,
):
    model = opensim.Model(str(source))
    model.initSystem()
    marker_set = model.updMarkerSet()
    posterior = None
    if marker_prior is not None:
        objective = objective_config or {}
        posterior = regularized_marker_update(
            {
                name: [trial_estimates[role][name] for role in trial_estimates]
                for name in markers
            },
            marker_prior,
            marker_classes or {},
            dynamic_weight=float(objective.get("dynamic_weight", 1.0)),
            anatomical_prior_weight=float(
                objective.get("anatomical_prior_weight", 10.0)
            ),
            tracking_prior_weight=float(objective.get("tracking_prior_weight", 0.25)),
            dynamic_noise_floor_m=float(objective.get("dynamic_noise_floor_m", 0.005)),
            rigid_cluster_weight=float(objective.get("rigid_cluster_weight", 1.0)),
            bilateral_symmetry_weight=float(
                objective.get("bilateral_symmetry_weight", 0.0)
            ),
        )
    rows = []
    for name in markers:
        marker = marker_set.get(name)
        old = _vec(marker.get_location())
        target = (
            [float(value) for value in posterior["markers"][name]["location_m"]]
            if posterior is not None
            else [
                statistics.median(
                    trial_estimates[role][name][axis] for role in trial_estimates
                )
                for axis in range(3)
            ]
        )
        proposed = [old[axis] + relaxation * (target[axis] - old[axis]) for axis in range(3)]
        initial = initial_locations[name]
        displacement = math.dist(proposed, initial)
        if displacement > maximum_displacement:
            scale = maximum_displacement / displacement
            proposed = [
                initial[axis] + scale * (proposed[axis] - initial[axis])
                for axis in range(3)
            ]
        marker.set_location(opensim.Vec3(*proposed))
        rows.append(
            {
                "marker": name,
                "body": marker.getParentFrame().getName(),
                "initial_location_m": initial,
                "previous_location_m": old,
                "registered_location_m": proposed,
                "iteration_update_m": math.dist(old, proposed),
                "total_displacement_m": math.dist(initial, proposed),
                "optimization_target_m": target,
                "posterior_standard_deviation_m": (
                    posterior["markers"][name]["posterior_standard_deviation_m"]
                    if posterior is not None
                    else None
                ),
            }
        )
    model.setName("02_dynamic_registered")
    model.finalizeConnections()
    output.parent.mkdir(parents=True, exist_ok=True)
    model.printToXML(str(output))
    checked = opensim.Model(str(output))
    checked.initSystem()
    return rows


def main(argv=None) -> int:
    args = _parser().parse_args(argv)
    plan_path = Path(args.plan).resolve()
    work_dir = Path(args.work_dir).resolve()
    output_model = Path(args.output_model).resolve()
    report_path = Path(args.report).resolve()
    plan = json.loads(plan_path.read_text(encoding="utf-8"))
    if plan.get("scope") != "g6b-dynamic-marker-registration-plan":
        raise ValueError("worker received an invalid G6b-3R plan")
    for role, expected in plan.get("input_sha256", {}).items():
        path = Path(str(plan["input_paths"][role])).resolve()
        if not path.is_file() or _sha256(path) != str(expected):
            raise ValueError("G6b-3R worker input checksum mismatch: {}".format(role))

    import opensim

    version = str(opensim.GetVersion())
    if not version.startswith(str(plan["required_opensim_version"])):
        raise RuntimeError("G6b-3R requires OpenSim 4.5.2")
    work_dir.mkdir(parents=True, exist_ok=True)
    roles = plan["calibration_roles"]
    markers = plan["active_markers"]
    config = plan["registration"]
    normalized_paths = {}
    normalizations = {}
    for role in roles:
        source = Path(str(plan["input_paths"][role])).resolve()
        output = work_dir / "{}_opensim_compatible.trc".format(role)
        normalizations[role] = _normalize_trc(source, output)
        normalized_paths[role] = output

    base_model = Path(str(plan["input_paths"]["base_dynamic_model"])).resolve()
    ik_setup = Path(str(plan["input_paths"]["public_ik_setup"])).resolve()
    base = opensim.Model(str(base_model))
    base.initSystem()
    initial_locations = {
        name: _vec(base.getMarkerSet().get(name).get_location()) for name in markers
    }
    current_model = base_model
    trial_report = {role: {} for role in roles}
    iteration_rows = []
    last_diagnostics = None
    for iteration in range(1, int(config["iteration_count"]) + 1):
        trial_estimates = {}
        diagnostics = {}
        for role in roles:
            directory = work_dir / "iteration_{}".format(iteration) / role
            motion, errors = _run_ik(
                opensim,
                current_model,
                normalized_paths[role],
                ik_setup,
                plan["start_time_s"],
                plan["end_time_s"],
                directory,
            )
            metrics = _error_metrics(errors)
            if iteration == 1:
                trial_report[role]["initial"] = metrics
            estimates, diagnostic = _trial_local_estimates(
                opensim,
                current_model,
                motion,
                normalized_paths[role],
                markers,
                plan["start_time_s"],
                plan["end_time_s"],
                int(config["sampled_frame_stride"]),
                float(config["within_trial_outlier_mad"]),
            )
            trial_estimates[role] = estimates
            diagnostics[role] = diagnostic
        next_model = work_dir / "iteration_{}".format(iteration) / "registered.osim"
        rows = _update_model(
            opensim,
            current_model,
            next_model,
            markers,
            trial_estimates,
            initial_locations,
            float(config["relaxation"]),
            float(config["maximum_total_marker_displacement_m"]),
            plan.get("marker_prior_prediction"),
            plan.get("marker_classes"),
            plan.get("objective"),
        )
        iteration_rows.append(
            {
                "iteration": iteration,
                "input_model_sha256": _sha256(current_model),
                "output_model_sha256": _sha256(next_model),
                "maximum_update_m": max(row["iteration_update_m"] for row in rows),
            }
        )
        current_model = next_model
        last_diagnostics = diagnostics

    output_model.parent.mkdir(parents=True, exist_ok=True)
    output_model.write_bytes(current_model.read_bytes())
    final_model = opensim.Model(str(output_model))
    final_model.initSystem()
    marker_rows = []
    for name in markers:
        location = _vec(final_model.getMarkerSet().get(name).get_location())
        marker_rows.append(
            {
                "marker": name,
                "body": final_model.getMarkerSet().get(name).getParentFrame().getName(),
                "initial_location_m": initial_locations[name],
                "registered_location_m": location,
                "total_displacement_m": math.dist(initial_locations[name], location),
                "within_trial_local_mad_m": {
                    role: last_diagnostics[role][name]["local_mad_m"] for role in roles
                },
                "sample_count": {
                    role: last_diagnostics[role][name]["sample_count"] for role in roles
                },
                "inlier_count": {
                    role: last_diagnostics[role][name]["inlier_count"] for role in roles
                },
            }
        )
    for role in roles:
        directory = work_dir / "final" / role
        _, errors = _run_ik(
            opensim,
            output_model,
            normalized_paths[role],
            ik_setup,
            plan["start_time_s"],
            plan["end_time_s"],
            directory,
        )
        trial_report[role]["final"] = _error_metrics(errors)

    report = {
        "schema_version": "1.0.0",
        "scope": "g6b-dynamic-marker-registration-worker",
        "opensim_version": version,
        "input_sha256": plan["input_sha256"],
        "hidden_inputs_available": False,
        "iteration_count": int(config["iteration_count"]),
        "output_model_sha256": _sha256(output_model),
        "normalizations": normalizations,
        "iterations": iteration_rows,
        "trials": trial_report,
        "markers": marker_rows,
        "objective": plan.get("objective") or {"mode": "dynamic-only-legacy"},
        "anatomical_prior_applied_during_optimization": (
            plan.get("marker_prior_prediction") is not None
        ),
    }
    report_path.parent.mkdir(parents=True, exist_ok=True)
    report_path.write_text(
        json.dumps(report, indent=2, sort_keys=True) + "\n", encoding="utf-8"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
