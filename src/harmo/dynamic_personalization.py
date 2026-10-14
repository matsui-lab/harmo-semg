"""Leakage-resistant dynamic-only personalization for G6d and G6e.

The target subject's static trial, static-derived model, and 4.00 m/s trial are
cryptographically excluded from fitting.  A sealed candidate can be opened by
a separate hidden evaluator only after model and lineage hashes are frozen.
"""

from __future__ import annotations

import hashlib
import json
import math
import os
import re
import shutil
import subprocess
import xml.etree.ElementTree as ET
from pathlib import Path
from typing import Any, Dict, Iterable, List, Mapping, Optional, Sequence, Tuple

import numpy as np
import yaml

from harmo.dynamic_calibration import sha256_file
from harmo.dynamic_model import load_model_markers
from harmo.mechanics_audit import canonical_sha256


DYNAMIC_PERSONALIZATION_SCHEMA_VERSION = "1.0.0"
BLIND_SUBJECTS = ("05", "11", "18", "19")
ALL_BENCHMARK_SUBJECTS = tuple("{:02d}".format(index) for index in range(1, 20))
DEVELOPMENT_SUBJECTS = tuple(
    subject for subject in ALL_BENCHMARK_SUBJECTS if subject not in BLIND_SUBJECTS
)
FIT_SPEEDS_M_S = (2.78, 3.00, 3.33)
HELDOUT_SPEED_M_S = 4.00
FORBIDDEN_TARGET_ROLES = {
    "static_markers",
    "static_model",
    "static_derived_model",
    "heldout_markers",
    "heldout_ground_reaction",
    "heldout_ik",
    "heldout_id",
    "manual_scaled_model",
    "study31_t2_markers",
    "study31_t2_ground_reaction",
    "study31_t3_markers",
    "study31_t3_ground_reaction",
}


def _load_yaml(path: Path) -> Dict[str, Any]:
    value = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    if not isinstance(value, dict):
        raise ValueError("G6d/G6e YAML root must be a mapping")
    return value


def _normalize_subject(value: Any) -> str:
    text = str(value).strip()
    match = re.search(r"(\d+)$", text)
    if not match:
        raise ValueError("subject id must end in digits: {}".format(value))
    return "{:02d}".format(int(match.group(1)))


def _safe_inventory_path(root: Path, raw: str) -> Path:
    relative = Path(str(raw))
    if relative.is_absolute() or ".." in relative.parts:
        raise ValueError("inventory paths must be relative and cannot contain '..'")
    resolved = (root / relative).resolve()
    try:
        resolved.relative_to(root.resolve())
    except ValueError as exc:
        raise ValueError("inventory path escapes data root") from exc
    return resolved


def validate_personalization_inventory(
    inventory_path: Path, data_root: Path
) -> Dict[str, Any]:
    """Verify every G6d/G6e inventory asset by byte count and SHA-256."""
    inventory_path = inventory_path.resolve()
    data_root = data_root.resolve()
    inventory = _load_yaml(inventory_path)
    if str(inventory.get("schema_version")) != DYNAMIC_PERSONALIZATION_SCHEMA_VERSION:
        raise ValueError("unsupported personalization inventory version")
    raw_subjects = inventory.get("subjects") or []
    subjects: Dict[str, Dict[str, Any]] = {}
    for raw in raw_subjects:
        if not isinstance(raw, Mapping):
            raise ValueError("subject inventory row must be a mapping")
        subject = _normalize_subject(raw.get("subject_id"))
        if subject in subjects:
            raise ValueError("duplicate subject metadata: {}".format(subject))
        height = float(raw.get("height_m"))
        mass = float(raw.get("mass_kg"))
        foot = float(raw.get("foot_length_m"))
        if not all(math.isfinite(value) and value > 0 for value in (height, mass, foot)):
            raise ValueError("subject anthropometry must be finite and positive")
        sex = str(raw.get("sex", "unknown")).strip().lower()
        subjects[subject] = {
            "subject_id": subject,
            "height_m": height,
            "mass_kg": mass,
            "sex": sex,
            "foot_length_m": foot,
        }
    if not subjects:
        raise ValueError("personalization inventory has no subject metadata")

    assets = []
    by_id: Dict[str, Dict[str, Any]] = {}
    for raw in inventory.get("assets") or []:
        if not isinstance(raw, Mapping):
            raise ValueError("asset inventory row must be a mapping")
        asset_id = str(raw.get("asset_id", "")).strip()
        if not asset_id or asset_id in by_id:
            raise ValueError("asset ids must be nonempty and unique")
        role = str(raw.get("role", "")).strip()
        if not role:
            raise ValueError("asset role cannot be empty")
        subject_value = raw.get("subject_id")
        subject = _normalize_subject(subject_value) if subject_value not in (None, "") else None
        speed = raw.get("speed_m_s")
        speed_value = None if speed in (None, "") else float(speed)
        path = _safe_inventory_path(data_root, str(raw.get("path", "")))
        expected_size = int(raw.get("size_bytes", 0))
        expected_hash = str(raw.get("sha256", "")).strip().lower()
        valid_hash = len(expected_hash) == 64 and all(
            character in "0123456789abcdef" for character in expected_hash
        )
        observed_size = path.stat().st_size if path.is_file() else None
        observed_hash = sha256_file(path) if path.is_file() else None
        passed = (
            valid_hash
            and expected_size > 0
            and observed_size == expected_size
            and observed_hash == expected_hash
        )
        row = {
            **dict(raw),
            "asset_id": asset_id,
            "role": role,
            "subject_id": subject,
            "speed_m_s": speed_value,
            "absolute_path": str(path),
            "observed_size_bytes": observed_size,
            "observed_sha256": observed_hash,
            "pass": passed,
        }
        assets.append(row)
        by_id[asset_id] = row
    if not assets:
        raise ValueError("personalization inventory has no assets")
    return {
        "schema_version": DYNAMIC_PERSONALIZATION_SCHEMA_VERSION,
        "scope": "g6-personalization-inventory-audit",
        "inventory_sha256": sha256_file(inventory_path),
        "subjects": subjects,
        "assets": assets,
        "assets_by_id": by_id,
        "pass": all(asset["pass"] for asset in assets),
    }


def _single_asset(
    assets: Sequence[Mapping[str, Any]], role: str, subject: Optional[str] = None
) -> Mapping[str, Any]:
    matches = [
        asset
        for asset in assets
        if asset.get("role") == role
        and (subject is None or asset.get("subject_id") == subject)
    ]
    if len(matches) != 1:
        raise ValueError(
            "expected one {} asset{}, observed {}".format(
                role, " for subject {}".format(subject) if subject else "", len(matches)
            )
        )
    return matches[0]


def _speed_asset(
    assets: Sequence[Mapping[str, Any]], role: str, subject: str, speed: float
) -> Mapping[str, Any]:
    matches = [
        asset
        for asset in assets
        if asset.get("role") == role
        and asset.get("subject_id") == subject
        and asset.get("speed_m_s") is not None
        and math.isclose(float(asset["speed_m_s"]), float(speed), abs_tol=1e-9)
    ]
    if len(matches) != 1:
        raise ValueError(
            "expected one {} asset for subject {} at {} m/s, observed {}".format(
                role, subject, speed, len(matches)
            )
        )
    return matches[0]


def _generic_model_asset(
    assets: Sequence[Mapping[str, Any]], target_subject: str
) -> Mapping[str, Any]:
    """Choose a canonical generic or deterministic cross-subject HBM2 template."""
    candidates = [asset for asset in assets if asset.get("role") == "generic_model"]
    canonical = [asset for asset in candidates if asset.get("subject_id") is None]
    if len(canonical) == 1:
        return canonical[0]
    if len(canonical) > 1:
        raise ValueError("inventory contains multiple subject-independent generic models")
    eligible = [
        asset for asset in candidates if asset.get("subject_id") != target_subject
    ]
    if not eligible:
        raise ValueError("no target-independent HBM2 template is available")
    return sorted(
        eligible,
        key=lambda asset: (
            str(asset.get("subject_id", "")),
            str(asset.get("asset_id", "")),
        ),
    )[0]


def _split_subjects(protocol: Mapping[str, Any]) -> Dict[str, List[str]]:
    design = protocol.get("design") or {}
    blind = [_normalize_subject(value) for value in design.get("blind_subjects", BLIND_SUBJECTS)]
    development = [
        _normalize_subject(value)
        for value in design.get("development_subjects", DEVELOPMENT_SUBJECTS)
    ]
    if tuple(blind) != BLIND_SUBJECTS:
        raise ValueError("G6e blind subjects are frozen as 05, 11, 18, and 19")
    if set(blind) & set(development):
        raise ValueError("development and blind subject sets must be disjoint")
    return {"blind": blind, "development": development}


def _is_digest_pinned_image(value: str) -> bool:
    text = str(value).strip().lower()
    digest = text.split("@sha256:", 1)[1] if "@sha256:" in text else (
        text.split("sha256:", 1)[1] if text.startswith("sha256:") else ""
    )
    return len(digest) == 64 and all(character in "0123456789abcdef" for character in digest)


