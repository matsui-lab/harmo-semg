"""Checksum-bound planning and evaluation for the G6b-3 mechanics pilot."""

from __future__ import annotations

import json
import math
import xml.etree.ElementTree as ET
from pathlib import Path
from typing import Any, Dict, List, Mapping, Sequence, Tuple

import numpy as np
import pandas as pd
import yaml

from harmo.dynamic_calibration import (
    _read_verified_trc,
    sha256_file,
    validate_public_source_bundle,
)
from harmo.g6_execution import rra_angle_change_metrics, rra_residual_metrics
from harmo.ingest.readers.sto import read_opensim_sto_trc


DYNAMIC_MECHANICS_SCHEMA_VERSION = "1.0.0"
_MODEL_KEYS = ("dynamic_derived", "static_derived")


def _load_yaml(path: Path) -> Dict[str, Any]:
    value = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    if not isinstance(value, dict):
        raise ValueError("G6b-3 YAML root must be a mapping")
    return value


def _finite(value: Any, label: str) -> float:
    numeric = float(value)
    if not math.isfinite(numeric):
        raise ValueError("{} must be finite".format(label))
    return numeric


def _verify_event_window(
    event_asset: Mapping[str, Any], sheet_name: str, start_time: float, end_time: float
) -> Dict[str, float]:
    if event_asset.get("pass") is not True:
        raise ValueError("G6b-3 event workbook is not checksum verified")
    path = Path(str(event_asset["absolute_path"]))
    frame = pd.read_excel(path, sheet_name=sheet_name, header=None)
    by_label: Dict[str, np.ndarray] = {}
    for _, row in frame.iterrows():
        label = str(row.iloc[0]).strip()
        values = pd.to_numeric(row.iloc[1:], errors="coerce").dropna().to_numpy(float)
        by_label[label] = values
    required = ("IC R", "TO R", "IC L", "TO L")
    if any(label not in by_label for label in required):
        raise ValueError("G6b-3 event workbook lacks required gait events")
    right_contacts = by_label["IC R"]
    candidates = right_contacts[right_contacts >= 20.0]
    if candidates.size < 2:
        raise ValueError("G6b-3 event workbook has no complete right stride")
    observed_start = float(candidates[0])
    observed_end = float(candidates[1])
    if not np.isclose(observed_start, start_time, atol=1e-9, rtol=0.0):
        raise ValueError("G6b-3 frozen start does not match the event workbook")
    if not np.isclose(observed_end, end_time, atol=1e-9, rtol=0.0):
        raise ValueError("G6b-3 frozen end does not match the event workbook")
    sequence = {
        "right_initial_contact_start_s": observed_start,
        "right_toe_off_s": float(by_label["TO R"][(by_label["TO R"] > start_time)][0]),
        "left_initial_contact_s": float(
            by_label["IC L"][(by_label["IC L"] > start_time)][0]
        ),
        "left_toe_off_s": float(by_label["TO L"][(by_label["TO L"] > start_time)][0]),
        "right_initial_contact_end_s": observed_end,
    }
    ordered = list(sequence.values())
    if ordered != sorted(ordered) or ordered[0] != start_time or ordered[-1] != end_time:
        raise ValueError("G6b-3 frozen event sequence is not a complete right stride")
    return sequence


