"""G6c-S6c subject/activity-matched structural identifiability audit."""

from __future__ import annotations

import csv
import json
import math
import xml.etree.ElementTree as ET
from pathlib import Path
from typing import Any, Dict, Mapping, Sequence

import pandas as pd
import yaml

from harmo.dynamic_calibration import (
    sha256_file,
    validate_public_source_bundle,
)
from harmo.ingest.readers.sto import read_sto_file
from harmo.ingest.readers.trc import read_trc_file


STRUCTURAL_IDENTIFIABILITY_SCHEMA_VERSION = "1.0.0"


def _load_yaml(path: Path) -> Dict[str, Any]:
    value = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    if not isinstance(value, dict):
        raise ValueError("G6c-S6c YAML root must be a mapping")
    return value


def _load_json(path: Path) -> Dict[str, Any]:
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise ValueError("G6c-S6c JSON root must be a mapping")
    return value


def trc_marker_labels(path: Path) -> list[str]:
    """Return unique marker labels from a verified TRC."""
    _, data = read_trc_file(str(path))
    labels = {
        str(column)[:-2]
        for column in data.columns
        if str(column).endswith(("_X", "_Y", "_Z"))
    }
    return sorted(labels)


def c3d_marker_labels(path: Path) -> list[str]:
    """Return unique point labels from a C3D without interpreting trajectories."""
    try:
        import ezc3d
    except ImportError as exc:
        raise RuntimeError("G6c-S6c requires ezc3d") from exc
    c3d = ezc3d.c3d(str(path))
    labels = {
        str(value).strip()
        for value in c3d["parameters"]["POINT"]["LABELS"]["value"]
        if str(value).strip()
    }
    return sorted(labels)


def storage_columns(path: Path) -> list[str]:
    """Return normalized OpenSim storage columns."""
    _, data = read_sto_file(str(path))
    return sorted(str(value).strip().lower() for value in data.columns)


def storage_time_range(path: Path) -> tuple[float, float, int]:
    """Return finite ordered time coverage for an OpenSim storage file."""
    _, data = read_sto_file(str(path))
    if "time" not in data:
        raise ValueError("G6c-S6c storage input has no time column")
    time = pd.to_numeric(data["time"], errors="coerce")
    if time.isna().any() or len(time) < 2 or not time.is_monotonic_increasing:
        raise ValueError("G6c-S6c storage time is invalid")
    return float(time.iloc[0]), float(time.iloc[-1]), int(len(time))


def model_inventory(path: Path) -> Dict[str, Any]:
    """Read the body, coordinate, marker, and mass inventory from an OSIM XML."""
    root = ET.parse(path).getroot()
    bodies = []
    masses = {}
    for body in root.findall(".//BodySet/objects/Body"):
        name = str(body.attrib.get("name", "")).strip()
        if not name:
            continue
        mass = float(body.findtext("mass", "nan"))
        if not math.isfinite(mass) or mass < 0:
            raise ValueError("G6c-S6c model has an invalid body mass")
        bodies.append(name)
        masses[name] = mass
    coordinates = sorted(
        {
            str(value.attrib.get("name", "")).strip()
            for value in root.findall(".//Coordinate")
            if str(value.attrib.get("name", "")).strip()
        }
    )
    markers = sorted(
        {
            str(value.attrib.get("name", "")).strip()
            for value in root.findall(".//MarkerSet/objects/Marker")
            if str(value.attrib.get("name", "")).strip()
        }
    )
    return {
        "body_count": len(bodies),
        "bodies": sorted(bodies),
        "coordinate_count": len(coordinates),
        "coordinates": coordinates,
        "marker_count": len(markers),
        "markers": markers,
        "body_masses_kg": masses,
        "total_mass_kg": float(sum(masses.values())),
    }


def _match_alias_groups(
    observed: Sequence[str], groups: Mapping[str, Sequence[str]]
) -> Dict[str, Any]:
    normalized = {str(value).strip().upper() for value in observed}
    matches = {}
    for group, aliases in groups.items():
        matched = sorted(
            {
                str(alias)
                for alias in aliases
                if str(alias).strip().upper() in normalized
            }
        )
        matches[str(group)] = {
            "aliases": [str(value) for value in aliases],
            "matched": matched,
            "pass": bool(matched),
        }
    return matches


