"""G6c-S4 decomposition of the remaining RRA residual moment."""

from __future__ import annotations

import json
import math
from pathlib import Path
from typing import Any, Dict, Mapping, Sequence

import numpy as np
import yaml

from harmo.dynamic_calibration import sha256_file, validate_public_source_bundle


MOMENT_LOCALIZATION_SCHEMA_VERSION = "1.0.0"


def _load_yaml(path: Path) -> Dict[str, Any]:
    value = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    if not isinstance(value, dict):
        raise ValueError("G6c-S4 YAML root must be a mapping")
    return value


def _only_match(directory: Path, pattern: str, label: str) -> Path:
    matches = sorted(directory.glob(pattern))
    if len(matches) != 1:
        raise ValueError(
            "G6c-S4 requires exactly one {} matching {}".format(label, pattern)
        )
    return matches[0].resolve()


def _finite_vectors(values: Sequence[Sequence[float]], label: str) -> np.ndarray:
    array = np.asarray(values, dtype=float)
    if (
        array.ndim != 2
        or array.shape[0] == 0
        or array.shape[1] != 3
        or not np.all(np.isfinite(array))
    ):
        raise ValueError("{} must be a nonempty finite (n, 3) matrix".format(label))
    return array


def moment_component_metrics(
    values: Sequence[Sequence[float]], *, reference_nm: float
) -> Dict[str, Any]:
    """Separate axis, signed-mean, and zero-mean moment contributions."""
    array = _finite_vectors(values, "moment values")
    reference = float(reference_nm)
    if not math.isfinite(reference) or reference <= 0:
        raise ValueError("moment reference must be finite and positive")
    mean = np.mean(array, axis=0)
    centered = array - mean[None, :]
    axis_rms = np.sqrt(np.mean(np.square(array), axis=0))
    axis_sd = np.sqrt(np.mean(np.square(centered), axis=0))
    vector_rms = float(np.sqrt(np.sum(np.square(axis_rms))))
    centered_vector_rms = float(np.sqrt(np.sum(np.square(axis_sd))))
    mean_magnitude = float(np.linalg.norm(mean))
    energy = float(vector_rms**2)
    return {
        "sample_count": int(array.shape[0]),
        "axis_mean_nm": mean.tolist(),
        "axis_rms_nm": axis_rms.tolist(),
        "axis_zero_mean_rms_nm": axis_sd.tolist(),
        "axis_energy_share": (np.square(axis_rms) / energy).tolist(),
        "vector_rms_nm": vector_rms,
        "fraction": vector_rms / reference,
        "mean_vector_magnitude_nm": mean_magnitude,
        "mean_fraction": mean_magnitude / reference,
        "mean_energy_share": float(mean_magnitude**2 / energy),
        "zero_mean_vector_rms_nm": centered_vector_rms,
        "zero_mean_fraction": centered_vector_rms / reference,
        "reference_nm": reference,
    }


def tracking_error_metrics(
    values_by_coordinate: Mapping[str, Sequence[float]],
    *, translational_coordinates: Sequence[str],
) -> Dict[str, Any]:
    """Summarize RRA position errors without mixing radians and metres."""
    if not values_by_coordinate:
        raise ValueError("tracking errors must contain at least one coordinate")
    normalized = {
        str(name): np.asarray(values, dtype=float)
        for name, values in values_by_coordinate.items()
    }
    lengths = {values.size for values in normalized.values()}
    if len(lengths) != 1 or next(iter(lengths)) == 0:
        raise ValueError("tracking errors must share one nonempty grid")
    if any(values.ndim != 1 or not np.all(np.isfinite(values)) for values in normalized.values()):
        raise ValueError("tracking errors must be finite vectors")
    translations = {str(value) for value in translational_coordinates}
    unknown = translations.difference(normalized)
    if unknown:
        raise ValueError("translational tracking coordinates are missing")
    rotational = [name for name in normalized if name not in translations]
    if not rotational or not translations:
        raise ValueError("tracking errors require rotational and translational coordinates")

    rotational_matrix = np.column_stack([normalized[name] for name in rotational])
    translation_matrix = np.column_stack(
        [normalized[name] for name in sorted(translations)]
    )
    rotation_rms = {
        name: float(np.degrees(np.sqrt(np.mean(np.square(normalized[name])))))
        for name in rotational
    }
    translation_rms = {
        name: float(1000.0 * np.sqrt(np.mean(np.square(normalized[name]))))
        for name in sorted(translations)
    }
    return {
        "sample_count": int(next(iter(lengths))),
        "rotational_coordinate_count": len(rotational),
        "translational_coordinate_count": len(translations),
        "aggregate_rotational_rms_deg": float(
            np.degrees(np.sqrt(np.mean(np.square(rotational_matrix))))
        ),
        "aggregate_translational_rms_mm": float(
            1000.0 * np.sqrt(np.mean(np.square(translation_matrix)))
        ),
        "rotational_coordinate_rms_deg": rotation_rms,
        "translational_coordinate_rms_mm": translation_rms,
        "maximum_rotational_coordinate": max(
            rotation_rms, key=lambda name: rotation_rms[name]
        ),
        "maximum_rotational_coordinate_rms_deg": max(rotation_rms.values()),
        "maximum_translational_coordinate": max(
            translation_rms, key=lambda name: translation_rms[name]
        ),
        "maximum_translational_coordinate_rms_mm": max(translation_rms.values()),
    }


