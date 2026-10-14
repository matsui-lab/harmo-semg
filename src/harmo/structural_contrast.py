"""G6c-S6b paired observed-versus-torso-welded upper-limb contrast."""

from __future__ import annotations

import csv
import json
import math
from pathlib import Path
from typing import Any, Dict, Mapping, Sequence

import numpy as np
import yaml

from harmo.dynamic_calibration import sha256_file
from harmo.whole_body_balance import zero_phase_lowpass


STRUCTURAL_CONTRAST_SCHEMA_VERSION = "1.0.0"
AXES = ("x", "y", "z")


def _load_yaml(path: Path) -> Dict[str, Any]:
    value = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    if not isinstance(value, dict):
        raise ValueError("G6c-S6b YAML root must be a mapping")
    return value


def _load_json(path: Path) -> Dict[str, Any]:
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise ValueError("G6c-S6b JSON root must be a mapping")
    return value


def _subject_mass(path: Path, subject_id: str) -> float:
    with path.open("r", encoding="utf-8", newline="") as stream:
        rows = list(csv.DictReader(stream))
    matches = [
        row for row in rows if str(row.get("subject_id", "")).strip() == subject_id
    ]
    if len(matches) != 1:
        raise ValueError("G6c-S6b subject lookup is not unique")
    mass = float(matches[0]["weight_kg"])
    if not math.isfinite(mass) or mass <= 0:
        raise ValueError("G6c-S6b subject mass is invalid")
    return mass


def _upper_limb_segments(
    markers_m: Mapping[str, np.ndarray],
) -> list[tuple[str, str, np.ndarray]]:
    segments = []
    for side in ("L", "R"):
        shoulder = np.asarray(markers_m[side + "SHO"], dtype=float)
        elbow = np.asarray(markers_m[side + "ELB"], dtype=float)
        wrist = 0.5 * (
            np.asarray(markers_m[side + "WRA"], dtype=float)
            + np.asarray(markers_m[side + "WRB"], dtype=float)
        )
        finger = np.asarray(markers_m[side + "FIN"], dtype=float)
        segments.extend(
            [
                (side + "-humerus", "humerus", 0.5 * (shoulder + elbow)),
                (side + "-forearm", "forearm", 0.5 * (elbow + wrist)),
                (side + "-hand", "hand", 0.5 * (wrist + finger)),
            ]
        )
    return segments


def _torso_frames(
    markers_m: Mapping[str, np.ndarray],
) -> tuple[np.ndarray, np.ndarray, Dict[str, float]]:
    origin = np.asarray(markers_m["TRXO"], dtype=float)
    vectors = np.stack(
        [
            np.asarray(markers_m["TRX" + suffix], dtype=float) - origin
            for suffix in ("A", "L", "P")
        ],
        axis=2,
    )
    lengths = np.linalg.norm(vectors, axis=1)
    if np.any(lengths <= 0) or not np.all(np.isfinite(lengths)):
        raise ValueError("G6c-S6b torso frame has a zero axis")
    rotation = vectors / lengths[:, None, :]
    gram = np.einsum("nki,nkj->nij", rotation, rotation)
    determinants = np.linalg.det(rotation)
    return origin, rotation, {
        "maximum_orthonormality_error": float(
            np.max(np.abs(gram - np.eye(3)))
        ),
        "minimum_determinant": float(np.min(determinants)),
        "maximum_determinant": float(np.max(determinants)),
    }


def _orbital_angular_momentum(
    time_s: np.ndarray,
    whole_body_com_m: np.ndarray,
    segments: Sequence[tuple[float, np.ndarray]],
    *,
    cutoff_hz: float,
    filter_order: int,
) -> tuple[np.ndarray, list[tuple[float, np.ndarray]]]:
    com = zero_phase_lowpass(
        time_s,
        whole_body_com_m,
        cutoff_hz=float(cutoff_hz),
        order=int(filter_order),
    )
    com_velocity = np.gradient(com, time_s, axis=0)
    filtered_segments = []
    angular_momentum = np.zeros((time_s.size, 3), dtype=float)
    for mass, raw_position in segments:
        position = zero_phase_lowpass(
            time_s,
            raw_position,
            cutoff_hz=float(cutoff_hz),
            order=int(filter_order),
        )
        velocity = np.gradient(position, time_s, axis=0)
        angular_momentum += float(mass) * np.cross(
            position - com, velocity - com_velocity
        )
        filtered_segments.append((float(mass), position))
    angular_momentum = zero_phase_lowpass(
        time_s,
        angular_momentum,
        cutoff_hz=float(cutoff_hz),
        order=int(filter_order),
    )
    return angular_momentum, filtered_segments