def build_personalization_plan(
    protocol_path: Path,
    inventory_path: Path,
    data_root: Path,
    *,
    subject_id: str,
    split: str,
) -> Dict[str, Any]:
    """Create a fit-worker plan with target hidden inputs excluded."""
    protocol_path = protocol_path.resolve()
    protocol = _load_yaml(protocol_path)
    if str(protocol.get("schema_version")) != DYNAMIC_PERSONALIZATION_SCHEMA_VERSION:
        raise ValueError("unsupported G6d protocol version")
    if split not in {"development", "blind", "study31"}:
        raise ValueError("split must be development, blind, or study31")
    subject = _normalize_subject(subject_id)
    audit = validate_personalization_inventory(inventory_path, data_root)
    if not audit["pass"]:
        raise ValueError("G6d/G6e inventory failed checksum verification")
    splits = _split_subjects(protocol)
    if split in {"development", "blind"} and subject not in splits[split]:
        raise ValueError("subject {} is not assigned to split {}".format(subject, split))
    if subject not in audit["subjects"]:
        raise ValueError("target subject lacks anthropometry")
    assets = audit["assets"]
    generic = _generic_model_asset(assets, subject)
    ik_setup = _single_asset(assets, "ik_setup")
    selected: List[Mapping[str, Any]] = [generic, ik_setup]
    trial_roles = []
    if split == "study31":
        marker = _single_asset(assets, "study31_t1_markers", subject)
        grf = _single_asset(assets, "study31_t1_ground_reaction", subject)
        selected.extend([marker, grf])
        trial_roles.append(
            {
                "trial_id": "T1",
                "speed_m_s": marker.get("speed_m_s"),
                "markers_asset_id": marker["asset_id"],
                "ground_reaction_asset_id": grf["asset_id"],
                "role": "fit",
            }
        )
    else:
        for speed in FIT_SPEEDS_M_S:
            marker = _speed_asset(assets, "dynamic_markers", subject, speed)
            grf = _speed_asset(assets, "dynamic_ground_reaction", subject, speed)
            selected.extend([marker, grf])
            trial_roles.append(
                {
                    "trial_id": "speed_{:.2f}".format(speed),
                    "speed_m_s": speed,
                    "markers_asset_id": marker["asset_id"],
                    "ground_reaction_asset_id": grf["asset_id"],
                    "role": "fit",
                }
            )
    training_subjects = list(splits["development"])
    if split == "development":
        training_subjects = [value for value in training_subjects if value != subject]
    training_assets = []
    for training_subject in training_subjects:
        training = _single_asset(assets, "static_derived_model", training_subject)
        training_assets.append(training)
        selected.append(training)
    hidden = [
        asset
        for asset in assets
        if asset.get("subject_id") == subject
        and (
            asset.get("role") in FORBIDDEN_TARGET_ROLES
            or (
                asset.get("speed_m_s") is not None
                and math.isclose(
                    float(asset["speed_m_s"]), HELDOUT_SPEED_M_S, abs_tol=1e-9
                )
            )
        )
    ]
    selected_ids = {str(asset["asset_id"]) for asset in selected}
    if selected_ids & {str(asset["asset_id"]) for asset in hidden}:
        raise ValueError("target hidden asset entered the fit selection")
    inputs = {
        str(asset["asset_id"]): {
            "path": str(asset["absolute_path"]),
            "sha256": str(asset["observed_sha256"]),
            "size_bytes": int(asset["observed_size_bytes"]),
            "role": str(asset["role"]),
            "subject_id": asset.get("subject_id"),
            "speed_m_s": asset.get("speed_m_s"),
        }
        for asset in selected
    }
    engine = protocol.get("engine") or {}
    image = str(engine.get("container_image", ""))
    if not _is_digest_pinned_image(image):
        raise ValueError("AddBiomechanics container_image must be digest pinned")
    plan = {
        "schema_version": DYNAMIC_PERSONALIZATION_SCHEMA_VERSION,
        "scope": "g6d-dynamic-personalization-fit-plan",
        "protocol_sha256": sha256_file(protocol_path),
        "inventory_sha256": audit["inventory_sha256"],
        "split": split,
        "subject_id": subject,
        "training_subject_ids": training_subjects,
        "anthropometry": audit["subjects"][subject],
        "template_provenance": {
            "asset_id": generic["asset_id"],
            "subject_id": generic.get("subject_id"),
            "target_independent": generic.get("subject_id") != subject,
            "selection": (
                "canonical-subject-independent-HBM2"
                if generic.get("subject_id") is None
                else "deterministic-cross-subject-development-template"
            ),
        },
        "training_anthropometry": {
            value: audit["subjects"][value] for value in training_subjects
        },
        "trials": trial_roles,
        "inputs": inputs,
        "input_sha256": {key: value["sha256"] for key, value in inputs.items()},
        "forbidden_input_sha256": sorted(
            {str(asset["observed_sha256"]) for asset in hidden}
        ),
        "forbidden_asset_ids": sorted(str(asset["asset_id"]) for asset in hidden),
        "forbidden_target_roles": sorted(FORBIDDEN_TARGET_ROLES),
        "heldout_speed_m_s": None if split == "study31" else HELDOUT_SPEED_M_S,
        "engine": {
            "strategy": str(engine.get("strategy", "hybrid")),
            "addbiomechanics_version": str(engine.get("addbiomechanics_version", "v2.2")),
            "addbiomechanics_commit": str(engine.get("addbiomechanics_commit", "")),
            "container_image": image,
            "container_platform": str(engine.get("container_platform", "linux/amd64")),
            "cloud_upload": False,
        },
        "marker_classes": protocol.get("marker_classes") or {},
        "native_optimizer": protocol.get("native_optimizer") or {},
        "functional_calibration": protocol.get("functional_calibration") or {},
        "thresholds": protocol.get("thresholds") or {},
        "seed": int(protocol.get("seed", 20260722)),
        "forbidden_input_attestation": {
            "target_static_absent": True,
            "target_static_model_absent": True,
            "target_4ms_absent": True if split != "study31" else None,
            "target_t2_t3_absent": True if split == "study31" else None,
            "checked_by": "role-subject-speed-and-sha256",
        },
    }
    validate_fit_plan(plan)
    plan["plan_sha256"] = canonical_sha256(plan)
    return plan


def validate_fit_plan(plan: Mapping[str, Any]) -> Dict[str, Any]:
    """Fail if a worker plan contains or resolves any target hidden input."""
    if plan.get("scope") != "g6d-dynamic-personalization-fit-plan":
        raise ValueError("invalid dynamic-personalization worker scope")
    subject = _normalize_subject(plan.get("subject_id"))
    forbidden_hashes = set(str(value) for value in plan.get("forbidden_input_sha256", []))
    observed_hashes = set()
    fit_speeds = []
    target_fit_roles = []
    for asset_id, raw in (plan.get("inputs") or {}).items():
        role = str(raw.get("role", ""))
        asset_subject = raw.get("subject_id")
        speed = raw.get("speed_m_s")
        digest = str(raw.get("sha256", ""))
        path = Path(str(raw.get("path", ""))).resolve()
        if not path.is_file() or sha256_file(path) != digest:
            raise ValueError("fit input checksum mismatch: {}".format(asset_id))
        observed_hashes.add(digest)
        if asset_subject is not None and _normalize_subject(asset_subject) == subject:
            if role == "generic_model":
                raise ValueError("target static-derived model cannot be the generic template")
            if role in FORBIDDEN_TARGET_ROLES:
                raise ValueError("forbidden target role entered fit worker: {}".format(role))
            if speed is not None and math.isclose(
                float(speed), HELDOUT_SPEED_M_S, abs_tol=1e-9
            ):
                raise ValueError("held-out 4.00 m/s trial entered fit worker")
            if role in {"dynamic_markers", "dynamic_ground_reaction"}:
                fit_speeds.append(float(speed))
            if role in {
                "study31_t1_markers",
                "study31_t1_ground_reaction",
            }:
                target_fit_roles.append(role)
        # Secondary lexical defense catches an accidentally mislabelled target
        # path.  It is not the primary evidence, which remains role+hash.
        lower_name = path.name.lower()
        if asset_subject is not None and _normalize_subject(asset_subject) == subject:
            if "static" in lower_name or re.search(r"(?:^|[_-])4(?:\.0)?ms(?:[_\.-]|$)", lower_name):
                raise ValueError("target input filename indicates a forbidden trial")
    if observed_hashes & forbidden_hashes:
        raise ValueError("a forbidden target checksum entered the fit worker")
    if (plan.get("template_provenance") or {}).get("target_independent") is not True:
        raise ValueError("fit plan does not attest a target-independent HBM2 template")
    if plan.get("split") == "study31":
        if sorted(target_fit_roles) != [
            "study31_t1_ground_reaction",
            "study31_t1_markers",
        ]:
            raise ValueError("Study 31 fit worker requires only the T1 marker/GRF pair")
        if fit_speeds:
            raise ValueError("benchmark dynamic inputs cannot enter a Study 31 fit worker")
    else:
        expected = sorted([speed for speed in FIT_SPEEDS_M_S for _ in (0, 1)])
        if sorted(fit_speeds) != expected:
            raise ValueError("fit worker requires marker and GRF inputs at exactly three speeds")
    engine = plan.get("engine") or {}
    if engine.get("cloud_upload") is not False:
        raise ValueError("cloud upload is prohibited")
    if not _is_digest_pinned_image(str(engine.get("container_image", ""))):
        raise ValueError("container image is not digest pinned")
    return {
        "pass": True,
        "target_subject_id": subject,
        "observed_input_count": len(observed_hashes),
        "forbidden_hash_intersection_count": 0,
        "cloud_upload": False,
    }


def annotate_marker_roles(
    model_path: Path,
    output_path: Path,
    anatomical_markers: Sequence[str],
    tracking_markers: Sequence[str],
) -> Dict[str, Any]:
    """Write explicit AddBiomechanics ``fixed`` marker annotations."""
    anatomical = set(str(value) for value in anatomical_markers)
    tracking = set(str(value) for value in tracking_markers)
    if anatomical & tracking:
        raise ValueError("anatomical and tracking marker sets overlap")
    tree = ET.parse(str(model_path))
    markers = list(tree.getroot().iter("Marker"))
    model_names = {str(marker.get("name", "")) for marker in markers}
    missing = sorted((anatomical | tracking) - model_names)
    if missing:
        raise ValueError("marker classification names are absent from model: {}".format(missing))
    if len(anatomical) < 8:
        raise ValueError("AddBiomechanics requires at least eight anatomical markers")
    rows = []
    for marker in markers:
        name = str(marker.get("name", ""))
        fixed = marker.find("fixed")
        if fixed is None:
            fixed = ET.SubElement(marker, "fixed")
        if name in anatomical:
            fixed.text = "true"
            marker_class = "anatomical"
        elif name in tracking:
            fixed.text = "false"
            marker_class = "tracking"
        else:
            raise ValueError("every model marker must be explicitly classified: {}".format(name))
        rows.append({"marker": name, "class": marker_class})
    output_path.parent.mkdir(parents=True, exist_ok=True)
    tree.write(str(output_path), encoding="utf-8", xml_declaration=True)
    return {
        "input_model_sha256": sha256_file(model_path),
        "output_model_sha256": sha256_file(output_path),
        "anatomical_marker_count": len(anatomical),
        "tracking_marker_count": len(tracking),
        "markers": rows,
    }


