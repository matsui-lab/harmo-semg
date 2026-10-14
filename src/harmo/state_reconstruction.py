"""G6c-S3 RRA-native state reconstruction and mechanics identity audit."""

from __future__ import annotations

import json
import math
from pathlib import Path
from typing import Any, Dict, Mapping, Sequence

import numpy as np
import yaml

from harmo.dynamic_calibration import sha256_file, validate_public_source_bundle


STATE_RECONSTRUCTION_SCHEMA_VERSION = "1.0.0"


def external_source_grid_mask(
    time_s: Sequence[float], *, sampling_hz: float, tolerance_s: float = 1e-11
) -> np.ndarray:
    """Identify observed source samples without accepting integrator substeps."""
    time = np.asarray(time_s, dtype=float)
    rate = float(sampling_hz)
    tolerance = float(tolerance_s)
    if time.ndim != 1 or time.size == 0 or not np.all(np.isfinite(time)):
        raise ValueError("source-grid time must be a finite nonempty vector")
    if rate <= 0 or tolerance < 0:
        raise ValueError("source-grid rate and tolerance are invalid")
    nearest = np.round(time * rate) / rate
    return np.abs(time - nearest) <= tolerance


def _load_yaml(path: Path) -> Dict[str, Any]:
    value = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    if not isinstance(value, dict):
        raise ValueError("G6c-S3 YAML root must be a mapping")
    return value


def _only_match(directory: Path, pattern: str, label: str) -> Path:
    matches = sorted(directory.glob(pattern))
    if len(matches) != 1:
        raise ValueError(
            "G6c-S3 requires exactly one {} matching {}".format(label, pattern)
        )
    return matches[0].resolve()


def vector_identity_metrics(
    observed: Sequence[Sequence[float]],
    predicted: Sequence[Sequence[float]],
    *,
    reference: float,
) -> Dict[str, Any]:
    """Quantify equality of two three-dimensional mechanics vectors."""
    left = np.asarray(observed, dtype=float)
    right = np.asarray(predicted, dtype=float)
    if left.shape != right.shape or left.ndim != 2 or left.shape[1] != 3:
        raise ValueError("identity vectors must both have shape (n, 3)")
    if left.shape[0] == 0 or not np.all(np.isfinite(left)) or not np.all(
        np.isfinite(right)
    ):
        raise ValueError("identity vectors must be nonempty and finite")
    reference_value = float(reference)
    if not math.isfinite(reference_value) or reference_value <= 0:
        raise ValueError("identity reference must be finite and positive")

    def rms(values: np.ndarray) -> float:
        return float(np.sqrt(np.mean(np.sum(np.square(values), axis=1))))

    difference = left - right
    difference_rms = rms(difference)
    return {
        "sample_count": int(left.shape[0]),
        "observed_vector_rms": rms(left),
        "predicted_vector_rms": rms(right),
        "difference_vector_rms": difference_rms,
        "difference_fraction": difference_rms / reference_value,
        "difference_axis_rms": np.sqrt(np.mean(np.square(difference), axis=0)).tolist(),
        "maximum_absolute_difference": float(np.max(np.abs(difference))),
        "reference": reference_value,
    }


