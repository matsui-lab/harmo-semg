"""G6c-S5 external-yaw and frontal-plane RRA sensitivity experiment."""

from __future__ import annotations

import json
import math
import shutil
import xml.etree.ElementTree as ET
from pathlib import Path
from typing import Any, Dict, Mapping, Optional, Sequence

import numpy as np
import pandas as pd
import yaml

from harmo.dynamic_calibration import sha256_file, validate_public_source_bundle
from harmo.ingest.readers.sto import read_opensim_sto_trc
from harmo.mechanics_audit import reconstruct_cop_and_free_moment
from harmo.whole_body_balance import zero_phase_lowpass


RRA_SENSITIVITY_SCHEMA_VERSION = "1.0.0"


def _load_yaml(path: Path) -> Dict[str, Any]:
    value = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    if not isinstance(value, dict):
        raise ValueError("G6c-S5 YAML root must be a mapping")
    return value


def _numeric_mot(path: Path) -> pd.DataFrame:
    _, table = read_opensim_sto_trc(str(path))
    numeric = table.apply(pd.to_numeric, errors="coerce")
    if "time" not in numeric:
        raise ValueError("G6c-S5 MOT lacks time")
    time = numeric["time"].to_numpy(float)
    if (
        time.size < 8
        or np.any(np.diff(time) <= 0)
        or not np.all(np.isfinite(numeric.to_numpy(float)))
    ):
        raise ValueError("G6c-S5 MOT must be finite and ordered")
    return numeric


def read_opensim_column_spellings(path: Path) -> Dict[str, str]:
    """Return normalized-to-source column spellings from a MOT/STO header."""
    lines = path.read_text(encoding="utf-8").splitlines()
    for index, line in enumerate(lines):
        if line.strip().lower() != "endheader":
            continue
        for header in lines[index + 1 :]:
            columns = header.split()
            if columns:
                mapping = {column.lower(): column for column in columns}
                if len(mapping) != len(columns):
                    raise ValueError("OpenSim source columns are case-ambiguous")
                return mapping
    raise ValueError("OpenSim source lacks a column header after endheader")


def write_opensim_mot(
    frame: pd.DataFrame,
    output: Path,
    *,
    name: str,
    column_spellings: Optional[Mapping[str, str]] = None,
) -> None:
    """Write a deterministic SI-unit OpenSim external-load storage."""
    if "time" not in frame or frame.empty:
        raise ValueError("OpenSim MOT output requires nonempty time data")
    numeric = frame.apply(pd.to_numeric, errors="coerce")
    if not np.all(np.isfinite(numeric.to_numpy(float))):
        raise ValueError("OpenSim MOT output must be finite")
    spellings = dict(column_spellings or {})
    if spellings:
        missing = [column for column in numeric if str(column) not in spellings]
        if missing:
            raise ValueError(
                "OpenSim source spelling is missing columns: {}".format(missing)
            )
        numeric = numeric.rename(columns=spellings)
        if len(set(numeric.columns)) != len(numeric.columns):
            raise ValueError("OpenSim restored column spellings are not unique")
    output = output.resolve()
    output.parent.mkdir(parents=True, exist_ok=True)
    lines = [
        "name {}".format(str(name)),
        "version=1",
        "nRows={}".format(len(numeric)),
        "nColumns={}".format(len(numeric.columns)),
        "inDegrees=no",
        "endheader",
        "\t".join(str(value) for value in numeric.columns),
    ]
    with output.open("w", encoding="utf-8", newline="\n") as stream:
        stream.write("\n".join(lines) + "\n")
        numeric.to_csv(
            stream,
            sep="\t",
            header=False,
            index=False,
            float_format="%.12g",
            lineterminator="\n",
        )


def _columns(prefix: str, stem: str) -> list[str]:
    return ["{}_{}{}".format(str(prefix).lower(), stem, axis) for axis in "xyz"]


