"""Checksum-bound intake for subject-specific OpenSim scaling sources.

The G6 confirmatory analysis may use either an original static calibration
trial or an already scaled model.  A scaled model is admissible only when the
exact static trial and every material scaling input/output remain available
and checksum-bound.  This module deliberately rejects undocumented legacy
models rather than inferring their lineage from filenames.
"""

from __future__ import annotations

import hashlib
import re
import xml.etree.ElementTree as ET
from collections import Counter
from datetime import datetime
from pathlib import Path, PurePosixPath
from typing import Any, Dict, List, Mapping, Optional, Sequence, Tuple

import yaml


INTAKE_SCHEMA_VERSION = "1.1.0"
STATIC_SOURCE = "static-calibration"
MODEL_SOURCE = "traceable-subject-specific-model"
ALLOWED_SOURCE_KINDS = (STATIC_SOURCE, MODEL_SOURCE)

_ARTIFACT_RULES = {
    "static_trial": {".c3d", ".trc"},
    "generic_model": {".osim"},
    "scale_setup": {".xml"},
    "scale_result": {".xml"},
    "marker_set": {".xml"},
    "scaled_model": {".osim"},
}
_MODEL_ARTIFACTS = (
    "static_trial",
    "generic_model",
    "scale_setup",
    "scale_result",
    "marker_set",
    "scaled_model",
)
_PROVENANCE_FIELDS = (
    "static_trial_id",
    "custodian",
    "acquisition_basis",
)
_GENERATION_FIELDS = (
    "software",
    "software_version",
    "generated_by",
    "generated_at",
    "source_static_trial_id",
)
_SHA256 = re.compile(r"^[0-9a-f]{64}$")
_LEGACY_MODEL = re.compile(r"(?:^|_)31_(\d+)(?:_|$)", re.I)
_STATIC_UNITS = {"mm", "cm", "m"}
_STATIC_COORDINATE_SYSTEMS = {"data_31_rule", "opensim"}


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        while True:
            block = stream.read(1024 * 1024)
            if not block:
                break
            digest.update(block)
    return digest.hexdigest()


def _nonempty(value: Any) -> bool:
    return (
        isinstance(value, str)
        and bool(value.strip())
        and not value.strip().upper().startswith("REPLACE_WITH")
    )


def _iso8601(value: Any) -> bool:
    if not _nonempty(value):
        return False
    try:
        parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except ValueError:
        return False
    return parsed.tzinfo is not None


def _safe_artifact_path(bundle_root: Path, value: Any) -> Tuple[Optional[Path], str]:
    if not _nonempty(value):
        return None, "path-missing"
    relative = PurePosixPath(str(value))
    if relative.is_absolute() or ".." in relative.parts:
        return None, "path-outside-bundle"
    candidate = bundle_root.joinpath(*relative.parts)
    cursor = bundle_root
    for part in relative.parts:
        cursor = cursor / part
        if cursor.is_symlink():
            return candidate, "symlink-not-allowed"
    try:
        resolved_root = bundle_root.resolve(strict=True)
        resolved = candidate.resolve(strict=True)
    except FileNotFoundError:
        return candidate, "file-missing"
    try:
        resolved.relative_to(resolved_root)
    except ValueError:
        return candidate, "path-outside-bundle"
    if candidate.is_symlink():
        return candidate, "symlink-not-allowed"
    if not resolved.is_file():
        return candidate, "not-a-regular-file"
    return resolved, "ok"