def waveform_associations(
    moments: Sequence[Sequence[float]],
    tracking: Mapping[str, Sequence[float]],
    *, minimum_absolute_correlation: float,
) -> Dict[str, Any]:
    """Report prespecified non-causal waveform associations."""
    moment = _finite_vectors(moments, "residual moments")
    threshold = float(minimum_absolute_correlation)
    if not 0 <= threshold <= 1:
        raise ValueError("correlation threshold must lie in [0, 1]")
    result: Dict[str, Any] = {}
    for axis, axis_name in enumerate(("x", "y", "z")):
        rows = []
        for name, raw in tracking.items():
            values = np.asarray(raw, dtype=float)
            if values.shape != (moment.shape[0],) or not np.all(np.isfinite(values)):
                raise ValueError("tracking association inputs must share the moment grid")
            if np.std(values) <= 1e-15 or np.std(moment[:, axis]) <= 1e-15:
                correlation = 0.0
            else:
                correlation = float(np.corrcoef(moment[:, axis], values)[0, 1])
            rows.append(
                {
                    "coordinate": str(name),
                    "correlation": correlation,
                    "absolute_correlation": abs(correlation),
                }
            )
        rows.sort(key=lambda row: (-row["absolute_correlation"], row["coordinate"]))
        result[axis_name] = {
            "maximum": rows[0],
            "prespecified_associations": [
                row
                for row in rows
                if row["absolute_correlation"] >= threshold
            ],
            "all_coordinates_ranked": rows,
        }
    return {
        "minimum_absolute_correlation": threshold,
        "interpretation": "association-only-not-causal-attribution",
        "axes": result,
    }


def classify_moment_localization(
    *,
    pass1: Mapping[str, Any],
    pass2: Mapping[str, Any],
    without_free_moment: Mapping[str, Any],
    vertical_free_mean_opposition_fraction: float,
    source_local_pass: bool,
    thresholds: Mapping[str, Any],
) -> Dict[str, Any]:
    """Classify localization while preserving the frozen mechanics gate."""
    limit = float(thresholds["residual_moment_fraction"])
    dominant_limit = float(thresholds["minimum_dominant_axis_energy_share"])
    bias_limit = float(thresholds["minimum_bias_energy_share"])
    opposition_limit = float(
        thresholds["minimum_vertical_free_mean_opposition_fraction"]
    )
    pass_change_limit = float(thresholds["maximum_pass_rms_relative_change"])
    pass1_rms = float(pass1["vector_rms_nm"])
    pass2_rms = float(pass2["vector_rms_nm"])
    pass_change = abs(pass2_rms - pass1_rms) / pass1_rms
    shares = [float(value) for value in pass2["axis_energy_share"]]
    dominant_axis = ("x", "y", "z")[int(np.argmax(shares))]
    dominant = max(shares) >= dominant_limit
    bias_dominant = float(pass2["mean_energy_share"]) >= bias_limit
    zero_mean_failed = float(pass2["zero_mean_fraction"]) > limit
    free_moment_insufficient = float(without_free_moment["fraction"]) > limit
    free_mean_opposed = (
        float(vertical_free_mean_opposition_fraction) >= opposition_limit
    )
    pass_stable = pass_change <= pass_change_limit
    mechanics_closed = float(pass2["fraction"]) <= limit

    if mechanics_closed:
        classification = "rra-moment-quality-closed"
    elif (
        dominant
        and bias_dominant
        and zero_mean_failed
        and free_moment_insufficient
        and free_mean_opposed
        and pass_stable
        and source_local_pass
    ):
        classification = "bias-dominated-multifactor-rra-moment-failure"
    elif not source_local_pass:
        classification = "target-window-external-source-equivalence-unresolved"
    else:
        classification = "rra-moment-failure-localized-without-single-sufficient-factor"
    return {
        "classification": classification,
        "mechanics_closed": bool(mechanics_closed),
        "dominant_axis": dominant_axis,
        "dominant_axis_energy_share": max(shares),
        "dominant_axis_identified": bool(dominant),
        "bias_dominant": bool(bias_dominant),
        "zero_mean_residual_still_fails": bool(zero_mean_failed),
        "free_moment_removal_still_fails": bool(free_moment_insufficient),
        "vertical_free_mean_opposition_confirmed": bool(free_mean_opposed),
        "pass1_to_pass2_relative_rms_change": pass_change,
        "pass_stable": bool(pass_stable),
        "target_window_source_equivalence_pass": bool(source_local_pass),
        "thresholds": dict(thresholds),
    }