def filter_public_side_wrenches(
    frame: pd.DataFrame,
    *,
    prefixes: Sequence[str],
    vertical_axis: int,
    cutoff_hz: float,
    order: int,
    contact_threshold_n: float,
) -> tuple[pd.DataFrame, Dict[str, Any]]:
    """Filter each foot's origin wrench then reconstruct COP/free moment."""
    output = frame.copy()
    time = output["time"].to_numpy(float)
    side_reports = {}
    maximum_roundtrip_error = 0.0
    for raw_prefix in prefixes:
        prefix = str(raw_prefix).lower()
        force_columns = _columns(prefix, "ground_force_v")
        point_columns = _columns(prefix, "ground_force_p")
        torque_columns = _columns(prefix, "ground_torque_")
        force = output[force_columns].to_numpy(float)
        point = output[point_columns].to_numpy(float)
        free = output[torque_columns].to_numpy(float)
        moment_origin = np.cross(point, force) + free
        filtered_force = zero_phase_lowpass(
            time, force, cutoff_hz=float(cutoff_hz), order=int(order)
        )
        filtered_origin = zero_phase_lowpass(
            time, moment_origin, cutoff_hz=float(cutoff_hz), order=int(order)
        )
        original_contact = np.abs(force[:, int(vertical_axis)]) >= float(
            contact_threshold_n
        )
        surface = (
            float(np.median(point[original_contact, int(vertical_axis)]))
            if np.any(original_contact)
            else 0.0
        )
        cop, filtered_free, contact = reconstruct_cop_and_free_moment(
            filtered_force,
            filtered_origin,
            vertical_axis=int(vertical_axis),
            surface_coordinate_m=surface,
            force_threshold_n=float(contact_threshold_n),
        )
        retained_force = filtered_force.copy()
        retained_force[~contact] = 0.0
        cop[~contact] = 0.0
        filtered_free[~contact] = 0.0
        retained_origin = np.cross(cop, retained_force) + filtered_free
        error = (
            float(
                np.max(
                    np.abs(retained_origin[contact] - filtered_origin[contact])
                )
            )
            if np.any(contact)
            else 0.0
        )
        maximum_roundtrip_error = max(maximum_roundtrip_error, error)
        output.loc[:, force_columns] = retained_force
        output.loc[:, point_columns] = cop
        output.loc[:, torque_columns] = filtered_free
        side_reports[prefix] = {
            "contact_sample_count": int(np.sum(contact)),
            "surface_coordinate_m": surface,
            "origin_wrench_roundtrip_maximum_error_nm": error,
        }
    return output, {
        "operation": "side-specific-origin-wrench-lowpass-and-cop-reconstruction",
        "cutoff_hz": float(cutoff_hz),
        "order": int(order),
        "contact_threshold_n": float(contact_threshold_n),
        "selection": "inherited-rra-cutoff-not-residual-fitted",
        "side_reports": side_reports,
        "maximum_origin_wrench_roundtrip_error_nm": maximum_roundtrip_error,
    }


def distribute_total_vertical_free_moment(
    frame: pd.DataFrame,
    total_vertical_free_moment_nm: Sequence[float],
    *,
    prefixes: Sequence[str],
    vertical_axis: int,
    contact_threshold_n: float,
) -> tuple[pd.DataFrame, Dict[str, Any]]:
    """Allocate a combined free moment by absolute vertical-force share."""
    output = frame.copy()
    target = np.asarray(total_vertical_free_moment_nm, dtype=float)
    if target.shape != (len(output),) or not np.all(np.isfinite(target)):
        raise ValueError("combined free moment must share the MOT time grid")
    normalized_prefixes = [str(value).lower() for value in prefixes]
    vertical_forces = np.column_stack(
        [
            np.abs(output["{}_ground_force_v{}".format(prefix, "xyz"[vertical_axis])])
            for prefix in normalized_prefixes
        ]
    )
    total_force = np.sum(vertical_forces, axis=1)
    contact = total_force >= float(contact_threshold_n)
    weights = np.zeros_like(vertical_forces)
    weights[contact] = vertical_forces[contact] / total_force[contact, None]
    for side, prefix in enumerate(normalized_prefixes):
        torque_columns = _columns(prefix, "ground_torque_")
        output.loc[:, torque_columns] = 0.0
        output.loc[contact, torque_columns[vertical_axis]] = (
            target[contact] * weights[contact, side]
        )
    observed = np.sum(
        [
            output["{}_ground_torque_{}".format(prefix, "xyz"[vertical_axis])]
            .to_numpy(float)
            for prefix in normalized_prefixes
        ],
        axis=0,
    )
    expected = np.where(contact, target, 0.0)
    return output, {
        "operation": "combined-free-moment-absolute-vertical-force-share",
        "contact_sample_count": int(np.sum(contact)),
        "maximum_total_reconstruction_error_nm": float(
            np.max(np.abs(observed - expected))
        ),
        "outside_contact_maximum_nm": (
            float(np.max(np.abs(observed[~contact])))
            if np.any(~contact)
            else 0.0
        ),
        "side_resolution": "constructed-not-observed",
    }