def assess_structural_identifiability(
    source: Mapping[str, Any], *, requirements: Mapping[str, Any]
) -> Dict[str, Any]:
    """Decide whether an observed paired full-body RRA is scientifically valid."""
    marker_groups = _match_alias_groups(
        source.get("trc_marker_labels") or [],
        requirements.get("upper_limb_marker_aliases") or {},
    )
    c3d_marker_groups = _match_alias_groups(
        source.get("c3d_marker_labels") or [],
        requirements.get("upper_limb_marker_aliases") or {},
    )
    body_groups = _match_alias_groups(
        (source.get("model") or {}).get("bodies") or [],
        requirements.get("articulated_body_aliases") or {},
    )
    coordinate_groups = _match_alias_groups(
        source.get("ik_columns") or [],
        requirements.get("upper_limb_coordinate_aliases") or {},
    )
    trc_landmark_count = sum(
        int(value["pass"]) for value in marker_groups.values()
    )
    c3d_landmark_count = sum(
        int(value["pass"]) for value in c3d_marker_groups.values()
    )
    model_body_count = sum(int(value["pass"]) for value in body_groups.values())
    ik_coordinate_count = sum(
        int(value["pass"]) for value in coordinate_groups.values()
    )
    expected_marker_groups = len(marker_groups)
    expected_body_groups = len(body_groups)
    expected_coordinate_groups = len(coordinate_groups)
    required_roles = [
        str(value)
        for value in requirements.get("required_paired_model_roles", [])
    ]
    available_roles = {
        str(value) for value in source.get("available_source_roles") or []
    }
    paired_roles_present = all(role in available_roles for role in required_roles)
    target_window = [
        float(value) for value in requirements.get("target_window_s", [])
    ]
    ground_range = source.get("ground_reaction_time_range_s") or []
    ground_covers_target = bool(
        len(target_window) == 2
        and len(ground_range) == 2
        and ground_range[0] <= target_window[0]
        and ground_range[1] >= target_window[1]
    )
    checks = {
        "source_bundle_verified": source.get("source_bundle_verified") is True,
        "same_subject_and_activity_sources": (
            source.get("same_subject_and_activity_sources") is True
        ),
        "trc_c3d_marker_inventory_equal": (
            source.get("trc_marker_labels") == source.get("c3d_marker_labels")
        ),
        "ground_reaction_covers_target": ground_covers_target,
        "upper_limb_landmarks_observed_in_trc": (
            trc_landmark_count == expected_marker_groups
            and trc_landmark_count
            >= int(requirements.get("minimum_observed_landmark_groups", 0))
        ),
        "upper_limb_landmarks_observed_in_c3d": (
            c3d_landmark_count == expected_marker_groups
            and c3d_landmark_count
            >= int(requirements.get("minimum_observed_landmark_groups", 0))
        ),
        "articulated_upper_limb_bodies_in_model": (
            model_body_count == expected_body_groups
            and model_body_count
            >= int(requirements.get("minimum_articulated_body_groups", 0))
        ),
        "observed_upper_limb_coordinates_in_ik": (
            ik_coordinate_count == expected_coordinate_groups
            and ik_coordinate_count
            >= int(requirements.get("minimum_observed_coordinate_groups", 0))
        ),
        "paired_model_roles_present": paired_roles_present,
        "subject_specific_upper_limb_scale_lineage": (
            source.get("subject_specific_upper_limb_scale_lineage") is True
        ),
        "paired_model_lower_body_equivalence": (
            source.get("paired_model_lower_body_equivalence") is True
        ),
        "paired_model_mass_com_inertia_equivalence": (
            source.get("paired_model_mass_com_inertia_equivalence") is True
        ),
    }
    observational_checks = (
        "upper_limb_landmarks_observed_in_trc",
        "upper_limb_landmarks_observed_in_c3d",
        "observed_upper_limb_coordinates_in_ik",
    )
    model_checks = (
        "articulated_upper_limb_bodies_in_model",
        "paired_model_roles_present",
        "subject_specific_upper_limb_scale_lineage",
        "paired_model_lower_body_equivalence",
        "paired_model_mass_com_inertia_equivalence",
    )
    source_checks = (
        "source_bundle_verified",
        "same_subject_and_activity_sources",
        "trc_c3d_marker_inventory_equal",
        "ground_reaction_covers_target",
    )
    observational_identifiability = all(
        checks[name] for name in observational_checks
    )
    paired_model_ready = all(checks[name] for name in model_checks)
    source_ready = all(checks[name] for name in source_checks)
    authorized = bool(
        source_ready and observational_identifiability and paired_model_ready
    )
    rows = []
    for name, passed in checks.items():
        rows.append(
            {
                "requirement": name,
                "pass": bool(passed),
                "category": (
                    "source"
                    if name in source_checks
                    else "observation"
                    if name in observational_checks
                    else "model-pair"
                ),
            }
        )
    return {
        "checks": checks,
        "source_ready": source_ready,
        "observational_identifiability": observational_identifiability,
        "paired_model_ready": paired_model_ready,
        "full_body_rra_authorized": authorized,
        "classification": (
            "subject-activity-matched-full-body-rra-identifiable"
            if authorized
            else "subject-activity-matched-upper-limb-effect-unidentifiable"
        ),
        "counts": {
            "trc_marker_count": len(source.get("trc_marker_labels") or []),
            "c3d_marker_count": len(source.get("c3d_marker_labels") or []),
            "trc_upper_limb_landmark_groups_observed": trc_landmark_count,
            "c3d_upper_limb_landmark_groups_observed": c3d_landmark_count,
            "required_upper_limb_landmark_groups": expected_marker_groups,
            "model_body_count": int(
                (source.get("model") or {}).get("body_count", 0)
            ),
            "articulated_upper_limb_body_groups_present": model_body_count,
            "required_articulated_upper_limb_body_groups": expected_body_groups,
            "ik_coordinate_count": len(source.get("ik_columns") or []),
            "upper_limb_coordinate_groups_observed": ik_coordinate_count,
            "required_upper_limb_coordinate_groups": expected_coordinate_groups,
        },
        "marker_group_evidence": marker_groups,
        "c3d_marker_group_evidence": c3d_marker_groups,
        "body_group_evidence": body_groups,
        "coordinate_group_evidence": coordinate_groups,
        "requirement_rows": rows,
        "causal_structural_attribution": False,
        "mechanics_closed": False,
    }


