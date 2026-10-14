"""Leakage-resistant planning and evaluation for dynamic marker registration."""

from __future__ import annotations

import json
import math
import xml.etree.ElementTree as ET
from pathlib import Path
from typing import Any, Dict, Mapping

import pandas as pd
import yaml

from harmo.dynamic_calibration import (
    _read_verified_trc,
    sha256_file,
    validate_public_source_bundle,
)
from harmo.dynamic_mechanics import build_dynamic_mechanics_plan


DYNAMIC_REGISTRATION_SCHEMA_VERSION = "1.0.0"


def _load_yaml(path: Path) -> Dict[str, Any]:
    value = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    if not isinstance(value, dict):
        raise ValueError("G6b-3R YAML root must be a mapping")
    return value


def _finite(value: Any, label: str) -> float:
    numeric = float(value)
    if not math.isfinite(numeric):
        raise ValueError("{} must be finite".format(label))
    return numeric


def _active_ik_markers(path: Path) -> list[str]:
    root = ET.parse(str(path)).getroot()
    return [
        str(task.get("name", "")).strip()
        for task in root.iter("IKMarkerTask")
        if str(task.findtext("apply", "false")).strip().lower() == "true"
    ]


def build_dynamic_registration_plan(
    protocol_path: Path,
    manifest_path: Path,
    data_root: Path,
    base_model_path: Path,
    g6b2_result_path: Path,
    retained_g6b3_result_path: Path,
) -> Dict[str, Any]:
    """Bind the worker only to calibration inputs and the G6b-2 model."""
    protocol_path = protocol_path.resolve()
    manifest_path = manifest_path.resolve()
    base_model_path = base_model_path.resolve()
    g6b2_result_path = g6b2_result_path.resolve()
    retained_g6b3_result_path = retained_g6b3_result_path.resolve()
    protocol = _load_yaml(protocol_path)
    if str(protocol.get("schema_version")) != DYNAMIC_REGISTRATION_SCHEMA_VERSION:
        raise ValueError("unsupported G6b-3R protocol version")
    audit = validate_public_source_bundle(manifest_path, data_root)
    if not audit["pass"]:
        raise ValueError("G6b-3R calibration source bundle failed verification")
    roles = audit["assets_by_role"]
    preconditions = protocol.get("preconditions") or {}
    g6b2 = json.loads(g6b2_result_path.read_text(encoding="utf-8"))
    if g6b2.get("pass") is not True:
        raise ValueError("G6b-3R requires the accepted G6b-2 result")
    if sha256_file(g6b2_result_path) != str(
        preconditions.get("g6b2_result_sha256", "")
    ):
        raise ValueError("G6b-3R G6b-2 result checksum mismatch")
    if sha256_file(retained_g6b3_result_path) != str(
        preconditions.get("retained_g6b3_result_sha256", "")
    ):
        raise ValueError("G6b-3R retained baseline checksum mismatch")
    base_hash = str(preconditions.get("base_dynamic_model_sha256", ""))
    if (
        not base_model_path.is_file()
        or sha256_file(base_model_path) != base_hash
        or g6b2.get("generated_model_sha256") != base_hash
    ):
        raise ValueError("G6b-3R base dynamic model checksum mismatch")

    design = protocol.get("design") or {}
    calibration_roles = [str(value) for value in design.get("calibration_roles", [])]
    ik_role = str(design.get("ik_setup_role", ""))
    excluded_roles = [
        str(value) for value in design.get("excluded_from_registration_worker", [])
    ]
    required_roles = calibration_roles + [ik_role]
    if (
        len(calibration_roles) != 3
        or len(set(required_roles)) != len(required_roles)
        or any(not value for value in required_roles)
    ):
        raise ValueError("G6b-3R requires three unique calibration trials and IK setup")
    if set(required_roles) & set(excluded_roles):
        raise ValueError("G6b-3R excluded input entered worker roles")
    missing = sorted(set(required_roles) - set(roles))
    if missing:
        raise ValueError("G6b-3R source manifest lacks roles: {}".format(missing))

    window = design.get("calibration_window_s") or []
    if len(window) != 2:
        raise ValueError("G6b-3R calibration window must have two bounds")
    start_time = _finite(window[0], "G6b-3R calibration start")
    end_time = _finite(window[1], "G6b-3R calibration end")
    if end_time <= start_time:
        raise ValueError("G6b-3R calibration window is empty")
    for role in calibration_roles:
        data = _read_verified_trc(roles[role])
        if float(data["time"].min()) > start_time or float(data["time"].max()) < (
            end_time
        ):
            raise ValueError("G6b-3R trial does not cover calibration window: {}".format(role))

    active_markers = [str(value) for value in design.get("active_markers", [])]
    ik_path = Path(str(roles[ik_role]["absolute_path"]))
    if _active_ik_markers(ik_path) != active_markers:
        raise ValueError("G6b-3R active marker order differs from public IK setup")
    registration = protocol.get("registration") or {}
    iteration_count = int(registration.get("iteration_count", 0))
    sampled_stride = int(registration.get("sampled_frame_stride", 0))
    if iteration_count < 1 or sampled_stride < 1:
        raise ValueError("G6b-3R iteration count and frame stride must be positive")

    input_paths = {
        role: str(Path(str(roles[role]["absolute_path"]))) for role in required_roles
    }
    input_hashes = {role: roles[role]["observed_sha256"] for role in required_roles}
    input_paths["base_dynamic_model"] = str(base_model_path)
    input_hashes["base_dynamic_model"] = base_hash
    return {
        "schema_version": DYNAMIC_REGISTRATION_SCHEMA_VERSION,
        "scope": "g6b-dynamic-marker-registration-plan",
        "protocol_sha256": sha256_file(protocol_path),
        "source_manifest_sha256": audit["manifest_sha256"],
        "g6b2_result_sha256": sha256_file(g6b2_result_path),
        "retained_g6b3_result_sha256": sha256_file(retained_g6b3_result_path),
        "target_subject_id": str(preconditions.get("target_subject_id", "")),
        "required_opensim_version": "4.5.2",
        "calibration_roles": calibration_roles,
        "excluded_roles": excluded_roles,
        "start_time_s": start_time,
        "end_time_s": end_time,
        "active_markers": active_markers,
        "input_paths": input_paths,
        "input_sha256": input_hashes,
        "registration": dict(registration),
        "thresholds": dict(protocol.get("thresholds") or {}),
        "claim_boundary": protocol.get("claim_boundary"),
    }


