"""Leakage-resistant model synthesis and hidden-reference evaluation for G6b-2."""

from __future__ import annotations

import json
import math
import xml.etree.ElementTree as ET
from collections import defaultdict
from pathlib import Path
from typing import Any, Dict, List, Mapping, Sequence

import numpy as np
import pandas as pd
import yaml

from harmo.dynamic_calibration import (
    _pair_distances,
    _read_verified_trc,
    _robust_estimate,
    sha256_file,
    validate_public_source_bundle,
)


DYNAMIC_MODEL_SCHEMA_VERSION = "1.0.0"


def _load_yaml(path: Path) -> Dict[str, Any]:
    value = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    if not isinstance(value, dict):
        raise ValueError("G6b-2 YAML root must be a mapping")
    return value


def _finite_positive(value: Any, label: str) -> float:
    numeric = float(value)
    if not math.isfinite(numeric) or numeric <= 0:
        raise ValueError("{} must be finite and positive".format(label))
    return numeric


def load_model_markers(path: Path) -> Dict[str, Dict[str, Any]]:
    """Load local marker coordinates and body names from an OpenSim XML model."""
    try:
        root = ET.parse(str(path)).getroot()
    except (OSError, ET.ParseError) as exc:
        raise ValueError("OpenSim model XML is unreadable: {}".format(path)) from exc
    markers: Dict[str, Dict[str, Any]] = {}
    for element in root.iter("Marker"):
        name = str(element.get("name", "")).strip()
        parent = element.findtext("socket_parent_frame") or element.findtext("body")
        location = element.findtext("location")
        if not name or not parent or not location:
            continue
        values = [float(value) for value in location.split()]
        if len(values) != 3 or not np.all(np.isfinite(values)):
            raise ValueError("invalid marker location for {}".format(name))
        body = str(parent).strip().replace("\\", "/").split("/")[-1]
        if name in markers:
            raise ValueError("duplicate OpenSim model marker {}".format(name))
        markers[name] = {"body": body, "location_m": values}
    if not markers:
        raise ValueError("OpenSim model has no readable marker set")
    return markers


def model_total_mass(path: Path) -> float:
    try:
        root = ET.parse(str(path)).getroot()
    except (OSError, ET.ParseError) as exc:
        raise ValueError("OpenSim model XML is unreadable: {}".format(path)) from exc
    masses = []
    for body in root.iter("Body"):
        value = body.findtext("mass")
        if value is not None:
            masses.append(float(value))
    if not masses or not np.all(np.isfinite(masses)):
        raise ValueError("OpenSim model has no finite body masses")
    return float(sum(masses))


def _model_pair_distance(
    markers: Mapping[str, Mapping[str, Any]],
    marker_a: str,
    marker_b: str,
    expected_body: str,
) -> float:
    if marker_a not in markers or marker_b not in markers:
        raise ValueError("model marker pair is incomplete: {}-{}".format(marker_a, marker_b))
    first = markers[marker_a]
    second = markers[marker_b]
    if first["body"] != second["body"] or first["body"] != expected_body:
        raise ValueError(
            "marker pair {}-{} is not fixed to declared body {}".format(
                marker_a, marker_b, expected_body
            )
        )
    distance = float(
        np.linalg.norm(
            np.asarray(first["location_m"], dtype=float)
            - np.asarray(second["location_m"], dtype=float)
        )
    )
    return _finite_positive(distance, "template marker distance")