def aggregate_rigid_body_dynamics(
    body_rows: Sequence[Mapping[str, Any]],
) -> Dict[str, np.ndarray]:
    """Aggregate body COM accelerations and analytic central H-dot."""
    if not body_rows:
        raise ValueError("body dynamics must contain at least one material body")
    total_mass = 0.0
    position_weighted = np.zeros(3)
    acceleration_weighted = np.zeros(3)
    normalized_rows = []
    for row in body_rows:
        mass = float(row["mass_kg"])
        center = np.asarray(row["center_position_m"], dtype=float)
        acceleration = np.asarray(row["center_acceleration_m_s2"], dtype=float)
        rotational_hdot = np.asarray(row["rotational_hdot_nm"], dtype=float)
        if mass <= 0 or any(value.shape != (3,) for value in (center, acceleration, rotational_hdot)):
            raise ValueError("body dynamics contain invalid mass or vector shape")
        if not all(
            np.all(np.isfinite(value))
            for value in (center, acceleration, rotational_hdot)
        ):
            raise ValueError("body dynamics vectors must be finite")
        total_mass += mass
        position_weighted += mass * center
        acceleration_weighted += mass * acceleration
        normalized_rows.append((mass, center, acceleration, rotational_hdot))
    system_center = position_weighted / total_mass
    system_acceleration = acceleration_weighted / total_mass
    angular_momentum_rate = np.zeros(3)
    for mass, center, acceleration, rotational_hdot in normalized_rows:
        angular_momentum_rate += rotational_hdot + np.cross(
            center - system_center,
            mass * (acceleration - system_acceleration),
        )
    return {
        "mass_kg": np.asarray(total_mass),
        "center_position_m": system_center,
        "center_acceleration_m_s2": system_acceleration,
        "central_angular_momentum_rate_nm": angular_momentum_rate,
    }


def classify_state_reconstruction(
    *,
    force_identity: Mapping[str, Any],
    moment_identity: Mapping[str, Any],
    native_force_fraction: float,
    native_moment_fraction: float,
    position_force_fraction: float,
    position_moment_fraction: float,
    thresholds: Mapping[str, Any],
) -> Dict[str, Any]:
    """Separate evaluator identity from residual-actuator quality."""
    force_identity_pass = float(force_identity["difference_fraction"]) <= float(
        thresholds["maximum_force_identity_error_fraction"]
    )
    moment_identity_pass = float(moment_identity["difference_fraction"]) <= float(
        thresholds["maximum_moment_identity_error_fraction"]
    )
    native_force_pass = float(native_force_fraction) <= float(
        thresholds["residual_force_fraction"]
    )
    native_moment_pass = float(native_moment_fraction) <= float(
        thresholds["residual_moment_fraction"]
    )
    if not (force_identity_pass and moment_identity_pass):
        classification = "rra-native-dynamics-identity-failed"
    elif native_force_pass and native_moment_pass:
        classification = "state-reconstruction-bias-resolved-mechanics"
    elif native_force_pass:
        classification = (
            "state-reconstruction-bias-localized-common-rra-moment-failure-remains"
        )
    else:
        classification = "state-reconstruction-bias-localized-rra-residuals-remain"
    return {
        "classification": classification,
        "force_identity_pass": bool(force_identity_pass),
        "moment_identity_pass": bool(moment_identity_pass),
        "native_force_pass": bool(native_force_pass),
        "native_moment_pass": bool(native_moment_pass),
        "native_mechanics_closed": bool(native_force_pass and native_moment_pass),
        "position_derived_force_fraction": float(position_force_fraction),
        "position_derived_moment_fraction": float(position_moment_fraction),
        "native_force_fraction": float(native_force_fraction),
        "native_moment_fraction": float(native_moment_fraction),
        "force_fraction_change": float(native_force_fraction)
        - float(position_force_fraction),
        "moment_fraction_change": float(native_moment_fraction)
        - float(position_moment_fraction),
        "thresholds": dict(thresholds),
    }