def _weighted_center(
    segments: Sequence[tuple[float, np.ndarray]]
) -> tuple[float, np.ndarray]:
    total_mass = float(sum(mass for mass, _ in segments))
    if total_mass <= 0:
        raise ValueError("G6c-S6b segment mass must be positive")
    center = (
        sum(mass * position for mass, position in segments) / total_mass
    )
    return total_mass, center


def _finite_ratio(numerator: float, denominator: float) -> float:
    if not math.isfinite(numerator) or not math.isfinite(denominator):
        raise ValueError("G6c-S6b contrast ratio inputs must be finite")
    if abs(denominator) <= np.finfo(float).eps:
        raise ValueError("G6c-S6b observed vertical signal is not identifiable")
    return float(numerator / denominator)


def paired_upper_limb_structural_contrast(
    time_s: Sequence[float],
    markers_m: Mapping[str, np.ndarray],
    *,
    subject_mass_kg: float,
    reference_total_mass_kg: float,
    reference_segment_masses_kg: Mapping[str, float],
    reference_frame_rule: str,
    cutoff_hz: float,
    filter_order: int,
    edge_exclusion_s: float,
) -> tuple[Dict[str, Any], list[Dict[str, Any]]]:
    """Compare observed arm motion with the same masses welded to the torso."""
    time = np.asarray(time_s, dtype=float)
    required = {
        "CentreOfMass",
        "TRXO",
        "TRXA",
        "TRXL",
        "TRXP",
        "LSHO",
        "LELB",
        "LWRA",
        "LWRB",
        "LFIN",
        "RSHO",
        "RELB",
        "RWRA",
        "RWRB",
        "RFIN",
    }
    missing = sorted(required - set(markers_m))
    if missing:
        raise ValueError("G6c-S6b markers are missing: {}".format(missing))
    if time.size < 16 or np.any(np.diff(time) <= 0):
        raise ValueError("G6c-S6b time is invalid")
    if not np.all(np.isfinite(time)):
        raise ValueError("G6c-S6b time must be finite")
    for marker in required:
        values = np.asarray(markers_m[marker], dtype=float)
        if values.shape != (time.size, 3) or not np.all(np.isfinite(values)):
            raise ValueError(
                "G6c-S6b marker {} must be a finite N x 3 array".format(marker)
            )
    if (
        not math.isfinite(subject_mass_kg)
        or subject_mass_kg <= 0
        or not math.isfinite(reference_total_mass_kg)
        or reference_total_mass_kg <= 0
    ):
        raise ValueError("G6c-S6b body masses must be finite and positive")
    if reference_frame_rule != "middle-sample":
        raise ValueError("G6c-S6b reference-frame rule is unsupported")
    reference_index = int(time.size // 2)

    origin, rotation, torso_quality = _torso_frames(markers_m)
    mass_scale = float(subject_mass_kg) / float(reference_total_mass_kg)
    masses = {
        name: float(value) * mass_scale
        for name, value in reference_segment_masses_kg.items()
    }
    for name in ("humerus", "forearm", "hand"):
        if name not in masses or masses[name] <= 0:
            raise ValueError("G6c-S6b reference arm masses are incomplete")

    observed_definitions = _upper_limb_segments(markers_m)
    observed_segments = []
    welded_segments = []
    reference_errors = []
    segment_offsets = {}
    for segment_id, mass_name, observed_position in observed_definitions:
        offset = np.linalg.solve(
            rotation[reference_index],
            observed_position[reference_index] - origin[reference_index],
        )
        welded_position = origin + np.einsum("nij,j->ni", rotation, offset)
        reference_errors.append(
            float(
                np.linalg.norm(
                    welded_position[reference_index]
                    - observed_position[reference_index]
                )
            )
        )
        segment_offsets[segment_id] = offset.tolist()
        observed_segments.append((masses[mass_name], observed_position))
        welded_segments.append((masses[mass_name], welded_position))

    com = np.asarray(markers_m["CentreOfMass"], dtype=float)
    observed_h, observed_filtered = _orbital_angular_momentum(
        time,
        com,
        observed_segments,
        cutoff_hz=cutoff_hz,
        filter_order=filter_order,
    )
    welded_h, welded_filtered = _orbital_angular_momentum(
        time,
        com,
        welded_segments,
        cutoff_hz=cutoff_hz,
        filter_order=filter_order,
    )
    delta_h = observed_h - welded_h
    observed_torque = np.gradient(observed_h, time, axis=0)
    welded_torque = np.gradient(welded_h, time, axis=0)
    delta_torque = observed_torque - welded_torque
    observed_mass, observed_arm_com = _weighted_center(observed_filtered)
    welded_mass, welded_arm_com = _weighted_center(welded_filtered)
    _, observed_arm_com_raw = _weighted_center(observed_segments)
    _, welded_arm_com_raw = _weighted_center(welded_segments)
    whole_body_com_difference = (
        observed_mass
        / float(subject_mass_kg)
        * (observed_arm_com - welded_arm_com)
    )
    retained = (time >= time[0] + float(edge_exclusion_s)) & (
        time <= time[-1] - float(edge_exclusion_s)
    )
    if int(np.sum(retained)) < 16:
        raise ValueError("G6c-S6b edge exclusion retained too few samples")

    def _axis_rms(values: np.ndarray) -> list[float]:
        return np.sqrt(np.mean(values[retained] ** 2, axis=0)).tolist()

    def _axis_peak_to_peak(values: np.ndarray) -> list[float]:
        return np.ptp(values[retained], axis=0).tolist()

    rows = []
    for index in np.flatnonzero(retained):
        row = {"time_s": float(time[index])}
        for axis, label in enumerate(AXES):
            row["observed_angular_momentum_nms_{}".format(label)] = float(
                observed_h[index, axis]
            )
            row["welded_angular_momentum_nms_{}".format(label)] = float(
                welded_h[index, axis]
            )
            row["contrast_angular_momentum_nms_{}".format(label)] = float(
                delta_h[index, axis]
            )
            row["observed_orbital_torque_nm_{}".format(label)] = float(
                observed_torque[index, axis]
            )
            row["welded_orbital_torque_nm_{}".format(label)] = float(
                welded_torque[index, axis]
            )
            row["contrast_orbital_torque_nm_{}".format(label)] = float(
                delta_torque[index, axis]
            )
            row["whole_body_com_difference_m_{}".format(label)] = float(
                whole_body_com_difference[index, axis]
            )
        rows.append(row)

    observed_h_p2p = np.asarray(_axis_peak_to_peak(observed_h))
    delta_h_p2p = np.asarray(_axis_peak_to_peak(delta_h))
    observed_torque_rms = np.asarray(_axis_rms(observed_torque))
    delta_torque_rms = np.asarray(_axis_rms(delta_torque))
    com_norm = np.linalg.norm(whole_body_com_difference[retained], axis=1)
    return {
        "method": "paired-observed-versus-middle-pose-torso-welded-orbital-contrast",
        "reference_frame": {
            "rule": reference_frame_rule,
            "index_zero_based": reference_index,
            "time_s": float(time[reference_index]),
            "maximum_segment_position_error_m": max(reference_errors),
        },
        "torso_frame_quality": torso_quality,
        "torso_fixed_segment_offsets_m": segment_offsets,
        "mass": {
            "subject_mass_kg": float(subject_mass_kg),
            "reference_total_mass_kg": float(reference_total_mass_kg),
            "mass_scale": mass_scale,
            "observed_upper_limb_mass_kg": observed_mass,
            "welded_upper_limb_mass_kg": welded_mass,
            "mass_difference_kg": float(observed_mass - welded_mass),
            "total_mass_preserved": observed_mass == welded_mass,
        },
        "processing": {
            "lowpass_cutoff_hz": float(cutoff_hz),
            "lowpass_order": int(filter_order),
            "edge_exclusion_s": float(edge_exclusion_s),
            "retained_sample_count": int(np.sum(retained)),
            "spin_angular_momentum_included": False,
        },
        "observed": {
            "angular_momentum_axis_rms_nms": _axis_rms(observed_h),
            "angular_momentum_peak_to_peak_nms": observed_h_p2p.tolist(),
            "orbital_torque_axis_rms_nm": observed_torque_rms.tolist(),
        },
        "torso_welded": {
            "angular_momentum_axis_rms_nms": _axis_rms(welded_h),
            "angular_momentum_peak_to_peak_nms": _axis_peak_to_peak(welded_h),
            "orbital_torque_axis_rms_nm": _axis_rms(welded_torque),
        },
        "contrast": {
            "angular_momentum_axis_rms_nms": _axis_rms(delta_h),
            "angular_momentum_peak_to_peak_nms": delta_h_p2p.tolist(),
            "orbital_torque_axis_rms_nm": delta_torque_rms.tolist(),
            "vertical_angular_momentum_peak_to_peak_ratio": float(
                _finite_ratio(delta_h_p2p[2], observed_h_p2p[2])
            ),
            "vertical_orbital_torque_rms_ratio": float(
                _finite_ratio(delta_torque_rms[2], observed_torque_rms[2])
            ),
        },
        "whole_body_com_perturbation": {
            "axis_rms_m": _axis_rms(whole_body_com_difference),
            "vector_rms_m": float(np.sqrt(np.mean(com_norm**2))),
            "maximum_norm_m": float(np.max(com_norm)),
            "reference_pose_norm_m": float(
                observed_mass
                / float(subject_mass_kg)
                * np.linalg.norm(
                    observed_arm_com_raw[reference_index]
                    - welded_arm_com_raw[reference_index]
                )
            ),
            "filtered_reference_pose_norm_m": float(
                np.linalg.norm(whole_body_com_difference[reference_index])
            ),
        },
        "interpretation": (
            "same-marker-samples-and-arm-masses;observed-articulation-versus-"
            "torso-welded-reference-pose-counterfactual;orbital-only"
        ),
    }, rows


def classify_structural_contrast(
    contrast: Mapping[str, Any], *, thresholds: Mapping[str, Any]
) -> Dict[str, Any]:
    frame = contrast.get("torso_frame_quality") or {}
    reference = contrast.get("reference_frame") or {}
    mass = contrast.get("mass") or {}
    com = contrast.get("whole_body_com_perturbation") or {}
    effect = contrast.get("contrast") or {}
    checks = {
        "torso_frame_orthonormality": float(
            frame.get("maximum_orthonormality_error", math.inf)
        )
        <= float(thresholds.get("maximum_torso_orthonormality_error", -math.inf)),
        "torso_frame_right_handed": float(
            frame.get("minimum_determinant", -math.inf)
        )
        > 0,
        "reference_pose_exact": float(
            reference.get("maximum_segment_position_error_m", math.inf)
        )
        <= float(thresholds.get("maximum_reference_pose_error_m", -math.inf)),
        "mass_preserved": mass.get("total_mass_preserved") is True
        and abs(float(mass.get("mass_difference_kg", math.inf)))
        <= float(thresholds.get("maximum_mass_difference_kg", -math.inf)),
        "whole_body_com_vector_rms": float(com.get("vector_rms_m", math.inf))
        <= float(thresholds.get("maximum_whole_body_com_vector_rms_m", -math.inf)),
        "whole_body_com_maximum": float(com.get("maximum_norm_m", math.inf))
        <= float(thresholds.get("maximum_whole_body_com_maximum_m", -math.inf)),
        "vertical_momentum_materiality": float(
            effect.get("vertical_angular_momentum_peak_to_peak_ratio", -math.inf)
        )
        >= float(thresholds.get("minimum_vertical_momentum_contrast_ratio", math.inf)),
        "vertical_torque_materiality": float(
            effect.get("vertical_orbital_torque_rms_ratio", -math.inf)
        )
        >= float(thresholds.get("minimum_vertical_torque_contrast_ratio", math.inf)),
    }
    validity_checks = (
        "torso_frame_orthonormality",
        "torso_frame_right_handed",
        "reference_pose_exact",
        "mass_preserved",
        "whole_body_com_vector_rms",
        "whole_body_com_maximum",
    )
    materiality_checks = (
        "vertical_momentum_materiality",
        "vertical_torque_materiality",
    )
    valid = all(checks[name] for name in validity_checks)
    material = all(checks[name] for name in materiality_checks)
    if not valid:
        classification = "paired-structural-counterfactual-invalid"
    elif material:
        classification = (
            "arm-articulation-material-in-observational-paired-counterfactual"
        )
    else:
        classification = (
            "no-material-arm-articulation-effect-in-observational-"
            "paired-counterfactual"
        )
    return {
        "checks": checks,
        "counterfactual_valid": valid,
        "material_effect_detected": bool(valid and material),
        "classification": classification,
        "study31_cause_identified": False,
        "opensim_rra_contrast_completed": False,
        "mechanics_closed": False,
    }


def build_structural_contrast_plan(
    protocol_path: Path,
    s6_result_path: Path,
    study1_c3d_path: Path,
    subject_info_path: Path,
) -> Dict[str, Any]:
    """Build a checksum-bound G6c-S6b paired structural contrast plan."""
    protocol_path = protocol_path.resolve()
    s6_result_path = s6_result_path.resolve()
    study1_c3d_path = study1_c3d_path.resolve()
    subject_info_path = subject_info_path.resolve()
    protocol = _load_yaml(protocol_path)
    if str(protocol.get("schema_version")) != STRUCTURAL_CONTRAST_SCHEMA_VERSION:
        raise ValueError("unsupported G6c-S6b protocol version")
    s6 = _load_json(s6_result_path)
    if (
        s6.get("scope") != "g6c-s6-structural-adequacy-audit"
        or s6.get("diagnostic_complete") is not True
        or s6.get("pass") is not False
    ):
        raise ValueError("G6c-S6b requires the retained complete S6 result")
    for name, path in {
        "s6_result": s6_result_path,
        "study1_c3d": study1_c3d_path,
        "study1_subject_info": subject_info_path,
    }.items():
        if not path.is_file():
            raise ValueError("G6c-S6b input is missing: {}".format(name))
    worker = s6.get("worker") or {}
    expected_c3d_hash = str(
        (worker.get("input_sha256") or {}).get("study1_c3d", "")
    )
    if sha256_file(study1_c3d_path) != expected_c3d_hash:
        raise ValueError("G6c-S6b Study 1 source differs from S6")

    design = protocol.get("design") or {}
    study1 = design.get("study1") or {}
    proxy = (worker.get("study1") or {}).get("upper_limb_proxy") or {}
    reference = study1.get("reference_model") or {}
    if reference != (proxy.get("reference_model") or {}):
        raise ValueError("G6c-S6b reference masses differ from S6")
    subject_id = str(study1.get("subject_id", ""))
    mass = _subject_mass(subject_info_path, subject_id)
    if mass != float(proxy.get("subject_mass_kg", math.nan)):
        raise ValueError("G6c-S6b subject mass differs from S6")
    paths = {
        "s6_result": s6_result_path,
        "study1_c3d": study1_c3d_path,
        "study1_subject_info": subject_info_path,
    }
    study31 = worker.get("study31") or {}
    return {
        "schema_version": STRUCTURAL_CONTRAST_SCHEMA_VERSION,
        "scope": "g6c-s6b-structural-contrast-plan",
        "protocol_sha256": sha256_file(protocol_path),
        "prior_result_sha256": {"s6": sha256_file(s6_result_path)},
        "input_paths": {name: str(path) for name, path in paths.items()},
        "input_sha256": {name: sha256_file(path) for name, path in paths.items()},
        "study1": study1,
        "study1_subject_mass_kg": mass,
        "study31_descriptive_reference": {
            "vertical_required_angular_momentum_peak_to_peak_mean_nms": float(
                study31[
                    "vertical_required_angular_momentum_peak_to_peak_mean_nms"
                ]
            ),
            "vertical_periodic_moment_rms_mean_nm": float(
                study31["vertical_periodic_moment_rms_mean_nm"]
            ),
            "source_result_sha256": sha256_file(s6_result_path),
            "causal_comparison": False,
        },
        "thresholds": dict(protocol.get("thresholds") or {}),
        "claim_boundary": str(protocol.get("claim_boundary", "")),
    }


def evaluate_structural_contrast(
    plan: Mapping[str, Any], worker: Mapping[str, Any]
) -> Dict[str, Any]:
    diagnosis = worker.get("diagnosis") or {}
    checks = {
        "worker_scope": worker.get("scope")
        == "g6c-s6b-structural-contrast-worker",
        "worker_inputs": worker.get("input_sha256") == plan.get("input_sha256"),
        "prior_result": worker.get("prior_result_sha256")
        == plan.get("prior_result_sha256"),
        "analysis_complete": worker.get("analysis_complete") is True,
        "artifacts": worker.get("artifacts_complete") is True,
    }
    diagnostic_complete = all(checks.values())
    return {
        "schema_version": STRUCTURAL_CONTRAST_SCHEMA_VERSION,
        "scope": "g6c-s6b-structural-contrast-audit",
        "pass": False,
        "diagnostic_complete": diagnostic_complete,
        "counterfactual_valid": bool(
            diagnostic_complete and diagnosis.get("counterfactual_valid") is True
        ),
        "material_effect_detected": bool(
            diagnostic_complete
            and diagnosis.get("material_effect_detected") is True
        ),
        "mechanics_closed": False,
        "study31_cause_identified": False,
        "checks": checks,
        "thresholds": dict(plan.get("thresholds") or {}),
        "worker": dict(worker),
        "scientific_claim_boundary": str(plan.get("claim_boundary", "")),
        "next_gate": (
            "g6c-s6c-subject-activity-matched-full-body-rra"
            if diagnostic_complete
            else "blocked-at-g6c-s6b-analysis"
        ),
    }


def write_structural_contrast(
    result: Mapping[str, Any], output: Path
) -> None:
    output = output.resolve()
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(
        json.dumps(result, indent=2, sort_keys=True) + "\n", encoding="utf-8"
    )