def evaluate_dynamic_registration(
    protocol_path: Path,
    plan: Mapping[str, Any],
    worker: Mapping[str, Any],
    registered_model_path: Path,
) -> Dict[str, Any]:
    """Evaluate calibration-only registration without opening hidden inputs."""
    protocol = _load_yaml(protocol_path.resolve())
    thresholds = protocol.get("thresholds") or {}
    registered_model_path = registered_model_path.resolve()
    trials = worker.get("trials") or {}
    markers = worker.get("markers") or []
    final_metrics = [trials.get(role, {}).get("final") or {} for role in plan["calibration_roles"]]
    initial_metrics = [
        trials.get(role, {}).get("initial") or {} for role in plan["calibration_roles"]
    ]
    improvements = []
    for initial, final in zip(initial_metrics, final_metrics):
        initial_rms = _finite(initial.get("ik_rms_error_m"), "initial IK RMS")
        final_rms = _finite(final.get("ik_rms_error_m"), "final IK RMS")
        improvements.append((initial_rms - final_rms) / initial_rms)
    displacements = [
        _finite(row.get("total_displacement_m"), "marker displacement")
        for row in markers
    ]
    local_mads = [
        _finite(value, "marker local MAD")
        for row in markers
        for value in (row.get("within_trial_local_mad_m") or {}).values()
    ]
    output_hash = sha256_file(registered_model_path) if registered_model_path.is_file() else None
    checks = {
        "runtime_identity": str(worker.get("opensim_version", "")).startswith(
            str(plan["required_opensim_version"])
        ),
        "worker_input_hashes": worker.get("input_sha256") == plan.get("input_sha256"),
        "hidden_inputs_unavailable": worker.get("hidden_inputs_available") is False,
        "iteration_count": worker.get("iteration_count")
        == int(plan["registration"]["iteration_count"]),
        "registered_model_hash": output_hash == worker.get("output_model_sha256"),
        "marker_coverage": len(markers) == len(plan["active_markers"]),
        "minimum_trial_rms_improvement": min(improvements)
        >= float(thresholds["minimum_trial_rms_improvement_fraction"]),
        "final_calibration_ik_rms": max(
            _finite(value.get("ik_rms_error_m"), "final IK RMS")
            for value in final_metrics
        )
        <= float(thresholds["final_calibration_ik_rms_error_m"]),
        "final_calibration_ik_max": max(
            _finite(value.get("ik_max_error_m"), "final IK max")
            for value in final_metrics
        )
        <= float(thresholds["final_calibration_ik_max_error_m"]),
        "marker_displacement": max(displacements)
        <= float(thresholds["maximum_marker_displacement_m"]),
        "within_trial_local_mad": max(local_mads)
        <= float(thresholds["maximum_within_trial_local_mad_m"]),
    }
    return {
        "schema_version": DYNAMIC_REGISTRATION_SCHEMA_VERSION,
        "scope": "g6b-dynamic-marker-registration-feasibility",
        "scientific_claim_boundary": plan.get("claim_boundary"),
        "protocol_sha256": plan["protocol_sha256"],
        "source_manifest_sha256": plan["source_manifest_sha256"],
        "g6b2_result_sha256": plan["g6b2_result_sha256"],
        "retained_g6b3_result_sha256": plan["retained_g6b3_result_sha256"],
        "target_subject_id": plan["target_subject_id"],
        "registered_model_sha256": output_hash,
        "thresholds": dict(thresholds),
        "summary": {
            "trial_count": len(final_metrics),
            "marker_count": len(markers),
            "minimum_trial_rms_improvement_fraction": min(improvements),
            "maximum_final_calibration_ik_rms_error_m": max(
                float(value["ik_rms_error_m"]) for value in final_metrics
            ),
            "maximum_final_calibration_ik_max_error_m": max(
                float(value["ik_max_error_m"]) for value in final_metrics
            ),
            "median_marker_displacement_m": float(pd.Series(displacements).median()),
            "maximum_marker_displacement_m": max(displacements),
            "maximum_within_trial_local_mad_m": max(local_mads),
        },
        "checks": checks,
        "pass": all(checks.values()),
        "trials": trials,
        "markers": markers,
        "worker": {
            "opensim_version": worker.get("opensim_version"),
            "iteration_count": worker.get("iteration_count"),
            "hidden_inputs_available": worker.get("hidden_inputs_available"),
            "output_model_sha256": worker.get("output_model_sha256"),
            "normalizations": worker.get("normalizations"),
        },
    }