def zero_free_moment(
    frame: pd.DataFrame, *, prefixes: Sequence[str]
) -> tuple[pd.DataFrame, Dict[str, Any]]:
    """Create the explicitly nonphysical no-free-moment negative control."""
    output = frame.copy()
    torque_columns = [
        column
        for prefix in prefixes
        for column in _columns(str(prefix).lower(), "ground_torque_")
    ]
    output.loc[:, torque_columns] = 0.0
    return output, {
        "operation": "zero-all-published-free-moment-components",
        "maximum_absolute_torque_nm": float(
            np.max(np.abs(output[torque_columns].to_numpy(float)))
        ),
        "role": "negative-control-only-physically-invalid",
    }


def modify_task_weights(
    source: Path,
    output: Path,
    *,
    coordinates: Sequence[str],
    multiplier: float,
) -> Dict[str, Any]:
    """Apply one declared multiplier to named CMC joint task weights."""
    factor = float(multiplier)
    if not math.isfinite(factor) or factor <= 0:
        raise ValueError("task-weight multiplier must be finite and positive")
    requested = {str(value) for value in coordinates}
    tree = ET.parse(str(source))
    changed = {}
    for task in tree.findall(".//CMC_Joint"):
        coordinate = task.findtext("coordinate") or task.attrib.get("name", "")
        coordinate = str(coordinate).strip()
        if coordinate not in requested:
            continue
        weight = task.find("weight")
        if weight is None or not (weight.text or "").strip():
            raise ValueError("CMC task lacks weight: {}".format(coordinate))
        values = [float(value) for value in str(weight.text).split()]
        if not values:
            raise ValueError("CMC task has an empty weight: {}".format(coordinate))
        previous = values[0]
        values[0] = previous * factor
        weight.text = " " + " ".join("{:.12g}".format(value) for value in values)
        changed[coordinate] = {"before": previous, "after": values[0]}
    if set(changed) != requested:
        raise ValueError("not every requested CMC task weight was changed")
    output = output.resolve()
    output.parent.mkdir(parents=True, exist_ok=True)
    tree.write(str(output), encoding="UTF-8", xml_declaration=True)
    return {
        "operation": "multiply-declared-cmc-task-weights",
        "multiplier": factor,
        "coordinates": changed,
        "source_sha256": sha256_file(source),
        "output_sha256": sha256_file(output),
    }


def copy_task_weights(source: Path, output: Path) -> Dict[str, Any]:
    output = output.resolve()
    output.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(source, output)
    return {
        "operation": "byte-identical-baseline-task-copy",
        "source_sha256": sha256_file(source),
        "output_sha256": sha256_file(output),
        "byte_identical": sha256_file(source) == sha256_file(output),
    }