def _artifact_record(
    role: str, value: Any, bundle_root: Path
) -> Tuple[Dict[str, Any], List[str]]:
    record: Dict[str, Any] = {
        "role": role,
        "path": None,
        "expected_size_bytes": None,
        "observed_size_bytes": None,
        "expected_sha256": None,
        "observed_sha256": None,
        "path_status": "not-checked",
        "extension_pass": False,
        "size_pass": False,
        "checksum_pass": False,
        "coordinate_unit": None,
        "coordinate_system_id": None,
        "sampling_rate_hz": None,
        "metadata_pass": role != "static_trial",
        "pass": False,
    }
    reasons: List[str] = []
    if not isinstance(value, Mapping):
        reasons.append("{}-record-missing".format(role))
        return record, reasons

    record["path"] = value.get("path")
    record["expected_size_bytes"] = value.get("size_bytes")
    expected_hash = str(value.get("sha256", "")).strip().lower()
    record["expected_sha256"] = expected_hash or None
    path, path_status = _safe_artifact_path(bundle_root, value.get("path"))
    record["path_status"] = path_status
    if path_status != "ok" or path is None:
        reasons.append("{}-{}".format(role, path_status))
        return record, reasons

    suffix = path.suffix.lower()
    record["extension_pass"] = suffix in _ARTIFACT_RULES[role]
    if not record["extension_pass"]:
        reasons.append("{}-extension-invalid".format(role))

    observed_size = int(path.stat().st_size)
    record["observed_size_bytes"] = observed_size
    expected_size = value.get("size_bytes")
    record["size_pass"] = (
        isinstance(expected_size, int)
        and not isinstance(expected_size, bool)
        and expected_size >= 0
        and expected_size == observed_size
    )
    if not record["size_pass"]:
        reasons.append("{}-size-mismatch".format(role))

    if not _SHA256.fullmatch(expected_hash):
        reasons.append("{}-sha256-invalid".format(role))
    else:
        observed_hash = _sha256(path)
        record["observed_sha256"] = observed_hash
        record["checksum_pass"] = observed_hash == expected_hash
        if not record["checksum_pass"]:
            reasons.append("{}-checksum-mismatch".format(role))

    if role == "static_trial":
        unit = str(value.get("coordinate_unit", "")).strip().lower()
        coordinate_system = str(value.get("coordinate_system_id", "")).strip()
        sampling_rate = value.get("sampling_rate_hz")
        record["coordinate_unit"] = unit or None
        record["coordinate_system_id"] = coordinate_system or None
        record["sampling_rate_hz"] = sampling_rate
        metadata_pass = True
        if unit not in _STATIC_UNITS:
            reasons.append("static_trial-coordinate-unit-invalid")
            metadata_pass = False
        if coordinate_system not in _STATIC_COORDINATE_SYSTEMS:
            reasons.append("static_trial-coordinate-system-invalid")
            metadata_pass = False
        rate_valid = not isinstance(sampling_rate, bool)
        if rate_valid:
            try:
                numeric_rate = float(sampling_rate)
            except (TypeError, ValueError):
                numeric_rate = float("nan")
            rate_valid = numeric_rate > 0 and numeric_rate < float("inf")
        if not rate_valid:
            reasons.append("static_trial-sampling-rate-invalid")
            metadata_pass = False
        record["metadata_pass"] = metadata_pass

    record["pass"] = bool(
        record["extension_pass"]
        and record["size_pass"]
        and record["checksum_pass"]
        and record["metadata_pass"]
    )
    return record, reasons


def _subject_id(value: Any) -> str:
    text = str(value).strip()
    if text.lower().startswith("participant"):
        text = text[len("participant") :]
    return text