def make_constraint_free_fit_model(model_path: Path, output_path: Path) -> Dict[str, Any]:
    """Remove only CoordinateCouplerConstraints from a fit-only model."""
    tree = ET.parse(str(model_path))
    root = tree.getroot()
    removed = []
    for parent in root.iter():
        for child in list(parent):
            if child.tag == "CoordinateCouplerConstraint":
                removed.append(str(child.get("name", "")))
                parent.remove(child)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    tree.write(str(output_path), encoding="utf-8", xml_declaration=True)
    return {
        "input_model_sha256": sha256_file(model_path),
        "fit_model_sha256": sha256_file(output_path),
        "removed_coordinate_couplers": removed,
        "marker_bearing_frames_modified": False,
    }


def transfer_fit_parameters_to_full_model(
    fitted_model_path: Path,
    full_template_path: Path,
    output_path: Path,
) -> Dict[str, Any]:
    """Transfer fitted geometry into HBM2 while retaining its constraints.

    Only named, scale-related properties are copied.  The full template's
    joints, constraints, muscles, and component topology remain authoritative.
    A caller must subsequently run ``forward_marker_equivalence`` before the
    transferred model can be accepted.
    """
    fitted_tree = ET.parse(str(fitted_model_path))
    full_tree = ET.parse(str(full_template_path))
    transferable = {
        "Marker": ("location",),
        "Body": ("mass", "mass_center", "inertia"),
        "PhysicalOffsetFrame": ("translation", "orientation"),
        "Mesh": ("scale_factors",),
        "PathPoint": ("location",),
        "ConditionalPathPoint": ("location",),
        "MovingPathPoint": ("x_location", "y_location", "z_location"),
    }
    fitted_index: Dict[Tuple[str, str], ET.Element] = {}
    for element in fitted_tree.getroot().iter():
        name = str(element.get("name", ""))
        if element.tag in transferable and name:
            key = (element.tag, name)
            if key in fitted_index:
                raise ValueError("fitted model has duplicate named component: {}".format(key))
            fitted_index[key] = element
    rows = []
    marker_updates = 0
    for element in full_tree.getroot().iter():
        name = str(element.get("name", ""))
        key = (element.tag, name)
        source = fitted_index.get(key)
        if source is None:
            continue
        for property_name in transferable[element.tag]:
            source_property = source.find(property_name)
            target_property = element.find(property_name)
            if source_property is None or target_property is None:
                continue
            before = str(target_property.text or "").strip()
            after = str(source_property.text or "").strip()
            if before == after:
                continue
            target_property.text = after
            rows.append(
                {
                    "component_type": element.tag,
                    "component_name": name,
                    "property": property_name,
                    "before": before,
                    "after": after,
                }
            )
            if element.tag == "Marker" and property_name == "location":
                marker_updates += 1
    fitted_markers = {
        str(element.get("name", ""))
        for element in fitted_tree.getroot().iter("Marker")
    }
    full_markers = {
        str(element.get("name", "")) for element in full_tree.getroot().iter("Marker")
    }
    if not fitted_markers or fitted_markers != full_markers:
        raise ValueError("fitted and full models must contain identical marker sets")
    output_path.parent.mkdir(parents=True, exist_ok=True)
    full_tree.write(str(output_path), encoding="utf-8", xml_declaration=True)
    retained_constraints = [
        str(element.get("name", ""))
        for element in full_tree.getroot().iter("CoordinateCouplerConstraint")
    ]
    return {
        "fitted_model_sha256": sha256_file(fitted_model_path),
        "full_template_sha256": sha256_file(full_template_path),
        "output_model_sha256": sha256_file(output_path),
        "transferred_property_count": len(rows),
        "updated_marker_count": marker_updates,
        "retained_coordinate_couplers": retained_constraints,
        "topology_source": "full-hbm2-template",
        "properties": rows,
    }


def forward_marker_equivalence(
    full_model_path: Path,
    fit_model_path: Path,
    *,
    pose_count: int = 100,
    seed: int = 20260722,
    tolerance_m: float = 1e-6,
) -> Dict[str, Any]:
    """Compare full and fit-only marker forward kinematics over 100 poses."""
    try:
        import opensim
    except ModuleNotFoundError as exc:
        raise RuntimeError("forward-marker equivalence requires OpenSim Python") from exc
    full = opensim.Model(str(full_model_path))
    fit = opensim.Model(str(fit_model_path))
    full_state = full.initSystem()
    fit_state = fit.initSystem()
    full_coordinates = full.getCoordinateSet()
    fit_coordinates = fit.getCoordinateSet()
    common_coordinates = [
        full_coordinates.get(index).getName()
        for index in range(full_coordinates.getSize())
        if fit_coordinates.getIndex(full_coordinates.get(index).getName()) >= 0
    ]
    full_markers = full.getMarkerSet()
    fit_markers = fit.getMarkerSet()
    common_markers = [
        full_markers.get(index).getName()
        for index in range(full_markers.getSize())
        if fit_markers.getIndex(full_markers.get(index).getName()) >= 0
    ]
    if not common_coordinates or not common_markers:
        raise ValueError("models have no common coordinates or markers")
    generator = np.random.default_rng(int(seed))
    maximum = 0.0
    for _ in range(int(pose_count)):
        for name in common_coordinates:
            left = full_coordinates.get(name)
            right = fit_coordinates.get(name)
            lower = max(float(left.getRangeMin()), float(right.getRangeMin()))
            upper = min(float(left.getRangeMax()), float(right.getRangeMax()))
            if not math.isfinite(lower) or not math.isfinite(upper) or lower >= upper:
                value = 0.0
            else:
                value = float(generator.uniform(lower, upper))
            left.setValue(full_state, value, False)
            right.setValue(fit_state, value, False)
        full.realizePosition(full_state)
        fit.realizePosition(fit_state)
        for name in common_markers:
            first = full_markers.get(name).getLocationInGround(full_state)
            second = fit_markers.get(name).getLocationInGround(fit_state)
            distance = math.sqrt(
                sum((float(first.get(axis)) - float(second.get(axis))) ** 2 for axis in range(3))
            )
            maximum = max(maximum, distance)
    return {
        "pose_count": int(pose_count),
        "marker_count": len(common_markers),
        "coordinate_count": len(common_coordinates),
        "maximum_forward_marker_difference_m": maximum,
        "tolerance_m": float(tolerance_m),
        "pass": maximum <= float(tolerance_m),
        "seed": int(seed),
    }


def _sex_code(value: str) -> float:
    normalized = str(value).strip().lower()
    if normalized in {"male", "m"}:
        return 1.0
    if normalized in {"female", "f"}:
        return -1.0
    return 0.0


def anthropometry_vector(row: Mapping[str, Any]) -> np.ndarray:
    return np.asarray(
        [
            1.0,
            float(row["height_m"]),
            float(row["mass_kg"]) / 100.0,
            _sex_code(str(row.get("sex", "unknown"))),
            float(row["foot_length_m"]),
        ],
        dtype=float,
    )


def _ridge_coefficients(x: np.ndarray, y: np.ndarray, penalty: float) -> np.ndarray:
    regularizer = np.eye(x.shape[1], dtype=float) * float(penalty)
    regularizer[0, 0] = 0.0
    return np.linalg.pinv(x.T @ x + regularizer) @ x.T @ y


def _parse_vector_text(element: Optional[ET.Element], size: int = 3) -> Optional[np.ndarray]:
    if element is None or element.text is None:
        return None
    values = np.asarray([float(value) for value in element.text.split()], dtype=float)
    if values.shape != (size,) or not np.all(np.isfinite(values)):
        return None
    return values


def _load_model_anatomy(model_path: Path) -> Dict[str, Any]:
    """Extract scale proxies and joint-center/axis geometry from an OpenSim model."""
    tree = ET.parse(str(model_path))
    root = tree.getroot()
    markers = load_model_markers(model_path)
    markers_by_body: Dict[str, List[np.ndarray]] = {}
    for raw in markers.values():
        markers_by_body.setdefault(str(raw["body"]), []).append(
            np.asarray(raw["location_m"], dtype=float)
        )
    segments = {}
    for body, locations in markers_by_body.items():
        matrix = np.vstack(locations)
        # The three-axis marker envelope is an observable proxy for the scale
        # baked into a static-derived model.  It remains defined for sparse
        # marker sets by also retaining RMS distance from the body origin.
        segments[body] = {
            "marker_envelope_m": np.ptp(matrix, axis=0).tolist(),
            "marker_origin_rms_m": np.sqrt(np.mean(np.square(matrix), axis=0)).tolist(),
            "marker_count": int(matrix.shape[0]),
        }
    joints = {}
    for element in root.iter():
        name = str(element.get("name", ""))
        if not name or not element.tag.endswith("Joint"):
            continue
        properties = {}
        for property_name in (
            "location_in_parent",
            "location",
            "orientation_in_parent",
            "orientation",
        ):
            value = _parse_vector_text(element.find(property_name))
            if value is not None:
                properties[property_name] = value.tolist()
        axes = {}
        for transform_axis in element.iter("TransformAxis"):
            axis_name = str(transform_axis.get("name", ""))
            value = _parse_vector_text(transform_axis.find("axis"))
            if axis_name and value is not None:
                axes[axis_name] = value.tolist()
        if properties or axes:
            joints[name] = {"properties": properties, "axes": axes}
    return {"segments": segments, "joints": joints}