def summarize_actuator_window(
    force: Sequence[Sequence[float]],
    moment: Sequence[Sequence[float]],
    *,
    body_weight_n: float,
    moment_reference_nm: float,
) -> Dict[str, Any]:
    force_array = np.asarray(force, dtype=float)
    moment_array = np.asarray(moment, dtype=float)
    if (
        force_array.shape != moment_array.shape
        or force_array.ndim != 2
        or force_array.shape[0] == 0
        or force_array.shape[1] != 3
        or not np.all(np.isfinite(force_array))
        or not np.all(np.isfinite(moment_array))
    ):
        raise ValueError("RRA force and moment must be finite paired (n, 3) arrays")
    body_weight = float(body_weight_n)
    moment_reference = float(moment_reference_nm)
    if body_weight <= 0 or moment_reference <= 0:
        raise ValueError("RRA normalization references must be positive")

    def metrics(values: np.ndarray, reference: float) -> Dict[str, Any]:
        axis_rms = np.sqrt(np.mean(np.square(values), axis=0))
        vector_rms = float(np.sqrt(np.sum(np.square(axis_rms))))
        return {
            "axis_mean": np.mean(values, axis=0).tolist(),
            "axis_rms": axis_rms.tolist(),
            "vector_rms": vector_rms,
            "fraction": vector_rms / reference,
        }

    return {
        "sample_count": int(force_array.shape[0]),
        "force": metrics(force_array, body_weight),
        "moment": metrics(moment_array, moment_reference),
        "body_weight_n": body_weight,
        "moment_reference_nm": moment_reference,
    }


def classify_rra_sensitivity(
    cells: Mapping[str, Mapping[str, Any]],
    *,
    baseline_cell: str,
    thresholds: Mapping[str, Any],
    baseline_reproduced: bool,
) -> Dict[str, Any]:
    """Classify all frozen cells without selecting a replacement pipeline."""
    expected_cells = int(thresholds["expected_cell_count"])
    if len(cells) != expected_cells or baseline_cell not in cells:
        raise ValueError("G6c-S5 cell set is incomplete")
    moment_limit = float(thresholds["residual_moment_fraction"])
    force_limit = float(thresholds["residual_force_fraction"])
    angle_limit = float(thresholds["rra_angle_change_rms_deg"])
    reduction_limit = float(thresholds["minimum_meaningful_relative_reduction"])
    successful = {
        cell_id: cell
        for cell_id, cell in cells.items()
        if str(cell.get("status", "completed")) == "completed"
    }
    failed = sorted(cell_id for cell_id in cells if cell_id not in successful)
    baseline_successful = baseline_cell in successful
    baseline_moment = (
        float(successful[baseline_cell]["residuals"]["moment"]["fraction"])
        if baseline_successful
        else math.nan
    )
    evaluated = {}
    for cell_id, cell in cells.items():
        if cell_id not in successful:
            evaluated[cell_id] = {
                "status": "failed",
                "force_pass": False,
                "moment_pass": False,
                "angle_pass": False,
                "admissible_for_mechanics_rescue": bool(
                    cell["admissible_for_mechanics_rescue"]
                ),
                "all_mechanics_checks_pass": False,
                "admissible_rescue": False,
                "diagnostic_only_rescue": False,
                "moment_relative_reduction_from_baseline": None,
                "meaningful_moment_reduction": False,
                "failure": dict(cell.get("failure") or {}),
            }
            continue
        force_pass = float(cell["residuals"]["force"]["fraction"]) <= force_limit
        moment_pass = float(cell["residuals"]["moment"]["fraction"]) <= moment_limit
        angle_pass = float(cell["rra_angle_change_rms_deg"]) <= angle_limit
        admissible = bool(cell["admissible_for_mechanics_rescue"])
        reduction = (
            1.0
            - (float(cell["residuals"]["moment"]["fraction"]) / baseline_moment)
            if baseline_successful and baseline_moment > 0
            else math.nan
        )
        evaluated[cell_id] = {
            "status": "completed",
            "force_pass": force_pass,
            "moment_pass": moment_pass,
            "angle_pass": angle_pass,
            "admissible_for_mechanics_rescue": admissible,
            "all_mechanics_checks_pass": bool(force_pass and moment_pass and angle_pass),
            "admissible_rescue": bool(
                admissible and force_pass and moment_pass and angle_pass
            ),
            "diagnostic_only_rescue": bool(
                not admissible and force_pass and moment_pass and angle_pass
            ),
            "moment_relative_reduction_from_baseline": reduction,
            "meaningful_moment_reduction": bool(
                math.isfinite(reduction) and reduction >= reduction_limit
            ),
        }
    valid = sorted(
        cell_id for cell_id, row in evaluated.items() if row["admissible_rescue"]
    )
    invalid = sorted(
        cell_id
        for cell_id, row in evaluated.items()
        if row["diagnostic_only_rescue"]
    )
    ranked_valid = sorted(
        (
            float(cells[cell_id]["residuals"]["moment"]["fraction"]),
            cell_id,
        )
        for cell_id, cell in successful.items()
        if bool(cell["admissible_for_mechanics_rescue"])
    )
    best_valid = ranked_valid[0][1] if ranked_valid else None
    if not baseline_reproduced:
        classification = "baseline-rra-not-reproduced"
    elif failed and valid:
        classification = "partial-nonconvergence-rescue-not-eligible"
    elif failed:
        classification = "rra-sensitivity-partially-nonconvergent"
    elif valid:
        classification = "source-faithful-sensitivity-rescue-requires-confirmation"
    elif invalid:
        classification = "diagnostic-only-unphysical-rescue"
    else:
        classification = "source-faithful-two-factor-sensitivity-insufficient"
    return {
        "classification": classification,
        "baseline_reproduced": bool(baseline_reproduced),
        "successful_cells": sorted(successful),
        "failed_cells": failed,
        "complete_factorial": not failed,
        "admissible_rescue_cells": valid,
        "diagnostic_only_rescue_cells": invalid,
        "best_admissible_cell": best_valid,
        "best_admissible_moment_fraction": (
            ranked_valid[0][0] if ranked_valid else None
        ),
        "sensitivity_rescue_candidate": bool(
            baseline_reproduced and valid and not failed
        ),
        "cell_checks": evaluated,
        "selected_pipeline": None,
        "selection_policy": "descriptive-ranking-only-no-pipeline-adoption",
        "thresholds": dict(thresholds),
    }