def build_moment_localization_plan(
    protocol_path: Path,
    source_manifest_path: Path,
    data_root: Path,
    s3_result_path: Path,
    s3_work_dir: Path,
    s2_work_dir: Path,
) -> Dict[str, Any]:
    """Build a checksum-bound S4 plan from frozen S2/S3 artifacts."""
    protocol_path = protocol_path.resolve()
    source_manifest_path = source_manifest_path.resolve()
    s3_result_path = s3_result_path.resolve()
    s3_work_dir = s3_work_dir.resolve()
    s2_work_dir = s2_work_dir.resolve()
    protocol = _load_yaml(protocol_path)
    if str(protocol.get("schema_version")) != MOMENT_LOCALIZATION_SCHEMA_VERSION:
        raise ValueError("unsupported G6c-S4 protocol version")
    s3_result = json.loads(s3_result_path.read_text(encoding="utf-8"))
    if s3_result.get("scope") != "g6c-s3-state-reconstruction-audit":
        raise ValueError("G6c-S4 source result is not G6c-S3")
    if s3_result.get("diagnostic_complete") is not True:
        raise ValueError("G6c-S4 requires a complete G6c-S3 diagnostic")
    if ((s3_result.get("worker") or {}).get("diagnosis") or {}).get(
        "native_moment_pass"
    ) is not False:
        raise ValueError("G6c-S4 requires the retained native moment failure")

    design = protocol.get("design") or {}
    declared = design.get("source_work_artifacts") or {}
    pass1_dir = s2_work_dir / str(declared.get("pass1_directory", ""))
    pass2_dir = s2_work_dir / str(declared.get("pass2_directory", ""))
    paths = {
        "s3_timeseries": s3_work_dir / str(declared.get("s3_timeseries", "")),
        "pass1_actuation": _only_match(
            pass1_dir, "*Actuation_force.sto", "pass-1 actuation"
        ),
        "pass2_actuation": _only_match(
            pass2_dir, "*Actuation_force.sto", "pass-2 actuation"
        ),
        "pass1_position_error": _only_match(
            pass1_dir, "*pErr.sto", "pass-1 position error"
        ),
        "pass2_position_error": _only_match(
            pass2_dir, "*pErr.sto", "pass-2 position error"
        ),
    }
    for name, path in paths.items():
        if not path.is_file():
            raise ValueError("G6c-S4 work artifact is missing: {}".format(name))
        paths[name] = path.resolve()
    hashes = {name: sha256_file(path) for name, path in paths.items()}
    s3_worker = s3_result.get("worker") or {}
    if hashes["s3_timeseries"] != str(
        (s3_worker.get("artifacts") or {}).get("timeseries_sha256", "")
    ):
        raise ValueError("G6c-S4 S3 timeseries hash mismatch")
    if hashes["pass2_actuation"] != str(
        (s3_worker.get("input_sha256") or {}).get("rra_actuation", "")
    ):
        raise ValueError("G6c-S4 pass-2 actuation hash mismatch")

    source_audit = validate_public_source_bundle(source_manifest_path, data_root)
    if not source_audit["pass"]:
        raise ValueError("G6c-S4 public source bundle failed checksum verification")
    roles = design.get("public_source_roles") or {}
    for name in ("ground_reaction", "raw_c3d"):
        entry = source_audit["assets_by_role"].get(str(roles.get(name, "")))
        if not entry:
            raise ValueError("G6c-S4 public source role is missing: {}".format(name))
        path = Path(str(entry["absolute_path"])).resolve()
        paths[name] = path
        hashes[name] = sha256_file(path)
    if hashes["ground_reaction"] != str(
        (s3_worker.get("input_sha256") or {}).get("ground_reaction", "")
    ):
        raise ValueError("G6c-S4 public ground reaction differs from G6c-S3")

    thresholds = dict(protocol.get("thresholds") or {})
    inherited = s3_result.get("thresholds") or {}
    if float(thresholds.get("residual_moment_fraction", math.nan)) != float(
        inherited.get("residual_moment_fraction", math.nan)
    ):
        raise ValueError("G6c-S4 changed the frozen residual-moment threshold")
    source_result = s3_worker.get("native_rra_quality") or {}
    return {
        "schema_version": MOMENT_LOCALIZATION_SCHEMA_VERSION,
        "scope": "g6c-s4-moment-localization-plan",
        "protocol_sha256": sha256_file(protocol_path),
        "source_manifest_sha256": source_audit["manifest_sha256"],
        "s3_result_sha256": sha256_file(s3_result_path),
        "input_paths": {name: str(path) for name, path in paths.items()},
        "input_sha256": hashes,
        "comparison_window_s": [
            float(value) for value in design.get("comparison_window_s", [])
        ],
        "expected_sample_count": int(design.get("expected_sample_count", 0)),
        "vertical_axis": int(design.get("vertical_axis", 1)),
        "public_prefixes": [str(value) for value in design.get("public_prefixes", [])],
        "translational_coordinates": [
            str(value) for value in design.get("translational_coordinates", [])
        ],
        "external_source_audit": dict(design.get("external_source_audit") or {}),
        "source_s3_native_quality": dict(source_result),
        "thresholds": thresholds,
        "claim_boundary": str(protocol.get("claim_boundary", "")),
    }