def _shrunk_vector_prior(
    x: np.ndarray,
    outcome: np.ndarray,
    penalty: float,
    shrinkage: float,
) -> Dict[str, Any]:
    coefficients = _ridge_coefficients(x, outcome, penalty)
    residual = outcome - x @ coefficients
    covariance = np.cov(residual, rowvar=False, ddof=1)
    covariance = np.atleast_2d(covariance)
    diagonal = np.diag(np.diag(covariance))
    shrunk = (1.0 - shrinkage) * covariance + shrinkage * diagonal
    shrunk += np.eye(outcome.shape[1]) * 1e-10
    return {
        "ridge_coefficients": coefficients.tolist(),
        "normalized_covariance": shrunk.tolist(),
        "residual_normalized_rmse": float(np.sqrt(np.mean(np.square(residual)))),
    }


def fit_anatomical_prior(
    training_models: Mapping[str, Path],
    anthropometry: Mapping[str, Mapping[str, Any]],
    *,
    ridge_candidates: Sequence[float],
    covariance_shrinkage: float,
) -> Dict[str, Any]:
    """Fit LOO priors for markers, segment scale proxies, and joint geometry."""
    subjects = sorted(training_models)
    if len(subjects) < 4:
        raise ValueError("anatomical prior requires at least four training subjects")
    if set(subjects) - set(anthropometry):
        raise ValueError("training model lacks anthropometry")
    markers_by_subject = {
        subject: load_model_markers(Path(training_models[subject])) for subject in subjects
    }
    anatomy_by_subject = {
        subject: _load_model_anatomy(Path(training_models[subject])) for subject in subjects
    }
    common_markers = set.intersection(
        *(set(markers) for markers in markers_by_subject.values())
    )
    if not common_markers:
        raise ValueError("training models have no common markers")
    x = np.vstack([anthropometry_vector(anthropometry[subject]) for subject in subjects])
    outcomes = {}
    marker_bodies = {}
    for marker in sorted(common_markers):
        bodies = {markers_by_subject[subject][marker]["body"] for subject in subjects}
        if len(bodies) != 1:
            raise ValueError("marker body assignment changes across subjects: {}".format(marker))
        marker_bodies[marker] = next(iter(bodies))
        outcomes[marker] = np.vstack(
            [
                np.asarray(markers_by_subject[subject][marker]["location_m"], dtype=float)
                / float(anthropometry[subject]["height_m"])
                for subject in subjects
            ]
        )
    common_segments = set.intersection(
        *(set(raw["segments"]) for raw in anatomy_by_subject.values())
    )
    segment_outcomes = {}
    for body in sorted(common_segments):
        segment_outcomes[body] = {}
        for property_name in ("marker_envelope_m", "marker_origin_rms_m"):
            segment_outcomes[body][property_name] = np.vstack(
                [
                    np.asarray(
                        anatomy_by_subject[subject]["segments"][body][property_name],
                        dtype=float,
                    )
                    / float(anthropometry[subject]["height_m"])
                    for subject in subjects
                ]
            )
    common_joints = set.intersection(
        *(set(raw["joints"]) for raw in anatomy_by_subject.values())
    )
    joint_outcomes: Dict[str, Dict[str, Dict[str, np.ndarray]]] = {}
    for joint in sorted(common_joints):
        common_properties = set.intersection(
            *(
                set(anatomy_by_subject[subject]["joints"][joint]["properties"])
                for subject in subjects
            )
        )
        common_axes = set.intersection(
            *(
                set(anatomy_by_subject[subject]["joints"][joint]["axes"])
                for subject in subjects
            )
        )
        properties = {}
        for property_name in sorted(common_properties):
            values = np.vstack(
                [
                    anatomy_by_subject[subject]["joints"][joint]["properties"][property_name]
                    for subject in subjects
                ]
            )
            if property_name.startswith("location"):
                values = values / np.asarray(
                    [anthropometry[subject]["height_m"] for subject in subjects],
                    dtype=float,
                )[:, None]
            properties[property_name] = values
        axes = {
            axis_name: np.vstack(
                [
                    anatomy_by_subject[subject]["joints"][joint]["axes"][axis_name]
                    for subject in subjects
                ]
            )
            for axis_name in sorted(common_axes)
        }
        if properties or axes:
            joint_outcomes[joint] = {"properties": properties, "axes": axes}
    candidates = [float(value) for value in ridge_candidates]
    if not candidates or any(value < 0 or not math.isfinite(value) for value in candidates):
        raise ValueError("ridge candidates must be finite and nonnegative")
    cv_rows = []
    for penalty in candidates:
        squared = []
        for heldout in range(len(subjects)):
            keep = np.ones(len(subjects), dtype=bool)
            keep[heldout] = False
            all_outcomes = list(outcomes.values())
            all_outcomes.extend(
                outcome
                for properties in segment_outcomes.values()
                for outcome in properties.values()
            )
            all_outcomes.extend(
                outcome
                for joint in joint_outcomes.values()
                for group in joint.values()
                for outcome in group.values()
            )
            for outcome in all_outcomes:
                coefficients = _ridge_coefficients(x[keep], outcome[keep], penalty)
                difference = x[heldout] @ coefficients - outcome[heldout]
                squared.extend(np.square(difference).tolist())
        cv_rows.append(
            {"ridge_penalty": penalty, "subject_loo_rmse": float(np.sqrt(np.mean(squared)))}
        )
    selected = min(cv_rows, key=lambda row: (row["subject_loo_rmse"], row["ridge_penalty"]))
    shrinkage = float(covariance_shrinkage)
    if not 0.0 <= shrinkage <= 1.0:
        raise ValueError("covariance shrinkage must be between zero and one")
    marker_priors = {}
    for marker, outcome in outcomes.items():
        marker_priors[marker] = {
            "body": marker_bodies[marker],
            **_shrunk_vector_prior(
                x, outcome, selected["ridge_penalty"], shrinkage
            ),
        }
    segment_priors = {
        body: {
            property_name: _shrunk_vector_prior(
                x, outcome, selected["ridge_penalty"], shrinkage
            )
            for property_name, outcome in properties.items()
        }
        for body, properties in segment_outcomes.items()
    }
    joint_priors = {
        joint: {
            group_name: {
                property_name: _shrunk_vector_prior(
                    x, outcome, selected["ridge_penalty"], shrinkage
                )
                for property_name, outcome in group.items()
            }
            for group_name, group in groups.items()
        }
        for joint, groups in joint_outcomes.items()
    }
    return {
        "schema_version": DYNAMIC_PERSONALIZATION_SCHEMA_VERSION,
        "scope": "g6d-anatomical-prior",
        "training_subject_ids": subjects,
        "training_model_sha256": {
            subject: sha256_file(Path(training_models[subject])) for subject in subjects
        },
        "feature_order": ["intercept", "height_m", "mass_100kg", "sex_code", "foot_length_m"],
        "ridge_selection": {
            "method": "subject-level-leave-one-out",
            "candidates": cv_rows,
            "selected_penalty": selected["ridge_penalty"],
        },
        "covariance_shrinkage": shrinkage,
        "markers": marker_priors,
        "segments": segment_priors,
        "joints": joint_priors,
    }


def predict_anatomical_prior(
    prior: Mapping[str, Any], anthropometry: Mapping[str, Any]
) -> Dict[str, Any]:
    vector = anthropometry_vector(anthropometry)
    height = float(anthropometry["height_m"])
    markers = {}
    for marker, raw in (prior.get("markers") or {}).items():
        coefficients = np.asarray(raw["ridge_coefficients"], dtype=float)
        mean_normalized = vector @ coefficients
        covariance_normalized = np.asarray(raw["normalized_covariance"], dtype=float)
        markers[str(marker)] = {
            "body": str(raw["body"]),
            "mean_location_m": (mean_normalized * height).tolist(),
            "covariance_m2": (covariance_normalized * height * height).tolist(),
            "standard_deviation_m": np.sqrt(
                np.maximum(np.diag(covariance_normalized) * height * height, 0.0)
            ).tolist(),
        }
    segments = {}
    for body, properties in (prior.get("segments") or {}).items():
        segments[body] = {}
        for property_name, raw in properties.items():
            mean = vector @ np.asarray(raw["ridge_coefficients"], dtype=float)
            covariance = np.asarray(raw["normalized_covariance"], dtype=float)
            segments[body][property_name] = {
                "mean_m": (mean * height).tolist(),
                "covariance_m2": (covariance * height * height).tolist(),
            }
    joints = {}
    for joint, groups in (prior.get("joints") or {}).items():
        joints[joint] = {"properties": {}, "axes": {}}
        for property_name, raw in groups.get("properties", {}).items():
            mean = vector @ np.asarray(raw["ridge_coefficients"], dtype=float)
            covariance = np.asarray(raw["normalized_covariance"], dtype=float)
            scale = height if property_name.startswith("location") else 1.0
            joints[joint]["properties"][property_name] = {
                "mean": (mean * scale).tolist(),
                "covariance": (covariance * scale * scale).tolist(),
            }
        for axis_name, raw in groups.get("axes", {}).items():
            mean = vector @ np.asarray(raw["ridge_coefficients"], dtype=float)
            norm = float(np.linalg.norm(mean))
            if norm > 0:
                mean = mean / norm
            joints[joint]["axes"][axis_name] = {
                "mean": mean.tolist(),
                "covariance": raw["normalized_covariance"],
            }
    return {
        "scope": "g6d-subject-anatomical-prior",
        "subject_id": _normalize_subject(anthropometry["subject_id"]),
        "markers": markers,
        "segments": segments,
        "joints": joints,
        "prior_sha256": canonical_sha256(prior),
    }