def build_rra_sensitivity_plan(
    protocol_path: Path,
    source_manifest_path: Path,
    data_root: Path,
    s2_result_path: Path,
    s3_result_path: Path,
    s4_result_path: Path,
    s2_work_dir: Path,
) -> Dict[str, Any]:
    """Build the checksum-bound, complete G6c-S5 factorial plan."""
    protocol_path = protocol_path.resolve()
    source_manifest_path = source_manifest_path.resolve()
    s2_result_path = s2_result_path.resolve()
    s3_result_path = s3_result_path.resolve()
    s4_result_path = s4_result_path.resolve()
    s2_work_dir = s2_work_dir.resolve()
    protocol = _load_yaml(protocol_path)
    if str(protocol.get("schema_version")) != RRA_SENSITIVITY_SCHEMA_VERSION:
        raise ValueError("unsupported G6c-S5 protocol version")
    s2 = json.loads(s2_result_path.read_text(encoding="utf-8"))
    s3 = json.loads(s3_result_path.read_text(encoding="utf-8"))
    s4 = json.loads(s4_result_path.read_text(encoding="utf-8"))
    if s2.get("scope") != "g6c-s2-mechanics-localization-audit":
        raise ValueError("G6c-S5 S2 source has the wrong scope")
    if s3.get("scope") != "g6c-s3-state-reconstruction-audit":
        raise ValueError("G6c-S5 S3 source has the wrong scope")
    if s4.get("scope") != "g6c-s4-moment-localization-audit":
        raise ValueError("G6c-S5 S4 source has the wrong scope")
    if any(value.get("diagnostic_complete") is not True for value in (s2, s3, s4)):
        raise ValueError("G6c-S5 requires complete S2-S4 diagnostics")
    if s4.get("mechanics_closed") is not False:
        raise ValueError("G6c-S5 requires the retained S4 mechanics failure")

    design = protocol.get("design") or {}
    declared = design.get("source_work_artifacts") or {}
    paths = {
        "desired_kinematics": s2_work_dir
        / str(declared.get("desired_kinematics", "")),
        "rra_actuators": s2_work_dir / str(declared.get("rra_actuators", "")),
        "rra_tasks": s2_work_dir / str(declared.get("rra_tasks", "")),
        "s2_worker_report": s2_work_dir
        / str(declared.get("s2_worker_report", "")),
        "s2_plan": s2_work_dir / str(declared.get("s2_plan", "")),
        "s2_result": s2_result_path,
        "s3_result": s3_result_path,
        "s4_result": s4_result_path,
    }
    for name, path in paths.items():
        if not path.is_file():
            raise ValueError("G6c-S5 input is missing: {}".format(name))
        paths[name] = path.resolve()
    local_s2_worker = json.loads(
        paths["s2_worker_report"].read_text(encoding="utf-8")
    )
    if local_s2_worker != (s2.get("worker") or {}):
        raise ValueError("G6c-S5 local S2 worker differs from the frozen result")
    s2_artifacts = local_s2_worker.get("artifacts") or {}
    if sha256_file(paths["rra_actuators"]) != str(
        s2_artifacts.get("actuators_sha256", "")
    ):
        raise ValueError("G6c-S5 RRA actuators differ from S2")
    if sha256_file(paths["rra_tasks"]) != str(
        s2_artifacts.get("tasks_sha256", "")
    ):
        raise ValueError("G6c-S5 RRA tasks differ from S2")
    if sha256_file(paths["s2_plan"]) != str(s2_artifacts.get("plan_sha256", "")):
        raise ValueError("G6c-S5 S2 plan hash mismatch")

    source = validate_public_source_bundle(source_manifest_path, data_root)
    if not source["pass"]:
        raise ValueError("G6c-S5 public source bundle failed checksum verification")
    roles = design.get("public_source_roles") or {}
    for name in ("model", "ground_reaction", "raw_c3d"):
        entry = source["assets_by_role"].get(str(roles.get(name, "")))
        if not entry:
            raise ValueError("G6c-S5 source role is missing: {}".format(name))
        path = Path(str(entry["absolute_path"])).resolve()
        paths[name] = path

    thresholds = dict(protocol.get("thresholds") or {})
    s3_thresholds = s3.get("thresholds") or {}
    for key in ("residual_force_fraction", "residual_moment_fraction"):
        if float(thresholds.get(key, math.nan)) != float(
            s3_thresholds.get(key, math.nan)
        ):
            raise ValueError("G6c-S5 changed inherited threshold: {}".format(key))
    external_conditions = list(design.get("external_conditions") or [])
    tracking_conditions = list(design.get("tracking_conditions") or [])
    cells = list(design.get("cells") or [])
    expected = {
        "{}__{}".format(external["id"], tracking["id"])
        for external in external_conditions
        for tracking in tracking_conditions
    }
    observed = {str(cell.get("id")) for cell in cells}
    if expected != observed or len(cells) != len(expected):
        raise ValueError("G6c-S5 cells must be the complete frozen cross-product")
    if len(cells) != int(thresholds.get("expected_cell_count", 0)):
        raise ValueError("G6c-S5 expected-cell threshold is inconsistent")

    s3_worker = s3.get("worker") or {}
    native = s3_worker.get("native_rra_quality") or {}
    angle_grid = design.get("angle_grid") or {}
    angle_start = float(angle_grid.get("start_s", math.nan))
    angle_end = float(angle_grid.get("end_s", math.nan))
    angle_rate = float(angle_grid.get("sampling_hz", math.nan))
    expected_angle_rows = int(angle_grid.get("expected_rows", 0))
    if (
        not all(math.isfinite(value) for value in (angle_start, angle_end, angle_rate))
        or angle_rate <= 0
        or angle_end < angle_start
    ):
        raise ValueError("G6c-S5 angle grid is invalid")
    angle_time_grid = (
        angle_start + np.arange(expected_angle_rows, dtype=float) / angle_rate
    )
    if angle_time_grid.size == 0 or abs(angle_time_grid[-1] - angle_end) > 1e-10:
        raise ValueError("G6c-S5 angle grid row count does not reach its end time")
    return {
        "schema_version": RRA_SENSITIVITY_SCHEMA_VERSION,
        "scope": "g6c-s5-rra-sensitivity-plan",
        "protocol_sha256": sha256_file(protocol_path),
        "source_manifest_sha256": source["manifest_sha256"],
        "prior_result_sha256": {
            "s2": sha256_file(s2_result_path),
            "s3": sha256_file(s3_result_path),
            "s4": sha256_file(s4_result_path),
        },
        "input_paths": {name: str(path) for name, path in paths.items()},
        "input_sha256": {name: sha256_file(path) for name, path in paths.items()},
        "rra_window_s": [float(value) for value in design.get("rra_window_s", [])],
        "native_target_window_s": [
            float(value) for value in design.get("native_target_window_s", [])
        ],
        "angle_time_grid_s": angle_time_grid.tolist(),
        "vertical_axis": int(design.get("vertical_axis", 1)),
        "public_prefixes": [str(value) for value in design.get("public_prefixes", [])],
        "external_source": dict(design.get("external_source") or {}),
        "external_conditions": external_conditions,
        "tracking_conditions": tracking_conditions,
        "cells": cells,
        "baseline_cell": str(design.get("baseline_cell", "")),
        "opensim": dict(protocol.get("opensim") or {}),
        "normalization": {
            "body_weight_n": float(s3_worker.get("body_weight_n")),
            "moment_reference_nm": float(s3_worker.get("moment_reference_nm")),
        },
        "expected_baseline": {
            "residual_force_rms_n": float(native.get("residual_force_rms_n")),
            "residual_moment_rms_nm": float(native.get("residual_moment_rms_nm")),
        },
        "thresholds": thresholds,
        "execution_failure_policy": dict(
            protocol.get("execution_failure_policy") or {}
        ),
        "claim_boundary": str(protocol.get("claim_boundary", "")),
    }