def build_structural_identifiability_plan(
    protocol_path: Path,
    source_manifest_path: Path,
    data_root: Path,
    s2_result_path: Path,
    s6_result_path: Path,
    s6b_result_path: Path,
) -> Dict[str, Any]:
    """Build a checksum-bound G6c-S6c observability plan."""
    protocol_path = protocol_path.resolve()
    source_manifest_path = source_manifest_path.resolve()
    data_root = data_root.resolve()
    s2_result_path = s2_result_path.resolve()
    s6_result_path = s6_result_path.resolve()
    s6b_result_path = s6b_result_path.resolve()
    protocol = _load_yaml(protocol_path)
    if (
        str(protocol.get("schema_version"))
        != STRUCTURAL_IDENTIFIABILITY_SCHEMA_VERSION
    ):
        raise ValueError("unsupported G6c-S6c protocol version")
    s2 = _load_json(s2_result_path)
    s6 = _load_json(s6_result_path)
    s6b = _load_json(s6b_result_path)
    if (
        s2.get("scope") != "g6c-s2-mechanics-localization-audit"
        or s2.get("diagnostic_complete") is not True
        or s2.get("pass") is not False
    ):
        raise ValueError("G6c-S6c requires the retained complete S2 result")
    if (
        s6.get("scope") != "g6c-s6-structural-adequacy-audit"
        or s6.get("diagnostic_complete") is not True
        or s6.get("pass") is not False
    ):
        raise ValueError("G6c-S6c requires the retained complete S6 result")
    if (
        s6b.get("scope") != "g6c-s6b-structural-contrast-audit"
        or s6b.get("diagnostic_complete") is not True
        or s6b.get("pass") is not False
        or s6b.get("material_effect_detected") is not True
    ):
        raise ValueError("G6c-S6c requires the retained complete S6b result")
    source_audit = validate_public_source_bundle(
        source_manifest_path, data_root
    )
    if not source_audit["pass"]:
        raise ValueError("G6c-S6c public source bundle failed verification")
    design = protocol.get("design") or {}
    source_roles = design.get("source_roles") or {}
    required_role_keys = (
        "markers",
        "raw_c3d",
        "ground_reaction",
        "model",
        "public_ik",
    )
    available = source_audit["assets_by_role"]
    paths = {
        "s2_result": s2_result_path,
        "s6_result": s6_result_path,
        "s6b_result": s6b_result_path,
    }
    for key in required_role_keys:
        role = str(source_roles.get(key, "")).strip()
        if not role or role not in available:
            raise ValueError(
                "G6c-S6c source role is unavailable: {}".format(key)
            )
        paths[key] = Path(str(available[role]["absolute_path"])).resolve()
    observed_source_hashes = {
        key: sha256_file(paths[key])
        for key in ("model", "markers", "raw_c3d", "ground_reaction")
    }
    s2_source_hashes = (s2.get("worker") or {}).get("input_sha256") or {}
    if any(
        observed_source_hashes[key] != str(s2_source_hashes.get(key, ""))
        for key in observed_source_hashes
    ):
        raise ValueError("G6c-S6c source assets differ from frozen S2")
    s6_source_hashes = (s6.get("worker") or {}).get("input_sha256") or {}
    if (
        observed_source_hashes["ground_reaction"]
        != str(s6_source_hashes.get("ground_reaction", ""))
        or sha256_file(s2_result_path)
        != str(s6_source_hashes.get("s2_result", ""))
    ):
        raise ValueError("G6c-S6c S2-to-S6 source chain is broken")
    s6b_source_hashes = (s6b.get("worker") or {}).get("input_sha256") or {}
    if sha256_file(s6_result_path) != str(
        s6b_source_hashes.get("s6_result", "")
    ):
        raise ValueError("G6c-S6c S6-to-S6b source chain is broken")
    declared_pair_roles = [
        str(value)
        for value in (design.get("requirements") or {}).get(
            "required_paired_model_roles", []
        )
    ]
    return {
        "schema_version": STRUCTURAL_IDENTIFIABILITY_SCHEMA_VERSION,
        "scope": "g6c-s6c-structural-identifiability-plan",
        "protocol_sha256": sha256_file(protocol_path),
        "source_manifest_sha256": source_audit["manifest_sha256"],
        "source_record_doi": source_audit.get("source_record_doi"),
        "prior_result_sha256": {
            "s2": sha256_file(s2_result_path),
            "s6": sha256_file(s6_result_path),
            "s6b": sha256_file(s6b_result_path),
        },
        "input_paths": {name: str(path) for name, path in paths.items()},
        "input_sha256": {name: sha256_file(path) for name, path in paths.items()},
        "available_source_roles": sorted(available),
        "missing_paired_model_roles": sorted(
            set(declared_pair_roles) - set(available)
        ),
        "same_subject_activity_source_chain_verified": True,
        "subject_id": str(design.get("subject_id", "")),
        "activity": str(design.get("activity", "")),
        "requirements": dict(design.get("requirements") or {}),
        "attestations": dict(design.get("attestations") or {}),
        "prohibited_substitutions": [
            str(value) for value in design.get("prohibited_substitutions", [])
        ],
        "claim_boundary": str(protocol.get("claim_boundary", "")),
    }