def apply_marker_prior(
    model_path: Path, output_path: Path, prediction: Mapping[str, Any]
) -> Dict[str, Any]:
    """Apply predicted marker and joint geometry; record segment-scale proxies."""
    original_anatomy = _load_model_anatomy(model_path)
    tree = ET.parse(str(model_path))
    by_name = {
        str(marker.get("name", "")): marker for marker in tree.getroot().iter("Marker")
    }
    rows = []
    for name, raw in (prediction.get("markers") or {}).items():
        if name not in by_name:
            continue
        marker = by_name[name]
        parent = marker.findtext("socket_parent_frame") or marker.findtext("body") or ""
        body = str(parent).replace("\\", "/").split("/")[-1]
        if body != str(raw["body"]):
            raise ValueError("marker prior body mismatch: {}".format(name))
        location = marker.find("location")
        if location is None:
            raise ValueError("model marker has no location: {}".format(name))
        old = [float(value) for value in str(location.text).split()]
        new = [float(value) for value in raw["mean_location_m"]]
        location.text = " ".join("{:.12g}".format(value) for value in new)
        rows.append(
            {"marker": name, "body": body, "old_location_m": old, "prior_location_m": new}
        )
    joint_rows = []
    joints_by_name = {
        str(element.get("name", "")): element
        for element in tree.getroot().iter()
        if element.tag.endswith("Joint") and element.get("name")
    }
    for joint_name, raw in (prediction.get("joints") or {}).items():
        joint = joints_by_name.get(str(joint_name))
        if joint is None:
            continue
        for property_name, value_row in raw.get("properties", {}).items():
            property_element = joint.find(property_name)
            if property_element is None:
                continue
            old = [float(value) for value in str(property_element.text).split()]
            new = [float(value) for value in value_row["mean"]]
            property_element.text = " ".join("{:.12g}".format(value) for value in new)
            joint_rows.append(
                {
                    "joint": joint_name,
                    "property": property_name,
                    "old": old,
                    "prior_mean": new,
                    "prior_covariance": value_row["covariance"],
                }
            )
        axes_by_name = {
            str(element.get("name", "")): element
            for element in joint.iter("TransformAxis")
            if element.get("name")
        }
        for axis_name, value_row in raw.get("axes", {}).items():
            transform_axis = axes_by_name.get(str(axis_name))
            axis_element = None if transform_axis is None else transform_axis.find("axis")
            if axis_element is None:
                continue
            old = [float(value) for value in str(axis_element.text).split()]
            new = [float(value) for value in value_row["mean"]]
            axis_element.text = " ".join("{:.12g}".format(value) for value in new)
            joint_rows.append(
                {
                    "joint": joint_name,
                    "property": "axis:{}".format(axis_name),
                    "old": old,
                    "prior_mean": new,
                    "prior_covariance": value_row["covariance"],
                }
            )
    segment_rows = []
    for body, raw in (prediction.get("segments") or {}).items():
        observed = (original_anatomy.get("segments") or {}).get(body)
        if observed is None:
            continue
        predicted = raw.get("marker_origin_rms_m", {}).get("mean_m")
        if predicted is None:
            continue
        current = np.asarray(observed["marker_origin_rms_m"], dtype=float)
        target = np.asarray(predicted, dtype=float)
        ratios = np.divide(
            target,
            current,
            out=np.full(3, np.nan, dtype=float),
            where=np.abs(current) > 1e-12,
        )
        segment_rows.append(
            {
                "body": body,
                "current_marker_origin_rms_m": current.tolist(),
                "predicted_marker_origin_rms_m": target.tolist(),
                "scale_proxy_ratio": ratios.tolist(),
                "applied_through_marker_and_joint_priors": True,
            }
        )
    output_path.parent.mkdir(parents=True, exist_ok=True)
    tree.write(str(output_path), encoding="utf-8", xml_declaration=True)
    return {
        "input_model_sha256": sha256_file(model_path),
        "output_model_sha256": sha256_file(output_path),
        "updated_marker_count": len(rows),
        "markers": rows,
        "updated_joint_property_count": len(joint_rows),
        "joint_centers_and_axes": joint_rows,
        "segment_scale_proxies": segment_rows,
    }


def regularized_marker_update(
    dynamic_estimates: Mapping[str, Sequence[Sequence[float]]],
    prediction: Mapping[str, Any],
    marker_classes: Mapping[str, Sequence[str]],
    *,
    dynamic_weight: float,
    anatomical_prior_weight: float,
    tracking_prior_weight: float,
    dynamic_noise_floor_m: float = 0.005,
    rigid_cluster_weight: float = 1.0,
    bilateral_symmetry_weight: float = 0.0,
) -> Dict[str, Any]:
    """Closed-form marker update for the harmo-native fallback objective."""
    anatomical = set(marker_classes.get("anatomical", []))
    tracking = set(marker_classes.get("tracking", []))
    rows = {}
    for marker, samples in dynamic_estimates.items():
        if marker not in (prediction.get("markers") or {}):
            continue
        values = np.asarray(samples, dtype=float)
        if values.ndim != 2 or values.shape[1] != 3:
            raise ValueError("dynamic marker estimates must have shape (n, 3)")
        finite = values[np.all(np.isfinite(values), axis=1)]
        if finite.size == 0:
            continue
        dynamic_mean = np.median(finite, axis=0)
        if finite.shape[0] > 1:
            dynamic_covariance = np.cov(finite, rowvar=False, ddof=1)
        else:
            dynamic_covariance = np.zeros((3, 3), dtype=float)
        noise_floor = float(dynamic_noise_floor_m)
        if not math.isfinite(noise_floor) or noise_floor <= 0:
            raise ValueError("dynamic_noise_floor_m must be finite and positive")
        dynamic_covariance += np.eye(3) * noise_floor * noise_floor
        dynamic_inverse = np.linalg.pinv(dynamic_covariance)
        prior_row = prediction["markers"][marker]
        prior_mean = np.asarray(prior_row["mean_location_m"], dtype=float)
        covariance = np.asarray(prior_row["covariance_m2"], dtype=float)
        inverse = np.linalg.pinv(covariance)
        prior_weight = (
            float(anatomical_prior_weight)
            if marker in anatomical
            else float(tracking_prior_weight)
            if marker in tracking
            else None
        )
        if prior_weight is None:
            raise ValueError("marker has no anatomical/tracking class: {}".format(marker))
        dynamic_precision_weight = float(dynamic_weight) * float(rigid_cluster_weight)
        system = dynamic_precision_weight * dynamic_inverse + prior_weight * inverse
        rhs = (
            dynamic_precision_weight * dynamic_inverse @ dynamic_mean
            + prior_weight * inverse @ prior_mean
        )
        estimate = np.linalg.solve(system, rhs)
        posterior = np.linalg.pinv(system)
        rows[marker] = {
            "location_m": estimate.tolist(),
            "posterior_covariance_m2": posterior.tolist(),
            "posterior_standard_deviation_m": np.sqrt(
                np.maximum(np.diag(posterior), 0.0)
            ).tolist(),
            "dynamic_sample_count": int(finite.shape[0]),
            "dynamic_covariance_m2": dynamic_covariance.tolist(),
            "marker_class": "anatomical" if marker in anatomical else "tracking",
        }
    symmetry_weight = float(bilateral_symmetry_weight)
    if symmetry_weight < 0 or not math.isfinite(symmetry_weight):
        raise ValueError("bilateral_symmetry_weight must be finite and nonnegative")
    symmetry_rows = []
    if symmetry_weight > 0:
        blend = symmetry_weight / (1.0 + symmetry_weight)
        mirror = np.diag([1.0, 1.0, -1.0])
        for left in sorted(name for name in rows if name.startswith("L")):
            right = "R" + left[1:]
            if right not in rows:
                continue
            prior_left = np.asarray(prediction["markers"][left]["mean_location_m"], dtype=float)
            prior_right = np.asarray(prediction["markers"][right]["mean_location_m"], dtype=float)
            estimate_left = np.asarray(rows[left]["location_m"], dtype=float)
            estimate_right = np.asarray(rows[right]["location_m"], dtype=float)
            left_delta = estimate_left - prior_left
            right_delta = estimate_right - prior_right
            shared_right_delta = 0.5 * (right_delta + mirror @ left_delta)
            symmetric_right = prior_right + shared_right_delta
            symmetric_left = prior_left + mirror @ shared_right_delta
            rows[right]["location_m"] = (
                (1.0 - blend) * estimate_right + blend * symmetric_right
            ).tolist()
            rows[left]["location_m"] = (
                (1.0 - blend) * estimate_left + blend * symmetric_left
            ).tolist()
            symmetry_rows.append({"left_marker": left, "right_marker": right})
    return {
        "markers": rows,
        "objective": "dynamic+anatomical-prior+rigid-cluster+bilateral-symmetry",
        "weights": {
            "dynamic": float(dynamic_weight),
            "anatomical_prior": float(anatomical_prior_weight),
            "tracking_prior": float(tracking_prior_weight),
            "rigid_cluster": float(rigid_cluster_weight),
            "bilateral_symmetry": symmetry_weight,
        },
        "bilateral_pairs": symmetry_rows,
    }


def score_joint_center(
    rotation_a: Sequence[Sequence[Sequence[float]]],
    translation_a: Sequence[Sequence[float]],
    rotation_b: Sequence[Sequence[Sequence[float]]],
    translation_b: Sequence[Sequence[float]],
    *,
    bootstrap_iterations: int = 500,
    seed: int = 20260722,
    threshold_m: float = 0.006,
) -> Dict[str, Any]:
    """Estimate a functional center using the SCoRE least-squares equation."""
    ra = np.asarray(rotation_a, dtype=float)
    rb = np.asarray(rotation_b, dtype=float)
    ta = np.asarray(translation_a, dtype=float)
    tb = np.asarray(translation_b, dtype=float)
    if ra.shape != rb.shape or ra.ndim != 3 or ra.shape[1:] != (3, 3):
        raise ValueError("SCoRE rotations must have shape (n, 3, 3)")
    if ta.shape != tb.shape or ta.shape != (ra.shape[0], 3):
        raise ValueError("SCoRE translations must have shape (n, 3)")

    def solve(indices: np.ndarray) -> Tuple[np.ndarray, np.ndarray, float]:
        matrix = np.vstack(
            [np.hstack((ra[index], -rb[index])) for index in indices]
        )
        rhs = np.concatenate([tb[index] - ta[index] for index in indices])
        solution, _, _, _ = np.linalg.lstsq(matrix, rhs, rcond=None)
        centers_a = ta[indices] + np.einsum("nij,j->ni", ra[indices], solution[:3])
        centers_b = tb[indices] + np.einsum("nij,j->ni", rb[indices], solution[3:])
        residual = float(np.sqrt(np.mean(np.sum(np.square(centers_a - centers_b), axis=1))))
        return solution[:3], solution[3:], residual

    indices = np.arange(ra.shape[0])
    center_a, center_b, residual = solve(indices)
    generator = np.random.default_rng(int(seed))
    bootstrap = []
    for _ in range(int(bootstrap_iterations)):
        sample = generator.choice(indices, size=indices.size, replace=True)
        bootstrap.append(solve(sample)[2])
    return {
        "center_in_segment_a_m": center_a.tolist(),
        "center_in_segment_b_m": center_b.tolist(),
        "residual_rms_m": residual,
        "bootstrap_residual_p95_m": float(np.percentile(bootstrap, 95)),
        "identifiable": float(np.percentile(bootstrap, 95)) <= float(threshold_m),
        "threshold_m": float(threshold_m),
        "bootstrap_iterations": int(bootstrap_iterations),
        "seed": int(seed),
    }