def evaluate_rra_sensitivity(
    plan: Mapping[str, Any], worker: Mapping[str, Any]
) -> Dict[str, Any]:
    diagnosis = worker.get("diagnosis") or {}
    checks = {
        "worker_scope": worker.get("scope") == "g6c-s5-rra-sensitivity-worker",
        "worker_inputs": worker.get("input_sha256") == plan.get("input_sha256"),
        "prior_results": worker.get("prior_result_sha256")
        == plan.get("prior_result_sha256"),
        "opensim_version": worker.get("opensim_version_pass") is True,
        "all_cells_complete": worker.get("all_cells_complete") is True,
        "baseline_reproduced": worker.get("baseline_reproduced") is True,
        "external_transform_integrity": worker.get(
            "external_transform_integrity_pass"
        )
        is True,
        "tracking_transform_integrity": worker.get(
            "tracking_transform_integrity_pass"
        )
        is True,
        "target_grids": worker.get("target_grids_exact") is True,
    }
    diagnostic_complete = all(checks.values())
    rescue = bool(
        diagnostic_complete and diagnosis.get("sensitivity_rescue_candidate") is True
    )
    return {
        "schema_version": RRA_SENSITIVITY_SCHEMA_VERSION,
        "scope": "g6c-s5-rra-sensitivity-audit",
        "pass": rescue,
        "diagnostic_complete": diagnostic_complete,
        "sensitivity_rescue_candidate": rescue,
        "checks": checks,
        "thresholds": dict(plan.get("thresholds") or {}),
        "worker": dict(worker),
        "scientific_claim_boundary": str(plan.get("claim_boundary", "")),
        "next_gate": (
            "preregister-independent-rra-confirmation"
            if rescue
            else "blocked-at-g6c-s5"
        ),
    }


def write_rra_sensitivity(result: Mapping[str, Any], output: Path) -> None:
    output = output.resolve()
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(
        json.dumps(result, indent=2, sort_keys=True) + "\n", encoding="utf-8"
    )