def validate_opensim_source_bundle(
    manifest_path: Path,
    bundle_root: Path,
    expected_subjects: Sequence[Any],
) -> Dict[str, Any]:
    """Validate all scaling sources without trusting filenames or mtimes."""
    manifest_path = manifest_path.resolve()
    bundle_root = bundle_root.resolve()
    expected = [_subject_id(value) for value in expected_subjects]
    global_reasons: List[str] = []
    try:
        manifest = yaml.safe_load(manifest_path.read_text(encoding="utf-8")) or {}
    except (OSError, yaml.YAMLError) as exc:
        manifest = {}
        global_reasons.append("manifest-unreadable:{}".format(type(exc).__name__))
    if not isinstance(manifest, Mapping):
        manifest = {}
        global_reasons.append("manifest-top-level-not-mapping")

    if manifest.get("schema_version") != INTAKE_SCHEMA_VERSION:
        global_reasons.append("schema-version-mismatch")
    if manifest.get("study_id") != "study31":
        global_reasons.append("study-id-mismatch")
    subject_values = manifest.get("subjects", [])
    if not isinstance(subject_values, list):
        subject_values = []
        global_reasons.append("subjects-not-list")

    indexed: Dict[str, Mapping[str, Any]] = {}
    observed_ids: List[str] = []
    duplicate_ids: List[str] = []
    invalid_records = 0
    for item in subject_values:
        if not isinstance(item, Mapping):
            invalid_records += 1
            continue
        subject = _subject_id(item.get("subject_id", ""))
        observed_ids.append(subject)
        if not subject:
            invalid_records += 1
        elif subject in indexed:
            duplicate_ids.append(subject)
        else:
            indexed[subject] = item
    if invalid_records:
        global_reasons.append("invalid-subject-record")

    rows: List[Dict[str, Any]] = []
    for subject in expected:
        item = indexed.get(subject)
        reasons: List[str] = []
        artifacts: List[Dict[str, Any]] = []
        source_kind = item.get("source_kind") if item else None
        if item is None:
            reasons.append("subject-record-missing")
        elif source_kind not in ALLOWED_SOURCE_KINDS:
            reasons.append("source-kind-invalid")

        provenance = item.get("provenance", {}) if item else {}
        if not isinstance(provenance, Mapping):
            provenance = {}
            reasons.append("provenance-not-mapping")
        for field in _PROVENANCE_FIELDS:
            if not _nonempty(provenance.get(field)):
                reasons.append("provenance-{}-missing".format(field))

        artifact_values = item.get("artifacts", {}) if item else {}
        if not isinstance(artifact_values, Mapping):
            artifact_values = {}
            reasons.append("artifacts-not-mapping")
        required_roles: Sequence[str]
        if source_kind == STATIC_SOURCE:
            required_roles = ("static_trial",)
        elif source_kind == MODEL_SOURCE:
            required_roles = _MODEL_ARTIFACTS
        else:
            required_roles = ()
        for role in required_roles:
            artifact, artifact_reasons = _artifact_record(
                role, artifact_values.get(role), bundle_root
            )
            artifacts.append(artifact)
            reasons.extend(artifact_reasons)

        generation = item.get("generation", {}) if item else {}
        if source_kind == MODEL_SOURCE:
            if not isinstance(generation, Mapping):
                generation = {}
                reasons.append("generation-not-mapping")
            for field in _GENERATION_FIELDS:
                if not _nonempty(generation.get(field)):
                    reasons.append("generation-{}-missing".format(field))
            if generation.get("software") != "OpenSim":
                reasons.append("generation-software-mismatch")
            if generation.get("software_version") != "4.5.2":
                reasons.append("generation-opensim-version-mismatch")
            if not _iso8601(generation.get("generated_at")):
                reasons.append("generation-generated_at-invalid")
            if (
                _nonempty(generation.get("source_static_trial_id"))
                and _nonempty(provenance.get("static_trial_id"))
                and generation.get("source_static_trial_id")
                != provenance.get("static_trial_id")
            ):
                reasons.append("static-trial-lineage-mismatch")

        unique_reasons = sorted(set(reasons))
        rows.append(
            {
                "subject_id": subject,
                "source_kind": source_kind,
                "status": "accepted" if not unique_reasons else "rejected",
                "reason_codes": unique_reasons,
                "provenance": dict(provenance),
                "generation": dict(generation)
                if isinstance(generation, Mapping)
                else {},
                "artifacts": artifacts,
            }
        )

    static_paths: Dict[str, List[str]] = {}
    static_hashes: Dict[str, List[str]] = {}
    for row in rows:
        for artifact in row["artifacts"]:
            if artifact["role"] != "static_trial" or not artifact["pass"]:
                continue
            path_key = str(artifact["path"])
            hash_key = str(artifact["observed_sha256"])
            static_paths.setdefault(path_key, []).append(row["subject_id"])
            static_hashes.setdefault(hash_key, []).append(row["subject_id"])
    duplicated_paths = {
        key for key, owners in static_paths.items() if len(set(owners)) > 1
    }
    duplicated_hashes = {
        key for key, owners in static_hashes.items() if len(set(owners)) > 1
    }
    for row in rows:
        for artifact in row["artifacts"]:
            if artifact["role"] != "static_trial":
                continue
            if str(artifact["path"]) in duplicated_paths:
                row["reason_codes"].append("static-trial-path-reused")
            if str(artifact["observed_sha256"]) in duplicated_hashes:
                row["reason_codes"].append("static-trial-checksum-reused")
        row["reason_codes"] = sorted(set(row["reason_codes"]))
        row["status"] = "accepted" if not row["reason_codes"] else "rejected"

    unexpected = sorted(set(observed_ids) - set(expected))
    checks = {
        "manifest_structure_valid": not global_reasons,
        "expected_subjects_exact": set(observed_ids) == set(expected),
        "subject_identities_unique": not duplicate_ids,
        "no_unexpected_subjects": not unexpected,
        "all_subjects_accepted": bool(rows)
        and all(row["status"] == "accepted" for row in rows),
    }
    return {
        "assessment_version": "1.1.0",
        "scope": "study31-subject-specific-scaling-source-intake",
        "manifest": str(manifest_path),
        "manifest_sha256": _sha256(manifest_path) if manifest_path.is_file() else None,
        "bundle_root": str(bundle_root),
        "expected_subjects": expected,
        "observed_subjects": sorted(set(observed_ids)),
        "unexpected_subjects": unexpected,
        "duplicate_subjects": sorted(set(duplicate_ids)),
        "global_reason_codes": sorted(set(global_reasons)),
        "counts": {
            "expected": len(expected),
            "observed_records": len(observed_ids),
            "accepted": sum(row["status"] == "accepted" for row in rows),
            "rejected": sum(row["status"] == "rejected" for row in rows),
        },
        "subjects": rows,
        "checks": checks,
        "pass": all(checks.values()),
    }