def build_dynamic_mechanics_plan(
    protocol_path: Path,
    manifest_path: Path,
    data_root: Path,
    dynamic_model_path: Path,
    g6b2_result_path: Path,
) -> Dict[str, Any]:
    """Bind G6b-3 to public inputs and the accepted G6b-2 model artifact."""
    protocol_path = protocol_path.resolve()
    manifest_path = manifest_path.resolve()
    dynamic_model_path = dynamic_model_path.resolve()
    g6b2_result_path = g6b2_result_path.resolve()
    protocol = _load_yaml(protocol_path)
    if str(protocol.get("schema_version")) != "1.0.0":
        raise ValueError("unsupported G6b-3 protocol version")
    audit = validate_public_source_bundle(manifest_path, data_root)
    if not audit["pass"]:
        raise ValueError("G6b-3 public source bundle failed checksum verification")
    roles = audit["assets_by_role"]
    heldout = protocol.get("heldout_trial") or {}
    opensim = protocol.get("opensim") or {}
    required_roles = [
        str(heldout.get("marker_role", "")),
        str(heldout.get("ground_reaction_role", "")),
        str(heldout.get("event_role", "")),
        str((protocol.get("models") or {}).get("static_derived", {}).get("role", "")),
        str(opensim.get("ik_setup_role", "")),
        str(opensim.get("id_setup_role", "")),
    ]
    if any(not role for role in required_roles) or len(set(required_roles)) != len(
        required_roles
    ):
        raise ValueError("G6b-3 input roles must be nonempty and unique")
    missing = sorted(set(required_roles) - set(roles))
    if missing:
        raise ValueError("G6b-3 manifest lacks roles: {}".format(missing))

    preconditions = protocol.get("preconditions") or {}
    g6b2 = json.loads(g6b2_result_path.read_text(encoding="utf-8"))
    if g6b2.get("pass") is not True:
        raise ValueError("G6b-3 requires a passing G6b-2 result")
    expected_dynamic_hash = str(preconditions.get("dynamic_model_sha256", ""))
    if str(g6b2.get("generated_model_sha256")) != expected_dynamic_hash:
        raise ValueError("G6b-2 result does not match the frozen dynamic model")
    if not dynamic_model_path.is_file() or sha256_file(dynamic_model_path) != (
        expected_dynamic_hash
    ):
        raise ValueError("G6b-3 dynamic-derived model checksum mismatch")
    static_role = required_roles[3]
    if roles[static_role]["observed_sha256"] != g6b2.get(
        "hidden_reference_model_sha256"
    ):
        raise ValueError("G6b-3 static reference differs from the G6b-2 reference")
    target_subject = str(preconditions.get("target_subject_id", ""))
    if target_subject != str(g6b2.get("target_subject_id", "")):
        raise ValueError("G6b-3 target subject differs from G6b-2")

    start_time = _finite(heldout.get("start_time_s"), "G6b-3 start time")
    end_time = _finite(heldout.get("end_time_s"), "G6b-3 end time")
    if end_time <= start_time:
        raise ValueError("G6b-3 end time must follow start time")
    events = _verify_event_window(
        roles[required_roles[2]],
        str(heldout.get("event_sheet", "")),
        start_time,
        end_time,
    )
    ik_setup_path = Path(str(roles[required_roles[4]]["absolute_path"]))
    ik_root = ET.parse(str(ik_setup_path)).getroot()
    active_marker_count = sum(
        1
        for task in ik_root.iter("IKMarkerTask")
        if str(task.findtext("apply", "false")).strip().lower() == "true"
    )
    if active_marker_count != int(opensim.get("active_marker_task_count", -1)):
        raise ValueError("G6b-3 public IK marker-task count differs from protocol")
    marker_data = _read_verified_trc(roles[required_roles[0]])
    if float(marker_data["time"].min()) > start_time or float(
        marker_data["time"].max()
    ) < end_time:
        raise ValueError("G6b-3 held-out marker trial does not cover the frozen stride")

    input_paths = {
        role: str(Path(str(roles[role]["absolute_path"]))) for role in required_roles
    }
    input_hashes = {role: roles[role]["observed_sha256"] for role in required_roles}
    input_paths["dynamic_derived_model"] = str(dynamic_model_path)
    input_hashes["dynamic_derived_model"] = expected_dynamic_hash
    return {
        "schema_version": DYNAMIC_MECHANICS_SCHEMA_VERSION,
        "scope": "g6b-heldout-mechanics-plan",
        "protocol_sha256": sha256_file(protocol_path),
        "source_manifest_sha256": audit["manifest_sha256"],
        "g6b2_result_sha256": sha256_file(g6b2_result_path),
        "target_subject_id": target_subject,
        "required_opensim_version": str(opensim.get("required_version", "")),
        "start_time_s": start_time,
        "end_time_s": end_time,
        "event_sequence": events,
        "input_paths": input_paths,
        "input_sha256": input_hashes,
        "models": {
            "dynamic_derived": {
                "path_role": "dynamic_derived_model",
                "static_used_in_synthesis": False,
            },
            "static_derived": {
                "path_role": static_role,
                "comparison_reference_only": True,
            },
        },
        "opensim": opensim,
        "primary_coordinates": [
            str(value) for value in protocol.get("primary_coordinates", [])
        ],
    }


def _storage_in_degrees(header: pd.DataFrame) -> bool:
    if header.empty:
        return False
    lookup = {str(key).lower(): str(value).lower() for key, value in header.iloc[0].items()}
    return lookup.get("indegrees") in {"yes", "true", "1"}