def build_dynamic_model_plan(
    protocol_path: Path, manifest_path: Path, data_root: Path
) -> Dict[str, Any]:
    """Build a model-scaling plan without opening the target reference model."""
    protocol_path = protocol_path.resolve()
    protocol = _load_yaml(protocol_path)
    if str(protocol.get("schema_version")) != "1.0.0":
        raise ValueError("unsupported G6b-2 protocol version")
    audit = validate_public_source_bundle(manifest_path, data_root)
    if not audit["pass"]:
        raise ValueError("G6b-2 public source bundle failed checksum verification")
    roles = audit["assets_by_role"]
    design = protocol.get("design") or {}
    template_role = str(design.get("template_model_role", ""))
    reference_role = str(design.get("hidden_reference_model_role", ""))
    metadata_role = str(design.get("anthropometry_source_role", ""))
    dynamic_roles = [str(value) for value in design.get("dynamic_calibration_roles", [])]
    if len(dynamic_roles) < 2 or len(set(dynamic_roles)) != len(dynamic_roles):
        raise ValueError("G6b-2 requires at least two unique dynamic trials")
    estimation_roles = [template_role, metadata_role] + dynamic_roles
    if reference_role in estimation_roles:
        raise ValueError("hidden target model cannot enter G6b-2 estimation")
    declared_estimation = [str(value) for value in design.get("estimation_input_roles", [])]
    if declared_estimation != estimation_roles:
        raise ValueError("G6b-2 estimation_input_roles differ from executable roles")
    missing = sorted(set(estimation_roles + [reference_role]) - set(roles))
    if missing:
        raise ValueError("G6b-2 source manifest lacks roles: {}".format(missing))
    template_subject = str(design.get("template_subject_id", ""))
    target_subject = str(design.get("target_subject_id", ""))
    if not template_subject or not target_subject or template_subject == target_subject:
        raise ValueError("G6b-2 template and target subjects must differ")

    template_asset = roles[template_role]
    template_path = Path(str(template_asset["absolute_path"]))
    template_markers = load_model_markers(template_path)
    dynamic_data = {role: _read_verified_trc(roles[role]) for role in dynamic_roles}
    outlier_mad = float((protocol.get("estimation") or {}).get("outlier_mad", 4.5))
    measurements = protocol.get("measurements")
    if not isinstance(measurements, list) or not measurements:
        raise ValueError("G6b-2 protocol has no measurements")

    measurement_rows: List[Dict[str, Any]] = []
    body_ratios: Dict[str, List[float]] = defaultdict(list)
    seen_ids = set()
    for raw in measurements:
        if not isinstance(raw, Mapping):
            raise ValueError("G6b-2 measurement must be a mapping")
        measurement_id = str(raw.get("id", "")).strip()
        body = str(raw.get("body", "")).strip()
        marker_a = str(raw.get("marker_a", "")).strip()
        marker_b = str(raw.get("marker_b", "")).strip()
        if not measurement_id or measurement_id in seen_ids:
            raise ValueError("G6b-2 measurement ids must be nonempty and unique")
        seen_ids.add(measurement_id)
        template_distance = _model_pair_distance(
            template_markers, marker_a, marker_b, body
        )
        trial_rows = []
        for role in dynamic_roles:
            estimate = _robust_estimate(
                _pair_distances(dynamic_data[role], marker_a, marker_b), outlier_mad
            )
            trial_rows.append({"role": role, **estimate})
        dynamic_distance = float(
            np.median([row["estimate_m"] for row in trial_rows])
        )
        ratio = _finite_positive(
            dynamic_distance / template_distance, "dynamic scale factor"
        )
        body_ratios[body].append(ratio)
        measurement_rows.append(
            {
                "measurement_id": measurement_id,
                "body": body,
                "marker_a": marker_a,
                "marker_b": marker_b,
                "template_distance_m": template_distance,
                "dynamic_distance_m": dynamic_distance,
                "scale_factor": ratio,
                "dynamic_trials": trial_rows,
            }
        )

    direct_factors = {
        body: float(np.median(values)) for body, values in sorted(body_ratios.items())
    }
    inheritance = {
        str(body): str(source)
        for body, source in ((protocol.get("model_scaling") or {}).get("inheritance") or {}).items()
    }
    required_bodies = [
        str(value)
        for value in (protocol.get("model_scaling") or {}).get("required_bodies", [])
    ]
    if not required_bodies or len(set(required_bodies)) != len(required_bodies):
        raise ValueError("G6b-2 required model bodies must be unique and nonempty")
    body_factors: Dict[str, float] = {}
    for body in required_bodies:
        source = body if body in direct_factors else inheritance.get(body)
        if source not in direct_factors:
            raise ValueError("G6b-2 body {} has no direct or inherited scale".format(body))
        body_factors[body] = direct_factors[source]

    target_mass = _finite_positive(design.get("target_mass_kg"), "target mass")
    target_height = _finite_positive(design.get("target_height_m"), "target height")
    return {
        "schema_version": DYNAMIC_MODEL_SCHEMA_VERSION,
        "scope": "g6b-dynamic-derived-model-plan",
        "protocol_sha256": sha256_file(protocol_path),
        "source_manifest_sha256": audit["manifest_sha256"],
        "template_subject_id": template_subject,
        "target_subject_id": target_subject,
        "template_model_path": str(template_path),
        "template_model_sha256": template_asset["observed_sha256"],
        "hidden_reference_role": reference_role,
        "hidden_reference_sha256": roles[reference_role]["observed_sha256"],
        "estimation_input_roles": estimation_roles,
        "estimation_input_sha256": {
            role: roles[role]["observed_sha256"] for role in estimation_roles
        },
        "target_mass_kg": target_mass,
        "target_height_m": target_height,
        "preserve_mass_distribution": False,
        "direct_body_scale_factors": direct_factors,
        "body_scale_factors": body_factors,
        "measurements": measurement_rows,
    }