def audit_legacy_scaled_models(
    model_dir: Path, expected_subjects: Sequence[Any]
) -> Dict[str, Any]:
    """Inventory legacy models while making their missing lineage explicit."""
    model_dir = model_dir.resolve()
    expected = [_subject_id(value) for value in expected_subjects]
    files: List[Dict[str, Any]] = []
    subject_counts: Counter[str] = Counter()
    for path in sorted(model_dir.rglob("*scaled.osim")) if model_dir.is_dir() else []:
        match = _LEGACY_MODEL.search(path.name)
        subject = match.group(1) if match else None
        if subject:
            subject_counts[subject] += 1
        files.append(
            {
                "path": str(path.relative_to(model_dir)),
                "subject_id": subject,
                "size_bytes": int(path.stat().st_size),
                "sha256": _sha256(path),
            }
        )
    represented = sorted(subject_counts)
    missing = [subject for subject in expected if subject not in subject_counts]
    ambiguous = sorted(
        subject for subject, count in subject_counts.items() if count != 1
    )
    scope_root = model_dir.parent
    scale_setups: List[Dict[str, Any]] = []
    for setup in sorted(scope_root.rglob("*setup_scale.xml")):
        marker_references: List[Dict[str, Any]] = []
        parse_status = "pass"
        try:
            tree = ET.parse(str(setup))
            marker_files = sorted(
                set(
                    element.text.strip()
                    for element in tree.findall(".//marker_file")
                    if element.text and element.text.strip()
                )
            )
        except ET.ParseError:
            marker_files = []
            parse_status = "invalid-xml"
        for marker_file in marker_files:
            marker_path = Path(marker_file)
            resolved = (
                marker_path if marker_path.is_absolute() else setup.parent / marker_path
            )
            marker_references.append(
                {
                    "path": marker_file,
                    "present": resolved.is_file(),
                }
            )
        scale_setups.append(
            {
                "path": str(setup.relative_to(scope_root)),
                "parse_status": parse_status,
                "marker_files": marker_references,
            }
        )
    static_artifacts = sorted(
        str(path.relative_to(scope_root))
        for path in scope_root.rglob("*")
        if path.is_file()
        and path.suffix.lower() in {".c3d", ".trc"}
        and re.search(r"static|standing|calib|scale", path.name, re.I)
    )
    lineage_manifests = sorted(
        str(path.relative_to(scope_root))
        for pattern in ("*lineage*.yaml", "*lineage*.yml", "*lineage*.json")
        for path in model_dir.rglob(pattern)
        if path.is_file()
    )
    checks = {
        "model_directory_present": model_dir.is_dir(),
        "expected_subjects_represented": set(represented) == set(expected),
        "one_model_per_subject": not ambiguous and len(files) == len(expected),
        "static_trial_artifact_present": bool(static_artifacts),
        "static_trial_lineage_manifest_present": bool(lineage_manifests),
        "checksum_bound_scaling_inputs_present": bool(lineage_manifests)
        and bool(static_artifacts),
    }
    return {
        "assessment_version": "1.0.0",
        "scope": "study31-legacy-scaled-model-discovery",
        "model_dir": model_dir.name,
        "expected_subjects": expected,
        "represented_subjects": represented,
        "missing_subjects": missing,
        "ambiguous_subjects": ambiguous,
        "models": files,
        "scale_setups": scale_setups,
        "static_trial_artifacts": static_artifacts,
        "lineage_manifests": lineage_manifests,
        "counts": {
            "expected_subjects": len(expected),
            "represented_subjects": len(represented),
            "model_files": len(files),
        },
        "reason_codes": [
            "legacy-models-have-no-declared-static-trial-lineage",
            "legacy-models-have-no-checksum-bound-scaling-input-bundle",
        ],
        "checks": checks,
        "pass": all(checks.values()),
    }