def _resolve_column(data: pd.DataFrame, expected: str) -> str:
    matches = [
        str(column)
        for column in data.columns
        if str(column).strip().lower() == expected.lower()
        or str(column).strip().replace("\\", "/").split("/")[-1].lower()
        == expected.lower()
    ]
    if len(matches) != 1:
        raise ValueError("expected exactly one {} column".format(expected))
    return matches[0]


def _marker_error_metrics(path: Path) -> Dict[str, Any]:
    _, data = read_opensim_sto_trc(str(path))
    by_lower = {str(column).lower(): str(column) for column in data.columns}
    rms_column = next(
        (column for key, column in by_lower.items() if "marker_error_rms" in key),
        None,
    )
    max_column = next(
        (column for key, column in by_lower.items() if "marker_error_max" in key),
        None,
    )
    if rms_column is None or max_column is None:
        raise ValueError("IK marker-error output lacks RMS or max columns")
    rms = pd.to_numeric(data[rms_column], errors="coerce").to_numpy(float)
    maximum = pd.to_numeric(data[max_column], errors="coerce").to_numpy(float)
    if not np.all(np.isfinite(rms)) or not np.all(np.isfinite(maximum)):
        raise ValueError("IK marker-error output contains nonfinite values")
    return {
        "ik_rms_error_m": float(np.sqrt(np.mean(np.square(rms)))),
        "ik_max_error_m": float(np.max(maximum)),
        "ik_marker_error_sample_count": int(rms.size),
        "ik_marker_errors_sha256": sha256_file(path),
    }


def _external_force_rms(path: Path, start_time: float, end_time: float) -> float:
    _, data = read_opensim_sto_trc(str(path))
    time = pd.to_numeric(data[_resolve_column(data, "time")], errors="coerce").to_numpy(
        float
    )
    keep = np.isfinite(time) & (time >= start_time) & (time <= end_time)
    if int(keep.sum()) < 2:
        raise ValueError("G6b-3 GRF has insufficient samples in the frozen stride")
    vectors = []
    for side in ("L", "R"):
        columns = [
            _resolve_column(data, "{}_ground_force_v{}".format(side, axis))
            for axis in "xyz"
        ]
        vectors.append(data[columns].apply(pd.to_numeric, errors="coerce").to_numpy(float))
    total = vectors[0][keep] + vectors[1][keep]
    if not np.all(np.isfinite(total)):
        raise ValueError("G6b-3 GRF contains nonfinite values")
    return float(np.sqrt(np.mean(np.sum(np.square(total), axis=1))))


def _aligned_waveform(
    reference_path: Path,
    candidate_path: Path,
    reference_column: str,
    candidate_column: str,
    convert_angles: bool,
) -> Tuple[np.ndarray, np.ndarray]:
    reference_header, reference = read_opensim_sto_trc(str(reference_path))
    candidate_header, candidate = read_opensim_sto_trc(str(candidate_path))
    reference_time = pd.to_numeric(reference["time"], errors="coerce").to_numpy(float)
    candidate_time = pd.to_numeric(candidate["time"], errors="coerce").to_numpy(float)
    reference_values = pd.to_numeric(
        reference[_resolve_column(reference, reference_column)], errors="coerce"
    ).to_numpy(float)
    candidate_values = pd.to_numeric(
        candidate[_resolve_column(candidate, candidate_column)], errors="coerce"
    ).to_numpy(float)
    valid_reference = np.isfinite(reference_time) & np.isfinite(reference_values)
    lower = max(float(reference_time[valid_reference].min()), float(candidate_time.min()))
    upper = min(float(reference_time[valid_reference].max()), float(candidate_time.max()))
    keep = (
        np.isfinite(candidate_time)
        & np.isfinite(candidate_values)
        & (candidate_time >= lower)
        & (candidate_time <= upper)
    )
    if int(valid_reference.sum()) < 2 or int(keep.sum()) < 2:
        raise ValueError("G6b-3 waveforms have insufficient temporal overlap")
    aligned_reference = np.interp(
        candidate_time[keep],
        reference_time[valid_reference],
        reference_values[valid_reference],
    )
    aligned_candidate = candidate_values[keep]
    if convert_angles:
        if not _storage_in_degrees(reference_header):
            aligned_reference = np.degrees(aligned_reference)
        if not _storage_in_degrees(candidate_header):
            aligned_candidate = np.degrees(aligned_candidate)
    return aligned_reference, aligned_candidate