def _group_measurements(rows: Sequence[Mapping[str, Any]]) -> Dict[str, List[Mapping[str, Any]]]:
    grouped: Dict[str, List[Mapping[str, Any]]] = defaultdict(list)
    for row in rows:
        grouped[str(row["body"])].append(row)
    return grouped


def evaluate_dynamic_model(
    plan: Mapping[str, Any],
    generated_model_path: Path,
    reference_model_asset: Mapping[str, Any],
    thresholds: Mapping[str, Any],
) -> Dict[str, Any]:
    """Reveal the target static-derived model only after dynamic synthesis."""
    generated_model_path = generated_model_path.resolve()
    if not generated_model_path.is_file():
        raise ValueError("G6b-2 generated model is missing")
    if reference_model_asset.get("pass") is not True:
        raise ValueError("G6b-2 target reference model is not checksum verified")
    if str(reference_model_asset.get("observed_sha256")) != str(
        plan.get("hidden_reference_sha256")
    ):
        raise ValueError("G6b-2 hidden reference checksum changed after planning")
    reference_path = Path(str(reference_model_asset["absolute_path"]))
    template_markers = load_model_markers(Path(str(plan["template_model_path"])))
    generated_markers = load_model_markers(generated_model_path)
    reference_markers = load_model_markers(reference_path)

    measurement_rows = []
    for row in plan.get("measurements", []):
        body = str(row["body"])
        marker_a = str(row["marker_a"])
        marker_b = str(row["marker_b"])
        template_distance = _model_pair_distance(
            template_markers, marker_a, marker_b, body
        )
        generated_distance = _model_pair_distance(
            generated_markers, marker_a, marker_b, body
        )
        reference_distance = _model_pair_distance(
            reference_markers, marker_a, marker_b, body
        )
        measurement_factor = float(row["scale_factor"])
        planned_body_factor = float(plan["direct_body_scale_factors"][body])
        generated_factor = generated_distance / template_distance
        reference_factor = reference_distance / template_distance
        measurement_rows.append(
            {
                "measurement_id": row["measurement_id"],
                "body": body,
                "dynamic_measurement_factor": measurement_factor,
                "planned_body_factor": planned_body_factor,
                "generated_model_factor": generated_factor,
                "static_reference_factor": reference_factor,
                "generation_relative_error": abs(
                    generated_factor - planned_body_factor
                )
                / planned_body_factor,
                "reference_relative_error": abs(measurement_factor - reference_factor)
                / reference_factor,
            }
        )

    body_rows = []
    for body, rows in sorted(_group_measurements(measurement_rows).items()):
        planned = float(np.median([float(row["planned_body_factor"]) for row in rows]))
        generated = float(
            np.median([float(row["generated_model_factor"]) for row in rows])
        )
        reference = float(
            np.median([float(row["static_reference_factor"]) for row in rows])
        )
        body_rows.append(
            {
                "body": body,
                "measurement_count": len(rows),
                "planned_dynamic_factor": planned,
                "generated_model_factor": generated,
                "static_reference_factor": reference,
                "generation_absolute_relative_error": abs(generated - planned) / planned,
                "static_reference_absolute_relative_error": abs(planned - reference)
                / reference,
            }
        )

    generated_mass = model_total_mass(generated_model_path)
    reference_mass = model_total_mass(reference_path)
    target_mass = float(plan["target_mass_kg"])
    mass_relative_error = abs(generated_mass - target_mass) / target_mass
    body_errors = [row["static_reference_absolute_relative_error"] for row in body_rows]
    generation_errors = [row["generation_absolute_relative_error"] for row in body_rows]
    required_direct = len(plan.get("direct_body_scale_factors", {}))
    coverage = len(body_rows) / required_direct if required_direct else 0.0
    summary = {
        "direct_body_count_frozen": required_direct,
        "direct_body_count_assessed": len(body_rows),
        "direct_body_coverage": coverage,
        "median_static_reference_absolute_relative_error": (
            float(np.median(body_errors)) if body_errors else None
        ),
        "p95_static_reference_absolute_relative_error": (
            float(np.percentile(body_errors, 95)) if body_errors else None
        ),
        "max_generation_absolute_relative_error": (
            max(generation_errors) if generation_errors else None
        ),
        "target_mass_kg": target_mass,
        "generated_mass_kg": generated_mass,
        "static_reference_mass_kg": reference_mass,
        "generated_mass_relative_error": mass_relative_error,
    }
    coverage_limit = float(thresholds.get("direct_body_coverage", 1.0))
    median_limit = float(thresholds.get("median_body_scale_error", 0.03))
    p95_limit = float(thresholds.get("p95_body_scale_error", 0.08))
    generation_limit = float(thresholds.get("model_generation_error", 1e-6))
    mass_limit = float(thresholds.get("mass_relative_error", 1e-6))
    checks = {
        "target_reference_hidden_during_estimation": plan["hidden_reference_role"]
        not in plan["estimation_input_roles"],
        "template_and_target_subjects_differ": plan["template_subject_id"]
        != plan["target_subject_id"],
        "direct_body_coverage": coverage >= coverage_limit,
        "median_body_scale_error": bool(
            body_errors and summary["median_static_reference_absolute_relative_error"] <= median_limit
        ),
        "p95_body_scale_error": bool(
            body_errors and summary["p95_static_reference_absolute_relative_error"] <= p95_limit
        ),
        "model_generation_error": bool(
            generation_errors and max(generation_errors) <= generation_limit
        ),
        "mass_relative_error": mass_relative_error <= mass_limit,
    }
    return {
        "schema_version": DYNAMIC_MODEL_SCHEMA_VERSION,
        "scope": "g6b-dynamic-derived-model-pilot",
        "scientific_claim_boundary": (
            "cross-subject-template isotropic model scaling only; marker placement, "
            "IK, ID, RRA, muscle force, and clinical validity remain unassessed"
        ),
        "protocol_sha256": plan["protocol_sha256"],
        "source_manifest_sha256": plan["source_manifest_sha256"],
        "template_subject_id": plan["template_subject_id"],
        "target_subject_id": plan["target_subject_id"],
        "generated_model_sha256": sha256_file(generated_model_path),
        "hidden_reference_model_sha256": reference_model_asset["observed_sha256"],
        "estimation_input_roles": plan["estimation_input_roles"],
        "direct_body_scale_factors": plan["direct_body_scale_factors"],
        "body_scale_factors": plan["body_scale_factors"],
        "thresholds": {
            "direct_body_coverage": coverage_limit,
            "median_body_scale_error": median_limit,
            "p95_body_scale_error": p95_limit,
            "model_generation_error": generation_limit,
            "mass_relative_error": mass_limit,
        },
        "summary": summary,
        "checks": checks,
        "pass": all(checks.values()),
        "bodies": body_rows,
        "measurements": measurement_rows,
    }


def write_dynamic_model_results(result: Mapping[str, Any], output: Path) -> None:
    output = output.resolve()
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(
        json.dumps(result, indent=2, sort_keys=True) + "\n", encoding="utf-8"
    )
    pd.DataFrame(result.get("bodies", [])).to_csv(
        output.with_suffix(".csv"), index=False
    )