def inspect_structural_identifiability_plan(
    plan: Mapping[str, Any],
) -> tuple[Dict[str, Any], list[Dict[str, Any]]]:
    """Inspect only the files declared in a G6c-S6c worker plan."""
    paths = {
        name: Path(str(value)).resolve()
        for name, value in (plan.get("input_paths") or {}).items()
    }
    observed_hashes = {name: sha256_file(path) for name, path in paths.items()}
    if observed_hashes != plan.get("input_sha256"):
        raise ValueError("G6c-S6c worker input checksum mismatch")
    trc_labels = trc_marker_labels(paths["markers"])
    c3d_labels = c3d_marker_labels(paths["raw_c3d"])
    ik_columns = storage_columns(paths["public_ik"])
    ground_start, ground_end, ground_rows = storage_time_range(
        paths["ground_reaction"]
    )
    model = model_inventory(paths["model"])
    attestations = plan.get("attestations") or {}
    source = {
        "source_bundle_verified": True,
        "same_subject_and_activity_sources": bool(
            plan.get("same_subject_activity_source_chain_verified")
        ),
        "subject_specific_upper_limb_scale_lineage": bool(
            attestations.get("subject_specific_upper_limb_scale_lineage")
        ),
        "paired_model_lower_body_equivalence": bool(
            attestations.get("paired_model_lower_body_equivalence")
        ),
        "paired_model_mass_com_inertia_equivalence": bool(
            attestations.get("paired_model_mass_com_inertia_equivalence")
        ),
        "available_source_roles": list(plan.get("available_source_roles") or []),
        "trc_marker_labels": trc_labels,
        "c3d_marker_labels": c3d_labels,
        "ik_columns": ik_columns,
        "ground_reaction_time_range_s": [ground_start, ground_end],
        "ground_reaction_row_count": ground_rows,
        "model": model,
    }
    diagnosis = assess_structural_identifiability(
        source, requirements=plan.get("requirements") or {}
    )
    return {
        "source": source,
        "diagnosis": diagnosis,
        "input_sha256": observed_hashes,
    }, list(diagnosis["requirement_rows"])