def waveform_comparison(
    reference: Sequence[float], candidate: Sequence[float]
) -> Dict[str, float]:
    reference_values = np.asarray(reference, dtype=float)
    candidate_values = np.asarray(candidate, dtype=float)
    if reference_values.shape != candidate_values.shape or reference_values.size < 2:
        raise ValueError("G6b-3 waveform arrays must have equal nontrivial shape")
    if not np.all(np.isfinite(reference_values)) or not np.all(
        np.isfinite(candidate_values)
    ):
        raise ValueError("G6b-3 waveform arrays must be finite")
    difference = candidate_values - reference_values
    rmse = float(np.sqrt(np.mean(np.square(difference))))
    reference_rms = float(np.sqrt(np.mean(np.square(reference_values))))
    if reference_rms <= 0:
        raise ValueError("G6b-3 reference waveform RMS must be positive")
    if float(np.std(reference_values)) <= 0 or float(np.std(candidate_values)) <= 0:
        correlation = 1.0 if np.allclose(reference_values, candidate_values) else 0.0
    else:
        correlation = float(np.corrcoef(reference_values, candidate_values)[0, 1])
    return {
        "rmse": rmse,
        "reference_rms": reference_rms,
        "nrmse": rmse / reference_rms,
        "correlation": correlation,
        "sample_count": int(reference_values.size),
    }


def _verified_artifact_paths(
    worker_model: Mapping[str, Any], expected_dir: Path
) -> Dict[str, Path]:
    artifacts = worker_model.get("artifacts")
    if not isinstance(artifacts, Mapping):
        raise ValueError("G6b-3 worker model lacks artifacts")
    resolved: Dict[str, Path] = {}
    for role, raw in artifacts.items():
        if not isinstance(raw, Mapping):
            raise ValueError("G6b-3 worker artifact must be a mapping")
        name = str(raw.get("file", ""))
        if not name or Path(name).name != name:
            raise ValueError("G6b-3 worker artifact name is unsafe")
        path = expected_dir / name
        if not path.is_file() or sha256_file(path) != str(raw.get("sha256", "")):
            raise ValueError("G6b-3 worker artifact checksum mismatch: {}".format(role))
        resolved[str(role)] = path
    return resolved