def sara_axis_repeatability(
    axes: Sequence[Sequence[float]], *, threshold_deg: float = 5.0
) -> Dict[str, Any]:
    vectors = np.asarray(axes, dtype=float)
    if vectors.ndim != 2 or vectors.shape[1] != 3 or vectors.shape[0] < 2:
        raise ValueError("SARA repeatability requires at least two three-vectors")
    norms = np.linalg.norm(vectors, axis=1)
    if np.any(norms <= 0) or not np.all(np.isfinite(norms)):
        raise ValueError("SARA axes must be finite and nonzero")
    vectors = vectors / norms[:, None]
    reference = vectors[0]
    vectors = np.asarray([vector if np.dot(vector, reference) >= 0 else -vector for vector in vectors])
    mean = np.mean(vectors, axis=0)
    mean /= np.linalg.norm(mean)
    angles = np.degrees(np.arccos(np.clip(vectors @ mean, -1.0, 1.0)))
    maximum = float(np.max(angles))
    return {
        "mean_axis": mean.tolist(),
        "trial_angle_differences_deg": angles.tolist(),
        "maximum_trial_axis_difference_deg": maximum,
        "identifiable": maximum <= float(threshold_deg),
        "threshold_deg": float(threshold_deg),
    }


def functional_identifiability(
    score_result: Optional[Mapping[str, Any]],
    sara_result: Optional[Mapping[str, Any]],
) -> Dict[str, Any]:
    hip = score_result is not None and score_result.get("identifiable") is True
    knee = sara_result is not None and sara_result.get("identifiable") is True
    return {
        "hip_center": "functional" if hip else "population_prior",
        "knee_axis": "functional" if knee else "population_prior",
        "functional_unidentifiable": not (hip and knee),
        "score": None if score_result is None else dict(score_result),
        "sara": None if sara_result is None else dict(sara_result),
    }


def _rotation_vector(matrix: np.ndarray) -> np.ndarray:
    cosine = float(np.clip((np.trace(matrix) - 1.0) / 2.0, -1.0, 1.0))
    angle = math.acos(cosine)
    if angle < 1e-8:
        return np.zeros(3, dtype=float)
    sine = math.sin(angle)
    if abs(sine) < 1e-8:
        values, vectors = np.linalg.eigh((matrix + np.eye(3)) / 2.0)
        axis = vectors[:, int(np.argmax(values))]
    else:
        axis = np.asarray(
            [
                matrix[2, 1] - matrix[1, 2],
                matrix[0, 2] - matrix[2, 0],
                matrix[1, 0] - matrix[0, 1],
            ]
        ) / (2.0 * sine)
    return axis * angle


def _functional_trial_axis(relative_rotations: Sequence[np.ndarray]) -> np.ndarray:
    rotations = np.asarray(relative_rotations, dtype=float)
    if rotations.ndim != 3 or rotations.shape[0] < 3:
        raise ValueError("functional knee-axis estimation needs at least three poses")
    reference = rotations[0]
    vectors = np.vstack([_rotation_vector(reference.T @ value) for value in rotations[1:]])
    vectors = vectors[np.linalg.norm(vectors, axis=1) > math.radians(1.0)]
    if vectors.shape[0] < 2:
        raise ValueError("functional knee-axis motion excursion is below one degree")
    _, _, right = np.linalg.svd(vectors, full_matrices=False)
    axis = right[0]
    return axis / np.linalg.norm(axis)


def estimate_functional_anatomy_from_motions(
    model_path: Path,
    motion_paths: Sequence[Path],
    marker_paths: Optional[Sequence[Path]] = None,
    *,
    score_threshold_m: float = 0.006,
    sara_threshold_deg: float = 5.0,
    bootstrap_iterations: int = 200,
    seed: int = 20260722,
    sample_stride: int = 10,
    rigid_cluster_residual_threshold_m: float = 0.02,
) -> Dict[str, Any]:
    """Try SCoRE/SARA from independent marker-cluster segment poses.

    IK-derived body transforms are intentionally prohibited: constrained body
    transforms would reproduce the current model's joint center by construction
    and create a circular, falsely precise SCoRE estimate.
    """
    if marker_paths is None or len(marker_paths) != len(motion_paths):
        raise ValueError(
            "functional calibration requires one independent marker trial per IK motion"
        )
    required = ("pelvis", "femur_r", "femur_l", "tibia_r", "tibia_l")
    model_markers = load_model_markers(model_path)
    body_markers = {
        body: sorted(
            name for name, raw in model_markers.items() if raw["body"] == body
        )
        for body in required
    }

    def read_trial(path: Path) -> Dict[str, np.ndarray]:
        lines = Path(path).read_text(encoding="utf-8").splitlines()
        header_index = next(
            (
                index
                for index, line in enumerate(lines)
                if line.startswith("Frame#\tTime\t")
                or line.startswith("Frame#\tTimes\t")
            ),
            None,
        )
        if header_index is None:
            raise ValueError("functional marker trial has no TRC marker header")
        names = [
            value.strip()
            for value in lines[header_index].split("\t")[2:]
            if value.strip()
        ]
        unit_scale = 1.0
        if header_index >= 2:
            labels = lines[header_index - 2].split("\t")
            values = lines[header_index - 1].split("\t")
            if "Units" in labels and labels.index("Units") < len(values):
                unit = values[labels.index("Units")].strip().lower()
                unit_scale = {"m": 1.0, "mm": 0.001}.get(unit, math.nan)
        if not math.isfinite(unit_scale):
            raise ValueError("functional marker trial has unsupported TRC units")
        samples = {name: [] for name in names}
        for row_index, line in enumerate(lines[header_index + 2 :]):
            if row_index % max(1, int(sample_stride)) or not line.strip():
                continue
            fields = line.split("\t")
            if len(fields) < 2 + 3 * len(names):
                continue
            for marker_index, name in enumerate(names):
                raw = fields[2 + 3 * marker_index : 5 + 3 * marker_index]
                try:
                    value = [float(item) * unit_scale for item in raw]
                except ValueError:
                    value = [math.nan, math.nan, math.nan]
                samples[name].append(value)
        return {name: np.asarray(values, dtype=float) for name, values in samples.items()}

    marker_trials = [read_trial(Path(path)) for path in marker_paths]
    cluster_markers = {}
    for body in required:
        common = [
            marker
            for marker in body_markers[body]
            if all(marker in trial for trial in marker_trials)
        ]
        local = np.vstack(
            [model_markers[marker]["location_m"] for marker in common]
        ) if common else np.empty((0, 3))
        rank = int(np.linalg.matrix_rank(local - np.mean(local, axis=0))) if len(common) else 0
        cluster_markers[body] = {
            "markers": common,
            "marker_count": len(common),
            "centered_geometry_rank": rank,
        }
        if len(common) < 3 or rank < 2:
            raise ValueError(
                "functional_unidentifiable: {} needs at least three non-collinear observed markers; observed {} rank {} ({})".format(
                    body, len(common), rank, ",".join(common)
                )
            )

    def rigid_pose(local: np.ndarray, observed: np.ndarray) -> Tuple[np.ndarray, np.ndarray, float]:
        local_center = np.mean(local, axis=0)
        observed_center = np.mean(observed, axis=0)
        left, _, right = np.linalg.svd(
            (local - local_center).T @ (observed - observed_center)
        )
        rotation = right.T @ left.T
        if np.linalg.det(rotation) < 0:
            right[-1, :] *= -1
            rotation = right.T @ left.T
        translation = observed_center - rotation @ local_center
        fitted = (rotation @ local.T).T + translation
        residual = float(np.sqrt(np.mean(np.sum(np.square(fitted - observed), axis=1))))
        return rotation, translation, residual

    trials = []
    cluster_diagnostics = []
    for trial_index, observations in enumerate(marker_trials):
        rows = {name: {"rotation": [], "translation": []} for name in required}
        sample_count = min(len(observations[name]) for name in observations)
        residuals = {body: [] for body in required}
        for row_index in range(sample_count):
            poses = {}
            for body_name in required:
                names = cluster_markers[body_name]["markers"]
                local = np.vstack([model_markers[name]["location_m"] for name in names])
                observed = np.vstack([observations[name][row_index] for name in names])
                if not np.all(np.isfinite(observed)):
                    break
                rotation, translation, residual = rigid_pose(local, observed)
                if residual > float(rigid_cluster_residual_threshold_m):
                    break
                poses[body_name] = (rotation, translation, residual)
            if len(poses) != len(required):
                continue
            for body_name, (rotation, translation, residual) in poses.items():
                rows[body_name]["rotation"].append(rotation)
                rows[body_name]["translation"].append(translation)
                residuals[body_name].append(residual)
        if any(len(rows[body]["rotation"]) < 3 for body in required):
            raise ValueError(
                "functional_unidentifiable: insufficient rigid-cluster frames in trial {}".format(
                    trial_index + 1
                )
            )
        trials.append(rows)
        cluster_diagnostics.append(
            {
                "trial": trial_index + 1,
                "retained_frame_count": len(rows["pelvis"]["rotation"]),
                "maximum_rigid_cluster_residual_m": max(
                    max(values) for values in residuals.values() if values
                ),
            }
        )
    if len(trials) < 2:
        raise ValueError("functional calibration requires at least two dynamic trials")

    score_by_side = {}
    sara_by_side = {}
    for side in ("r", "l"):
        pelvis_rotations = np.concatenate(
            [np.asarray(trial["pelvis"]["rotation"]) for trial in trials]
        )
        pelvis_translations = np.concatenate(
            [np.asarray(trial["pelvis"]["translation"]) for trial in trials]
        )
        femur_rotations = np.concatenate(
            [np.asarray(trial["femur_{}".format(side)]["rotation"]) for trial in trials]
        )
        femur_translations = np.concatenate(
            [np.asarray(trial["femur_{}".format(side)]["translation"]) for trial in trials]
        )
        score_by_side[side] = score_joint_center(
            pelvis_rotations,
            pelvis_translations,
            femur_rotations,
            femur_translations,
            bootstrap_iterations=int(bootstrap_iterations),
            seed=int(seed) + (0 if side == "r" else 1),
            threshold_m=float(score_threshold_m),
        )
        trial_axes = []
        for trial in trials:
            femur = np.asarray(trial["femur_{}".format(side)]["rotation"])
            tibia = np.asarray(trial["tibia_{}".format(side)]["rotation"])
            trial_axes.append(
                _functional_trial_axis(
                    [first.T @ second for first, second in zip(femur, tibia)]
                ).tolist()
            )
        sara_by_side[side] = sara_axis_repeatability(
            trial_axes, threshold_deg=float(sara_threshold_deg)
        )
        sara_by_side[side]["trial_axes"] = trial_axes
    score_result = {
        "identifiable": all(raw["identifiable"] for raw in score_by_side.values()),
        "sides": score_by_side,
        "maximum_bootstrap_residual_p95_m": max(
            raw["bootstrap_residual_p95_m"] for raw in score_by_side.values()
        ),
        "threshold_m": float(score_threshold_m),
    }
    sara_result = {
        "identifiable": all(raw["identifiable"] for raw in sara_by_side.values()),
        "sides": sara_by_side,
        "maximum_trial_axis_difference_deg": max(
            raw["maximum_trial_axis_difference_deg"] for raw in sara_by_side.values()
        ),
        "threshold_deg": float(sara_threshold_deg),
    }
    result = functional_identifiability(score_result, sara_result)
    result["motion_sha256"] = {
        "{}:{}".format(index, Path(path).name): sha256_file(Path(path))
        for index, path in enumerate(motion_paths, start=1)
    }
    result["marker_sha256"] = {
        "{}:{}".format(index, Path(path).name): sha256_file(Path(path))
        for index, path in enumerate(marker_paths, start=1)
    }
    result["marker_clusters"] = cluster_markers
    result["rigid_cluster_diagnostics"] = cluster_diagnostics
    result["ik_body_transforms_used"] = False
    result["method"] = "independent-marker-cluster-SCoRE-and-SARA"
    return result