def build_state_reconstruction_plan(
    protocol_path: Path,
    source_manifest_path: Path,
    data_root: Path,
    s2_result_path: Path,
    s2_work_dir: Path,
) -> Dict[str, Any]:
    """Build a checksum-bound plan from public loads and a complete S2 workdir."""
    protocol_path = protocol_path.resolve()
    source_manifest_path = source_manifest_path.resolve()
    s2_result_path = s2_result_path.resolve()
    s2_work_dir = s2_work_dir.resolve()
    protocol = _load_yaml(protocol_path)
    if str(protocol.get("schema_version")) != STATE_RECONSTRUCTION_SCHEMA_VERSION:
        raise ValueError("unsupported G6c-S3 protocol version")
    s2_result = json.loads(s2_result_path.read_text(encoding="utf-8"))
    if s2_result.get("scope") != "g6c-s2-mechanics-localization-audit":
        raise ValueError("G6c-S3 source result is not G6c-S2")
    if s2_result.get("diagnostic_complete") is not True:
        raise ValueError("G6c-S3 requires a completed G6c-S2 diagnostic")
    s2_worker = s2_result.get("worker") or {}
    design = protocol.get("design") or {}
    declared = design.get("source_work_artifacts") or {}
    pass2_dir = s2_work_dir / str(declared.get("pass2_directory", ""))
    paths = {
        "adjusted_model": s2_work_dir / str(declared.get("adjusted_model", "")),
        "adaptive_kinematics": s2_work_dir
        / str(declared.get("adaptive_kinematics", "")),
        "source_grid_kinematics": s2_work_dir
        / str(declared.get("source_grid_kinematics", "")),
        "factorial_timeseries": s2_work_dir
        / str(declared.get("factorial_timeseries", "")),
        "s2_worker_report": s2_work_dir
        / str(declared.get("worker_report", "")),
        "native_states": _only_match(pass2_dir, "*states.sto", "pass-2 states"),
        "native_q": _only_match(pass2_dir, "*Kinematics_q.sto", "pass-2 q"),
        "native_u": _only_match(pass2_dir, "*Kinematics_u.sto", "pass-2 u"),
        "native_udot": _only_match(
            pass2_dir, "*Kinematics_dudt.sto", "pass-2 udot"
        ),
        "rra_actuation": _only_match(
            pass2_dir, "*Actuation_force.sto", "pass-2 actuation"
        ),
    }
    for name, path in paths.items():
        if not path.is_file():
            raise ValueError("G6c-S3 work artifact is missing: {}".format(name))
        paths[name] = path.resolve()
    local_worker = json.loads(paths["s2_worker_report"].read_text(encoding="utf-8"))
    if local_worker != s2_worker:
        raise ValueError("G6c-S3 S2 worker report differs from the frozen result")
    hashes = {name: sha256_file(path) for name, path in paths.items()}
    expected_artifacts = s2_worker.get("artifacts") or {}
    required_hashes = {
        "adjusted_model": "rra_adjusted_model_sha256",
        "source_grid_kinematics": "rra_source_grid_sha256",
        "factorial_timeseries": "timeseries_sha256",
        "rra_actuation": "rra_actuation_sha256",
    }
    for name, artifact_key in required_hashes.items():
        if hashes[name] != str(expected_artifacts.get(artifact_key, "")):
            raise ValueError("G6c-S3 S2 artifact hash mismatch: {}".format(name))
    if hashes["adaptive_kinematics"] != hashes["native_q"]:
        raise ValueError("G6c-S3 adaptive kinematics are not the pass-2 q output")

    source_audit = validate_public_source_bundle(source_manifest_path, data_root)
    if not source_audit["pass"]:
        raise ValueError("G6c-S3 public source bundle failed checksum verification")
    role = str(design.get("public_ground_reaction_role", ""))
    role_entry = source_audit["assets_by_role"].get(role)
    if not role_entry:
        raise ValueError("G6c-S3 public ground reaction role is missing")
    ground_reaction = Path(str(role_entry["absolute_path"])).resolve()
    paths["ground_reaction"] = ground_reaction
    hashes["ground_reaction"] = sha256_file(ground_reaction)

    thresholds = protocol.get("thresholds") or {}
    s2_thresholds = s2_result.get("thresholds") or {}
    for key in ("residual_force_fraction", "residual_moment_fraction"):
        if float(thresholds.get(key, math.nan)) != float(
            s2_thresholds.get(key, math.nan)
        ):
            raise ValueError("G6c-S3 changed the frozen mechanics threshold")
    position_derived = (
        ((s2_worker.get("balances") or {}).get("mot") or {}).get(
            "adjusted_model_rra_kinematics"
        )
        or {}
    )
    if not position_derived:
        raise ValueError("G6c-S3 lacks the position-derived S2 comparison")
    return {
        "schema_version": STATE_RECONSTRUCTION_SCHEMA_VERSION,
        "scope": "g6c-s3-state-reconstruction-plan",
        "protocol_sha256": sha256_file(protocol_path),
        "source_manifest_sha256": source_audit["manifest_sha256"],
        "s2_result_sha256": sha256_file(s2_result_path),
        "input_paths": {name: str(path) for name, path in paths.items()},
        "input_sha256": hashes,
        "comparison_window_s": [
            float(value) for value in design.get("comparison_window_s", [])
        ],
        "expected_minimum_native_rows": int(
            design.get("expected_minimum_native_rows", 0)
        ),
        "identity_primary_grid": str(design.get("identity_primary_grid", "")),
        "adaptive_substeps_role": str(design.get("adaptive_substeps_role", "")),
        "gravity_m_s2": [float(value) for value in design.get("gravity_m_s2", [])],
        "vertical_axis": int(design.get("vertical_axis", 1)),
        "residual_force_application": design.get("residual_force_application")
        or {},
        "native_dynamics": design.get("native_dynamics") or {},
        "position_derived": position_derived,
        "thresholds": dict(thresholds),
        "audit_history": protocol.get("audit_history") or [],
        "opensim_required_version": "4.5.2",
    }