def build_registered_mechanics_plan(
    wrapper_protocol_path: Path,
    baseline_protocol_path: Path,
    manifest_path: Path,
    data_root: Path,
    base_model_path: Path,
    registered_model_path: Path,
    g6b2_result_path: Path,
    registration_result_path: Path,
) -> Dict[str, Any]:
    """Reuse the unchanged G6b-3 contract with the registered model artifact."""
    wrapper_protocol_path = wrapper_protocol_path.resolve()
    baseline_protocol_path = baseline_protocol_path.resolve()
    registered_model_path = registered_model_path.resolve()
    registration_result_path = registration_result_path.resolve()
    wrapper = _load_yaml(wrapper_protocol_path)
    if str(wrapper.get("schema_version")) != DYNAMIC_REGISTRATION_SCHEMA_VERSION:
        raise ValueError("unsupported G6b-3R-H protocol version")
    if sha256_file(baseline_protocol_path) != str(
        wrapper.get("baseline_mechanics_protocol_sha256", "")
    ):
        raise ValueError("G6b-3R-H baseline mechanics protocol checksum mismatch")
    if sha256_file(registration_result_path) != str(
        wrapper.get("registration_result_sha256", "")
    ):
        raise ValueError("G6b-3R-H registration result checksum mismatch")
    registration = json.loads(registration_result_path.read_text(encoding="utf-8"))
    registered_hash = str(wrapper.get("registered_model_sha256", ""))
    if (
        not registered_model_path.is_file()
        or sha256_file(registered_model_path) != registered_hash
        or registration.get("registered_model_sha256") != registered_hash
    ):
        raise ValueError("G6b-3R-H registered model checksum mismatch")
    if registration.get("worker", {}).get("hidden_inputs_available") is not False:
        raise ValueError("G6b-3R-H registration did not attest hidden-input isolation")
    if registration.get("pass") is not True and wrapper.get(
        "allow_exploratory_mechanics_after_failed_registration_gate"
    ) is not True:
        raise ValueError("G6b-3R-H requires a passing registration result")

    plan = build_dynamic_mechanics_plan(
        baseline_protocol_path,
        manifest_path,
        data_root,
        base_model_path,
        g6b2_result_path,
    )
    if plan["input_sha256"]["dynamic_derived_model"] != str(
        wrapper.get("base_dynamic_model_sha256", "")
    ):
        raise ValueError("G6b-3R-H base model differs from the retained baseline")
    plan["input_paths"]["dynamic_derived_model"] = str(registered_model_path)
    plan["input_sha256"]["dynamic_derived_model"] = registered_hash
    plan["models"]["dynamic_derived"].update(
        {
            "registration_applied": True,
            "registration_result_sha256": sha256_file(registration_result_path),
            "registration_gate_pass": registration.get("pass"),
        }
    )
    plan.update(
        {
            "result_scope": "g6b-registered-heldout-mechanics-exploratory",
            "claim_boundary": wrapper.get("claim_boundary"),
            "wrapper_protocol_sha256": sha256_file(wrapper_protocol_path),
            "registration_result_sha256": sha256_file(registration_result_path),
            "registered_model_sha256": registered_hash,
        }
    )
    return plan


def write_dynamic_registration_results(result: Mapping[str, Any], output: Path) -> None:
    output = output.resolve()
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(
        json.dumps(result, indent=2, sort_keys=True) + "\n", encoding="utf-8"
    )
    pd.DataFrame(result.get("markers") or []).to_csv(
        output.with_suffix(".csv"), index=False
    )