def apply_identifiable_functional_anatomy(
    model_path: Path,
    output_path: Path,
    identifiability: Mapping[str, Any],
) -> Dict[str, Any]:
    """Transfer only quality-gated bilateral SCoRE centers and SARA axes."""
    if identifiability.get("functional_unidentifiable") is not False:
        raise ValueError("functional anatomy cannot be applied when its gate failed")
    tree = ET.parse(str(model_path))
    joints = {
        str(element.get("name", "")): element
        for element in tree.getroot().iter()
        if element.tag.endswith("Joint") and element.get("name")
    }
    rows = []
    for side in ("r", "l"):
        hip_name = "hip_{}".format(side)
        hip = joints.get(hip_name)
        if hip is None:
            raise ValueError("model lacks {} for functional transfer".format(hip_name))
        score = identifiability["score"]["sides"][side]
        for property_name, values in (
            ("location_in_parent", score["center_in_segment_a_m"]),
            ("location", score["center_in_segment_b_m"]),
        ):
            element = hip.find(property_name)
            if element is None:
                raise ValueError("{} lacks {}".format(hip_name, property_name))
            old = str(element.text or "").strip()
            element.text = " ".join("{:.12g}".format(float(value)) for value in values)
            rows.append(
                {
                    "joint": hip_name,
                    "property": property_name,
                    "before": old,
                    "after": element.text,
                    "source": "SCoRE",
                }
            )
        knee_name = "walker_knee_{}".format(side)
        knee = joints.get(knee_name)
        if knee is None:
            raise ValueError("model lacks {} for functional transfer".format(knee_name))
        axes = list(knee.iter("TransformAxis"))
        transform_axis = next(
            (axis for axis in axes if str(axis.get("name", "")) == "rotation1"),
            axes[0] if axes else None,
        )
        axis_element = None if transform_axis is None else transform_axis.find("axis")
        if axis_element is None:
            raise ValueError("{} has no transform axis".format(knee_name))
        values = identifiability["sara"]["sides"][side]["mean_axis"]
        old = str(axis_element.text or "").strip()
        axis_element.text = " ".join("{:.12g}".format(float(value)) for value in values)
        rows.append(
            {
                "joint": knee_name,
                "property": "axis:{}".format(transform_axis.get("name", "")),
                "before": old,
                "after": axis_element.text,
                "source": "SARA",
            }
        )
    output_path.parent.mkdir(parents=True, exist_ok=True)
    tree.write(str(output_path), encoding="utf-8", xml_declaration=True)
    return {
        "input_model_sha256": sha256_file(model_path),
        "output_model_sha256": sha256_file(output_path),
        "transferred_property_count": len(rows),
        "properties": rows,
    }


def normalize_trc_time_header(source: Path, output: Path) -> Dict[str, Any]:
    """Materialize an AddBiomechanics-compatible TRC without changing samples.

    The public running benchmark labels its second column ``Times``.  TRC and
    AddBiomechanics use the singular ``Time`` token.  This byte-level repair is
    deliberately limited to that header token and is recorded in provenance.
    """
    source = source.resolve()
    content = source.read_bytes()
    plural = b"Frame#\tTimes\t"
    singular = b"Frame#\tTime\t"
    plural_count = content.count(plural)
    singular_count = content.count(singular)
    if plural_count == 1 and singular_count == 0:
        normalized = content.replace(plural, singular, 1)
        operation = "replace-Frame#-Times-with-Time"
    elif singular_count == 1 and plural_count == 0:
        normalized = content
        operation = "copy-standard-Time-header"
    else:
        raise ValueError(
            "TRC must contain exactly one Frame#/Time or Frame#/Times header"
        )
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_bytes(normalized)
    return {
        "source_path": str(source),
        "source_sha256": sha256_file(source),
        "output_path": str(output.resolve()),
        "output_sha256": sha256_file(output),
        "operation": operation,
        "sample_bytes_modified": False,
        "byte_length_delta": len(normalized) - len(content),
    }


def prepare_addbiomechanics_subject(
    plan: Mapping[str, Any], model_path: Path, subject_dir: Path
) -> Dict[str, Any]:
    """Materialize only allowed fit inputs in the official v2.2 folder layout."""
    validate_fit_plan(plan)
    if subject_dir.exists() and any(subject_dir.iterdir()):
        raise ValueError("AddBiomechanics subject directory must be empty")
    subject_dir.mkdir(parents=True, exist_ok=True)
    shutil.copy2(str(model_path), str(subject_dir / "unscaled_generic.osim"))
    anthropometry = plan["anthropometry"]
    subject_json = {
        "massKg": float(anthropometry["mass_kg"]),
        "heightM": float(anthropometry["height_m"]),
        "sex": str(anthropometry.get("sex", "unknown")),
        "skeletonPreset": "custom",
        "disableDynamics": False,
        "footBodyNames": ["calcn_r", "calcn_l"],
    }
    (subject_dir / "_subject.json").write_text(
        json.dumps(subject_json, indent=2, sort_keys=True) + "\n", encoding="utf-8"
    )
    trial_rows = []
    inputs = plan["inputs"]
    for trial in plan["trials"]:
        speed_raw = trial.get("speed_m_s")
        speed = None if speed_raw is None else float(speed_raw)
        trial_id = str(trial.get("trial_id") or "speed_{:.2f}".format(speed))
        safe_trial_id = re.sub(r"[^A-Za-z0-9_-]+", "_", trial_id).strip("_")
        if not safe_trial_id:
            raise ValueError("trial_id cannot be empty after path sanitization")
        directory = subject_dir / "trials" / safe_trial_id
        directory.mkdir(parents=True, exist_ok=True)
        marker = Path(inputs[trial["markers_asset_id"]]["path"])
        grf = Path(inputs[trial["ground_reaction_asset_id"]]["path"])
        trc_normalization = normalize_trc_time_header(
            marker, directory / "markers.trc"
        )
        shutil.copy2(str(grf), str(directory / "grf.mot"))
        trial_rows.append(
            {
                "trial_id": trial_id,
                "speed_m_s": speed,
                "marker_sha256": sha256_file(directory / "markers.trc"),
                "grf_sha256": sha256_file(directory / "grf.mot"),
                "trc_normalization": trc_normalization,
            }
        )
    return {
        "subject_dir": str(subject_dir),
        "subject_json_sha256": sha256_file(subject_dir / "_subject.json"),
        "model_sha256": sha256_file(subject_dir / "unscaled_generic.osim"),
        "trials": trial_rows,
        "cloud_upload": False,
    }