def evaluate_state_reconstruction(
    plan: Mapping[str, Any], worker: Mapping[str, Any]
) -> Dict[str, Any]:
    """Evaluate diagnostic integrity separately from mechanics closure."""
    checks = {
        "worker_scope": worker.get("scope")
        == "g6c-s3-state-reconstruction-worker",
        "worker_input_hashes": worker.get("input_sha256") == plan.get("input_sha256"),
        "s2_result_hash": worker.get("s2_result_sha256")
        == plan.get("s2_result_sha256"),
        "native_grid_exact": worker.get("native_grid_exact") is True,
        "native_sample_count": int(worker.get("native_sample_count", 0))
        >= int(plan.get("expected_minimum_native_rows", 0)),
        "force_identity": ((worker.get("diagnosis") or {}).get("force_identity_pass"))
        is True,
        "moment_identity": (
            (worker.get("diagnosis") or {}).get("moment_identity_pass")
        )
        is True,
        "constraint_integrity": worker.get("constraint_integrity_pass") is True,
        "native_state_consistency": worker.get("native_state_consistency_pass")
        is True,
        "body_angular_momentum_identity": worker.get(
            "body_angular_momentum_identity_pass"
        )
        is True,
    }
    diagnostic_complete = all(checks.values())
    mechanics_closed = bool(
        diagnostic_complete
        and (worker.get("diagnosis") or {}).get("native_mechanics_closed") is True
    )
    return {
        "schema_version": STATE_RECONSTRUCTION_SCHEMA_VERSION,
        "scope": "g6c-s3-state-reconstruction-audit",
        "pass": mechanics_closed,
        "diagnostic_complete": diagnostic_complete,
        "mechanics_closed": mechanics_closed,
        "checks": checks,
        "thresholds": dict(plan.get("thresholds") or {}),
        "worker": dict(worker),
        "scientific_claim_boundary": (
            "G6c-S3 distinguishes state-reconstruction bias from native RRA "
            "residual quality; it does not validate personalization"
        ),
        "next_gate": "g6d" if mechanics_closed else "blocked-at-g6c-s3",
    }


def write_state_reconstruction(result: Mapping[str, Any], output: Path) -> None:
    output = output.resolve()
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(
        json.dumps(result, indent=2, sort_keys=True) + "\n", encoding="utf-8"
    )