def evaluate_structural_identifiability(
    plan: Mapping[str, Any], worker: Mapping[str, Any]
) -> Dict[str, Any]:
    diagnosis = worker.get("diagnosis") or {}
    checks = {
        "worker_scope": worker.get("scope")
        == "g6c-s6c-structural-identifiability-worker",
        "worker_inputs": worker.get("input_sha256") == plan.get("input_sha256"),
        "prior_result": worker.get("prior_result_sha256")
        == plan.get("prior_result_sha256"),
        "analysis_complete": worker.get("analysis_complete") is True,
        "artifacts_complete": worker.get("artifacts_complete") is True,
    }
    diagnostic_complete = all(checks.values())
    authorized = bool(
        diagnostic_complete
        and diagnosis.get("full_body_rra_authorized") is True
    )
    return {
        "schema_version": STRUCTURAL_IDENTIFIABILITY_SCHEMA_VERSION,
        "scope": "g6c-s6c-structural-identifiability-audit",
        "pass": authorized,
        "diagnostic_complete": diagnostic_complete,
        "full_body_rra_authorized": authorized,
        "causal_structural_attribution": False,
        "mechanics_closed": False,
        "checks": checks,
        "worker": dict(worker),
        "scientific_claim_boundary": str(plan.get("claim_boundary", "")),
        "prohibited_substitutions": list(
            plan.get("prohibited_substitutions") or []
        ),
        "causal_next_gate": (
            "g6c-s6c-r-paired-full-body-rra"
            if authorized
            else "blocked-requires-upper-limb-observed-subject-activity-data"
        ),
        "diagnostic_next_gate": (
            "none"
            if authorized
            else "g6c-s6d-preregistered-bounded-arm-kinematics-sensitivity"
        ),
    }


def write_structural_identifiability(
    result: Mapping[str, Any], output: Path
) -> None:
    output = output.resolve()
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(
        json.dumps(result, indent=2, sort_keys=True) + "\n", encoding="utf-8"
    )


def write_requirement_rows(rows: Sequence[Mapping[str, Any]], path: Path) -> None:
    path = path.resolve()
    path.parent.mkdir(parents=True, exist_ok=True)
    fieldnames = ["requirement", "category", "pass"]
    with path.open("w", encoding="utf-8", newline="") as stream:
        writer = csv.DictWriter(stream, fieldnames=fieldnames)
        writer.writeheader()
        for row in rows:
            writer.writerow({name: row.get(name) for name in fieldnames})