def addbiomechanics_docker_command(
    plan: Mapping[str, Any], subject_dir: Path, output_name: str = "osim_results"
) -> List[str]:
    validate_fit_plan(plan)
    engine = plan["engine"]
    return [
        "docker",
        "run",
        "--rm",
        "--platform",
        str(engine["container_platform"]),
        "--network",
        "none",
        "-v",
        "{}:/subject".format(subject_dir.resolve()),
        str(engine["container_image"]),
        "python3",
        "/opt/AddBiomechanics/server/engine/src/engine.py",
        "/subject",
        str(output_name),
    ]


def seal_fit_result(
    plan: Mapping[str, Any], worker_report: Mapping[str, Any], model_path: Path
) -> Dict[str, Any]:
    """Freeze the candidate before hidden artifacts can be opened."""
    validate_fit_plan(plan)
    model_path = model_path.resolve()
    if not model_path.is_file():
        raise ValueError("candidate model is absent")
    report_hash = canonical_sha256(worker_report)
    seal = {
        "schema_version": DYNAMIC_PERSONALIZATION_SCHEMA_VERSION,
        "scope": "g6d-personalization-seal",
        "subject_id": plan["subject_id"],
        "split": plan["split"],
        "plan_sha256": plan.get("plan_sha256") or canonical_sha256(plan),
        "candidate_model_sha256": sha256_file(model_path),
        "worker_report_sha256": report_hash,
        "engine_identity": worker_report.get("engine_identity"),
        "input_sha256": plan["input_sha256"],
        "forbidden_input_attestation": plan["forbidden_input_attestation"],
        "sealed_before_hidden_evaluation": True,
    }
    seal["seal_sha256"] = canonical_sha256(seal)
    return seal


def build_hidden_evaluation_plan(
    protocol_path: Path,
    inventory_path: Path,
    data_root: Path,
    seal: Mapping[str, Any],
    candidate_model: Path,
) -> Dict[str, Any]:
    """Open target hidden assets only after verifying an immutable fit seal."""
    protocol = _load_yaml(protocol_path.resolve())
    audit = validate_personalization_inventory(inventory_path, data_root)
    if not audit["pass"]:
        raise ValueError("hidden evaluator inventory failed verification")
    if seal.get("scope") != "g6d-personalization-seal" or seal.get(
        "sealed_before_hidden_evaluation"
    ) is not True:
        raise ValueError("hidden evaluator requires a valid pre-evaluation seal")
    if canonical_sha256({key: value for key, value in seal.items() if key != "seal_sha256"}) != seal.get(
        "seal_sha256"
    ):
        raise ValueError("fit seal content changed")
    candidate_model = candidate_model.resolve()
    if sha256_file(candidate_model) != seal.get("candidate_model_sha256"):
        raise ValueError("candidate model changed after sealing")
    subject = _normalize_subject(seal["subject_id"])
    assets = audit["assets"]
    if seal.get("split") == "study31":
        hidden_roles = [
            "study31_t2_markers",
            "study31_t2_ground_reaction",
            "study31_t3_markers",
            "study31_t3_ground_reaction",
        ]
    else:
        hidden_roles = [
            "static_markers",
            "static_derived_model",
            "heldout_markers",
            "heldout_ground_reaction",
            "heldout_ik",
            "heldout_id",
        ]
    selected = [_single_asset(assets, role, subject) for role in hidden_roles]
    inputs = {
        str(asset["asset_id"]): {
            "path": str(asset["absolute_path"]),
            "sha256": str(asset["observed_sha256"]),
            "role": str(asset["role"]),
            "subject_id": subject,
            "speed_m_s": asset.get("speed_m_s"),
        }
        for asset in selected
    }
    return {
        "schema_version": DYNAMIC_PERSONALIZATION_SCHEMA_VERSION,
        "scope": "g6e-hidden-evaluation-plan",
        "subject_id": subject,
        "split": seal["split"],
        "seal": dict(seal),
        "candidate_model": str(candidate_model),
        "candidate_model_sha256": sha256_file(candidate_model),
        "hidden_inputs": inputs,
        "hidden_input_sha256": {key: value["sha256"] for key, value in inputs.items()},
        "evaluation_trial_ids": ["T2", "T3"] if seal.get("split") == "study31" else ["4.00-m/s"],
        "thresholds": protocol.get("thresholds") or {},
        "evaluation_opened_after_seal": True,
    }


def evaluate_hidden_subject(
    evaluation_plan: Mapping[str, Any], metrics: Mapping[str, Any]
) -> Dict[str, Any]:
    """Apply all frozen scale/IK/RRA/residual/angle/moment thresholds."""
    if evaluation_plan.get("scope") != "g6e-hidden-evaluation-plan":
        raise ValueError("invalid hidden evaluation scope")
    thresholds = evaluation_plan.get("thresholds") or {}
    provenance = metrics.get("provenance") or {}
    checks = {
        "candidate_model_immutable": sha256_file(
            Path(str(evaluation_plan["candidate_model"]))
        )
        == evaluation_plan.get("candidate_model_sha256"),
        "metrics_candidate_model_bound": provenance.get("candidate_model_sha256")
        == evaluation_plan.get("candidate_model_sha256"),
        "metrics_hidden_inputs_bound": provenance.get("hidden_input_sha256")
        == evaluation_plan.get("hidden_input_sha256"),
        "separate_hidden_evaluator_identified": bool(
            str(provenance.get("evaluator_identity", "")).strip()
        ),
        "segment_scale_median": float(metrics.get("segment_scale_median_relative_error", math.inf))
        <= float(thresholds.get("segment_scale_median_relative_error", 0.02)),
        "segment_scale_p95": float(metrics.get("segment_scale_p95_relative_error", math.inf))
        <= float(thresholds.get("segment_scale_p95_relative_error", 0.05)),
        "ik_rms": float(metrics.get("ik_rms_error_m", math.inf))
        <= float(thresholds.get("ik_rms_error_m", 0.02)),
        "ik_max": float(metrics.get("ik_max_error_m", math.inf))
        <= float(thresholds.get("ik_max_error_m", 0.04)),
        "rra_angle_change": float(metrics.get("rra_angle_change_rms_deg", math.inf))
        <= float(thresholds.get("rra_angle_change_rms_deg", 5.0)),
        "residual_force": float(metrics.get("residual_force_fraction", math.inf))
        <= float(thresholds.get("residual_force_fraction", 0.05)),
        "residual_moment": float(metrics.get("residual_moment_fraction", math.inf))
        <= float(thresholds.get("residual_moment_fraction", 0.01)),
        "joint_angle_median": float(metrics.get("joint_angle_median_rmse_deg", math.inf))
        <= float(thresholds.get("joint_angle_median_rmse_deg", 5.0)),
        "joint_angle_max": float(metrics.get("joint_angle_max_rmse_deg", math.inf))
        <= float(thresholds.get("joint_angle_max_rmse_deg", 10.0)),
        "joint_moment_median": float(metrics.get("joint_moment_median_nrmse", math.inf))
        <= float(thresholds.get("joint_moment_median_nrmse", 0.20)),
        "joint_moment_max": float(metrics.get("joint_moment_max_nrmse", math.inf))
        <= float(thresholds.get("joint_moment_max_nrmse", 0.40)),
        "joint_moment_correlation": float(
            metrics.get("minimum_joint_moment_correlation", -math.inf)
        )
        >= float(thresholds.get("minimum_joint_moment_correlation", 0.90)),
    }
    return {
        "schema_version": DYNAMIC_PERSONALIZATION_SCHEMA_VERSION,
        "scope": "g6e-hidden-subject-evaluation",
        "subject_id": evaluation_plan["subject_id"],
        "pass": all(checks.values()),
        "checks": checks,
        "metrics": dict(metrics),
        "thresholds": dict(thresholds),
        "seal_sha256": evaluation_plan["seal"]["seal_sha256"],
    }


def aggregate_blind_evaluations(
    results: Sequence[Mapping[str, Any]]
) -> Dict[str, Any]:
    by_subject = {_normalize_subject(row["subject_id"]): dict(row) for row in results}
    missing = sorted(set(BLIND_SUBJECTS) - set(by_subject))
    extra = sorted(set(by_subject) - set(BLIND_SUBJECTS))
    all_pass = not missing and not extra and all(
        by_subject[subject].get("pass") is True for subject in BLIND_SUBJECTS
    )
    return {
        "schema_version": DYNAMIC_PERSONALIZATION_SCHEMA_VERSION,
        "scope": "g6e-blind-confirmation",
        "blind_subject_ids": list(BLIND_SUBJECTS),
        "subject_results": [by_subject[subject] for subject in BLIND_SUBJECTS if subject in by_subject],
        "missing_subject_ids": missing,
        "unexpected_subject_ids": extra,
        "pass": all_pass,
        "study31_authorized": all_pass,
        "claim": (
            "dynamic-only method validated on the frozen four-subject blind set"
            if all_pass
            else "dynamic-only method not validated"
        ),
    }


def study31_fixed_denominator(
    processed: Sequence[Mapping[str, Any]], expected_count: int = 336
) -> Dict[str, Any]:
    rows = [dict(row) for row in processed]
    observed_ids = {str(row.get("trial_id")) for row in rows}
    if len(observed_ids) != len(rows):
        raise ValueError("Study 31 trial ids must be unique")
    source_absent = int(expected_count) - len(rows)
    if source_absent < 0:
        raise ValueError("processed Study 31 rows exceed the frozen denominator")
    statuses: Dict[str, int] = {}
    for row in rows:
        status = str(row.get("quality_status", "failed"))
        statuses[status] = statuses.get(status, 0) + 1
    statuses["source-absent"] = source_absent
    return {
        "scope": "g6e-study31-fixed-denominator",
        "expected_trial_count": int(expected_count),
        "processed_trial_count": len(rows),
        "source_absent_count": source_absent,
        "quality_status_counts": statuses,
        "denominator_preserved": sum(statuses.values()) == int(expected_count),
        "failed_trials_removed": False,
        "rows": rows,
    }


def write_json_result(result: Mapping[str, Any], output: Path) -> None:
    output = output.resolve()
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(
        json.dumps(result, indent=2, sort_keys=True) + "\n", encoding="utf-8"
    )