def evaluate_moment_localization(
    plan: Mapping[str, Any], worker: Mapping[str, Any]
) -> Dict[str, Any]:
    """Evaluate diagnostic integrity independently from mechanics quality."""
    diagnosis = worker.get("diagnosis") or {}
    checks = {
        "worker_scope": worker.get("scope") == "g6c-s4-moment-localization-worker",
        "worker_input_hashes": worker.get("input_sha256") == plan.get("input_sha256"),
        "s3_result_hash": worker.get("s3_result_sha256")
        == plan.get("s3_result_sha256"),
        "sample_count": int(worker.get("sample_count", 0))
        == int(plan.get("expected_sample_count", 0)),
        "source_equivalence": (
            (worker.get("target_window_source_equivalence") or {}).get("pass")
        )
        is True,
        "pass_artifact_grids": worker.get("pass_artifact_grids_exact") is True,
        "s3_moment_reproduced": worker.get("s3_moment_reproduced") is True,
        "decomposition_identity": worker.get("decomposition_identity_pass") is True,
    }
    diagnostic_complete = all(checks.values())
    mechanics_closed = bool(
        diagnostic_complete and diagnosis.get("mechanics_closed") is True
    )
    return {
        "schema_version": MOMENT_LOCALIZATION_SCHEMA_VERSION,
        "scope": "g6c-s4-moment-localization-audit",
        "pass": mechanics_closed,
        "diagnostic_complete": diagnostic_complete,
        "mechanics_closed": mechanics_closed,
        "checks": checks,
        "thresholds": dict(plan.get("thresholds") or {}),
        "worker": dict(worker),
        "scientific_claim_boundary": str(plan.get("claim_boundary", "")),
        "next_gate": "g6d" if mechanics_closed else "blocked-at-g6c-s4",
    }


def write_moment_localization(result: Mapping[str, Any], output: Path) -> None:
    output = output.resolve()
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(
        json.dumps(result, indent=2, sort_keys=True) + "\n", encoding="utf-8"
    )