def evaluate_dynamic_mechanics(
    protocol_path: Path,
    plan: Mapping[str, Any],
    worker_report: Mapping[str, Any],
    work_dir: Path,
) -> Dict[str, Any]:
    """Evaluate absolute OpenSim quality and dynamic-vs-static agreement."""
    protocol = _load_yaml(protocol_path.resolve())
    thresholds = protocol.get("thresholds") or {}
    runtime_ok = str(worker_report.get("opensim_version", "")).startswith(
        str(plan["required_opensim_version"])
    )
    input_hashes_ok = worker_report.get("input_sha256") == plan.get("input_sha256")
    normalization = worker_report.get("marker_normalization") or {}
    normalized_name = str(normalization.get("file", ""))
    normalized_path = work_dir.resolve() / normalized_name
    marker_normalization_ok = (
        Path(normalized_name).name == normalized_name
        and normalization.get("replacement_count") == 1
        and normalization.get("source_sha256")
        == plan["input_sha256"]["heldout_markers"]
        and normalization.get("source_size_bytes")
        == normalization.get("normalized_size_bytes", 0) + 1
        and normalized_path.is_file()
        and sha256_file(normalized_path) == normalization.get("normalized_sha256")
    )
    models_report = worker_report.get("models") or {}
    pipeline_complete = all(
        isinstance(models_report.get(key), Mapping)
        and models_report[key].get("success") is True
        for key in _MODEL_KEYS
    )
    base = {
        "schema_version": DYNAMIC_MECHANICS_SCHEMA_VERSION,
        "scope": plan.get("result_scope", "g6b-heldout-mechanics-pilot"),
        "scientific_claim_boundary": plan.get(
            "claim_boundary", protocol.get("claim_boundary")
        ),
        "protocol_sha256": plan["protocol_sha256"],
        "source_manifest_sha256": plan["source_manifest_sha256"],
        "g6b2_result_sha256": plan["g6b2_result_sha256"],
        "target_subject_id": plan["target_subject_id"],
        "heldout_window_s": [plan["start_time_s"], plan["end_time_s"]],
        "event_sequence": plan["event_sequence"],
        "worker": {
            "opensim_version": worker_report.get("opensim_version"),
            "marker_normalization": normalization,
            "models": {
                key: {
                    "success": (models_report.get(key) or {}).get("success"),
                    "rra_pass_count": (models_report.get(key) or {}).get(
                        "rra_pass_count"
                    ),
                    "com_height_m": (models_report.get(key) or {}).get(
                        "com_height_m"
                    ),
                    "artifacts": (models_report.get(key) or {}).get("artifacts"),
                    "failed_stage": (models_report.get(key) or {}).get(
                        "failed_stage"
                    ),
                    "error_type": (models_report.get(key) or {}).get("error_type"),
                    "error_message": (models_report.get(key) or {}).get(
                        "error_message"
                    ),
                }
                for key in _MODEL_KEYS
            },
        },
    }
    if plan.get("registration_result_sha256"):
        base["registration_result_sha256"] = plan["registration_result_sha256"]
        base["registered_model_sha256"] = plan["registered_model_sha256"]
        base["wrapper_protocol_sha256"] = plan["wrapper_protocol_sha256"]
    if not pipeline_complete:
        checks = {
            "runtime_identity": runtime_ok,
            "worker_input_hashes": input_hashes_ok,
            "marker_header_normalization": marker_normalization_ok,
            "both_model_pipelines_complete": False,
        }
        return {**base, "checks": checks, "pass": False, "models": {}, "waveforms": []}

    grf_path = Path(str(plan["input_paths"]["heldout_ground_reaction"]))
    external_force_rms = _external_force_rms(
        grf_path, float(plan["start_time_s"]), float(plan["end_time_s"])
    )
    model_metrics: Dict[str, Dict[str, Any]] = {}
    artifact_paths: Dict[str, Dict[str, Path]] = {}
    for key in _MODEL_KEYS:
        model_dir = work_dir.resolve() / key
        artifact_paths[key] = _verified_artifact_paths(models_report[key], model_dir)
        paths = artifact_paths[key]
        required_artifacts = {
            "ik_motion",
            "ik_marker_errors",
            "rra_kinematics",
            "rra_actuation",
            "rra_adjusted_model",
            "inverse_dynamics",
        }
        if set(paths) != required_artifacts:
            raise ValueError("G6b-3 worker artifact roles differ from the frozen set")
        metrics = _marker_error_metrics(paths["ik_marker_errors"])
        metrics.update(
            rra_angle_change_metrics(paths["ik_motion"], paths["rra_kinematics"])
        )
        metrics.update(rra_residual_metrics(paths["rra_actuation"]))
        com_height = _finite(models_report[key].get("com_height_m"), "COM height")
        if com_height <= 0:
            raise ValueError("G6b-3 COM height must be positive")
        metrics.update(
            {
                "external_force_rms_n": external_force_rms,
                "com_height_m": com_height,
                "external_moment_reference_rms_nm": external_force_rms * com_height,
                "residual_force_fraction": metrics["residual_force_rms"]
                / external_force_rms,
                "residual_moment_fraction": metrics["residual_moment_rms"]
                / (external_force_rms * com_height),
                "rra_pass_count": 2,
                "artifacts": {
                    role: {
                        "file": path.name,
                        "sha256": sha256_file(path),
                    }
                    for role, path in paths.items()
                },
            }
        )
        model_metrics[key] = metrics

    waveforms: List[Dict[str, Any]] = []
    angle_errors = []
    moment_nrmse = []
    moment_correlations = []
    for coordinate in plan.get("primary_coordinates", []):
        reference_angle, candidate_angle = _aligned_waveform(
            artifact_paths["static_derived"]["rra_kinematics"],
            artifact_paths["dynamic_derived"]["rra_kinematics"],
            str(coordinate),
            str(coordinate),
            convert_angles=True,
        )
        angle = waveform_comparison(reference_angle, candidate_angle)
        reference_moment, candidate_moment = _aligned_waveform(
            artifact_paths["static_derived"]["inverse_dynamics"],
            artifact_paths["dynamic_derived"]["inverse_dynamics"],
            "{}_moment".format(coordinate),
            "{}_moment".format(coordinate),
            convert_angles=False,
        )
        moment = waveform_comparison(reference_moment, candidate_moment)
        angle_errors.append(angle["rmse"])
        moment_nrmse.append(moment["nrmse"])
        moment_correlations.append(moment["correlation"])
        waveforms.append(
            {
                "coordinate": coordinate,
                "joint_angle_rmse_deg": angle["rmse"],
                "joint_angle_correlation": angle["correlation"],
                "joint_moment_rmse_nm": moment["rmse"],
                "joint_moment_reference_rms_nm": moment["reference_rms"],
                "joint_moment_nrmse": moment["nrmse"],
                "joint_moment_correlation": moment["correlation"],
                "sample_count": min(angle["sample_count"], moment["sample_count"]),
            }
        )

    summary = {
        "model_count": len(model_metrics),
        "primary_coordinate_count": len(waveforms),
        "dynamic_ik_rms_degradation_m": model_metrics["dynamic_derived"][
            "ik_rms_error_m"
        ]
        - model_metrics["static_derived"]["ik_rms_error_m"],
        "median_joint_angle_rmse_deg": float(np.median(angle_errors)),
        "max_joint_angle_rmse_deg": float(np.max(angle_errors)),
        "median_joint_moment_nrmse": float(np.median(moment_nrmse)),
        "max_joint_moment_nrmse": float(np.max(moment_nrmse)),
        "minimum_joint_moment_correlation": float(np.min(moment_correlations)),
    }
    checks = {
        "runtime_identity": runtime_ok,
        "worker_input_hashes": input_hashes_ok,
        "marker_header_normalization": marker_normalization_ok,
        "both_model_pipelines_complete": True,
    }
    for key in _MODEL_KEYS:
        metrics = model_metrics[key]
        checks["{}_ik_rms".format(key)] = metrics["ik_rms_error_m"] <= float(
            thresholds["ik_rms_error_m"]
        )
        checks["{}_ik_max".format(key)] = metrics["ik_max_error_m"] <= float(
            thresholds["ik_max_error_m"]
        )
        checks["{}_rra_angle".format(key)] = metrics[
            "rra_angle_change_rms_deg"
        ] <= float(thresholds["rra_angle_change_rms_deg"])
        checks["{}_residual_force".format(key)] = metrics[
            "residual_force_fraction"
        ] <= float(thresholds["residual_force_fraction"])
        checks["{}_residual_moment".format(key)] = metrics[
            "residual_moment_fraction"
        ] <= float(thresholds["residual_moment_fraction"])
    checks.update(
        {
            "dynamic_ik_rms_degradation": summary["dynamic_ik_rms_degradation_m"]
            <= float(thresholds["dynamic_ik_rms_degradation_m"]),
            "median_joint_angle_rmse": summary["median_joint_angle_rmse_deg"]
            <= float(thresholds["median_joint_angle_rmse_deg"]),
            "max_joint_angle_rmse": summary["max_joint_angle_rmse_deg"]
            <= float(thresholds["max_joint_angle_rmse_deg"]),
            "median_joint_moment_nrmse": summary["median_joint_moment_nrmse"]
            <= float(thresholds["median_joint_moment_nrmse"]),
            "max_joint_moment_nrmse": summary["max_joint_moment_nrmse"]
            <= float(thresholds["max_joint_moment_nrmse"]),
            "minimum_joint_moment_correlation": summary[
                "minimum_joint_moment_correlation"
            ]
            >= float(thresholds["minimum_joint_moment_correlation"]),
        }
    )
    return {
        **base,
        "thresholds": dict(thresholds),
        "summary": summary,
        "checks": checks,
        "pass": all(checks.values()),
        "models": model_metrics,
        "waveforms": waveforms,
    }


def write_dynamic_mechanics_results(result: Mapping[str, Any], output: Path) -> None:
    output = output.resolve()
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(
        json.dumps(result, indent=2, sort_keys=True) + "\n", encoding="utf-8"
    )
    model_rows = [
        {"model": key, **{k: v for k, v in value.items() if not isinstance(v, dict)}}
        for key, value in (result.get("models") or {}).items()
    ]
    pd.DataFrame(model_rows).to_csv(output.with_suffix(".csv"), index=False)
    pd.DataFrame(result.get("waveforms", [])).to_csv(
        output.with_name(output.stem + "_waveforms.csv"), index=False
    )
