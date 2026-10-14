"""Fail-closed execution contracts for the Study 31 G6 OpenSim analysis.

This module intentionally has no OpenSim dependency.  It provides the pure
planning, checkpoint, identity, provenance, and RRA metric functions used by
both the paper runner and the OpenSim worker process.
"""

from __future__ import annotations

import hashlib
import json
import math
import re
from collections import Counter
from pathlib import Path
from typing import Any, Dict, List, Mapping, Optional, Sequence, Tuple

import numpy as np
import pandas as pd

from harmo.ingest.readers.sto import read_opensim_sto_trc


EXECUTION_SCHEMA_VERSION = "1.0.0"
REQUIRED_VALIDATION_FIELDS = (
    "ik_rms_error",
    "ik_max_error",
    "residual_force_rms",
    "ex_loads_rms",
    "residual_moment_rms",
    "ex_moment_rms",
    "rra_angle_change_rms_deg",
)
_SESSION = re.compile(r"^ses-(V(?:1|15|2|25|3|35|4))-(T[123])$")
_TRANSLATION = re.compile(r"(?:^|[/_])t[xyz](?:$|[/_])", re.I)
_SHA256 = re.compile(r"^[0-9a-f]{64}$")
_CHECKPOINT_ARTIFACTS = (
    ("scaled_model_file", "scaled_model_sha256"),
    ("rra_kinematics_file", "rra_kinematics_sha256"),
    ("rra_actuation_file", "rra_actuation_sha256"),
    ("rra_adjusted_model_file", "rra_adjusted_model_sha256"),
)


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def trial_identity(value: Mapping[str, Any]) -> Tuple[str, str, str]:
    """Return and cross-check ``(subject, speed, trial)``."""
    subject = str(value.get("subject_id", "")).strip()
    session = str(value.get("session_id", "")).strip()
    match = _SESSION.fullmatch(session)
    if not subject or match is None:
        raise ValueError("invalid G6 subject/session identity")
    speed = str(value.get("speed") or match.group(1)).strip()
    trial = str(value.get("trial") or match.group(2)).strip()
    if speed != match.group(1) or trial != match.group(2):
        raise ValueError("speed/trial conflicts with session_id")
    return subject, speed, trial


def expected_identities(experiment: Mapping[str, Any]) -> List[Tuple[str, str, str]]:
    return [
        (str(subject), str(speed), str(trial))
        for subject in experiment["subjects"]
        for speed in experiment["speeds"]
        for trial in experiment["trials"]
    ]


def validation_complete(
    value: Mapping[str, Any],
    expected: Optional[Tuple[str, str, str]] = None,
    expected_provenance: Optional[Mapping[str, Any]] = None,
) -> Tuple[bool, List[str]]:
    """Check identity and all raw inputs required for the five G6 metrics."""
    reasons: List[str] = []
    try:
        identity = trial_identity(value)
    except ValueError as exc:
        identity = None
        reasons.append(str(exc))
    if expected is not None and identity != expected:
        reasons.append("identity-mismatch")
    if str(value.get("study_id")) != "study31":
        reasons.append("study-id-mismatch")
    for field in REQUIRED_VALIDATION_FIELDS:
        raw = value.get(field)
        if isinstance(raw, bool):
            reasons.append("{}-invalid".format(field))
            continue
        try:
            numeric = float(raw)
        except (TypeError, ValueError):
            reasons.append("{}-missing".format(field))
            continue
        if not math.isfinite(numeric):
            reasons.append("{}-nonfinite".format(field))
        elif field in {"ex_loads_rms", "ex_moment_rms"} and numeric <= 0:
            reasons.append("{}-nonpositive".format(field))
        elif numeric < 0:
            reasons.append("{}-negative".format(field))
    provenance = value.get("g6_provenance")
    if not isinstance(provenance, Mapping):
        reasons.append("g6-provenance-missing")
    else:
        for field in (
            "run_id",
            "source_manifest_sha256",
            "scaling_source_kind",
            "static_trial_sha256",
            "scaled_model_sha256",
            "scaled_model_file",
            "rra_kinematics_sha256",
            "rra_kinematics_file",
            "rra_actuation_sha256",
            "rra_actuation_file",
            "rra_adjusted_model_sha256",
            "rra_adjusted_model_file",
        ):
            if not str(provenance.get(field, "")).strip():
                reasons.append("g6-provenance-{}-missing".format(field))
        for field in (
            "source_manifest_sha256",
            "static_trial_sha256",
            "scaled_model_sha256",
            "rra_kinematics_sha256",
            "rra_actuation_sha256",
            "rra_adjusted_model_sha256",
        ):
            if not _SHA256.fullmatch(str(provenance.get(field, ""))):
                reasons.append("g6-provenance-{}-invalid".format(field))
        for field, expected_value in (expected_provenance or {}).items():
            if str(provenance.get(field, "")) != str(expected_value):
                reasons.append("g6-provenance-{}-mismatch".format(field))
    if value.get("residual_force_source") != "final-rra-actuation-force":
        reasons.append("residual-force-source-invalid")
    if value.get("residual_moment_source") != "final-rra-actuation-force":
        reasons.append("residual-moment-source-invalid")
    if value.get("downstream_kinematics_source") != "final-rra-kinematics":
        reasons.append("downstream-kinematics-source-invalid")
    if value.get("downstream_model_source") != "final-rra-adjusted-model":
        reasons.append("downstream-model-source-invalid")
    try:
        rra_pass_count = int(value.get("rra_pass_count"))
    except (TypeError, ValueError):
        rra_pass_count = None
    if rra_pass_count != 2:
        reasons.append("rra-pass-count-not-two")
    reasons = sorted(set(reasons))
    return not reasons, reasons


def checkpoint_status(
    path: Path,
    expected: Tuple[str, str, str],
    expected_provenance: Optional[Mapping[str, Any]] = None,
) -> Tuple[str, List[str]]:
    if not path.is_file():
        return "pending", ["validation-file-missing"]
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return "pending", ["validation-file-unreadable"]
    if not isinstance(value, Mapping):
        return "pending", ["validation-file-not-object"]
    complete, reasons = validation_complete(value, expected, expected_provenance)
    if complete:
        provenance = value["g6_provenance"]
        for file_field, digest_field in _CHECKPOINT_ARTIFACTS:
            artifact_name = str(provenance[file_field])
            label = file_field[:-5].replace("_", "-")
            if Path(artifact_name).name != artifact_name:
                reasons.append("{}-artifact-path-invalid".format(label))
                continue
            artifact_path = path.parent / artifact_name
            if not artifact_path.is_file():
                reasons.append("{}-artifact-missing".format(label))
            elif sha256_file(artifact_path) != provenance[digest_field]:
                reasons.append("{}-artifact-checksum-mismatch".format(label))
        complete = not reasons
    return ("resume-complete", []) if complete else ("pending", reasons)


def source_map_from_audit(audit: Mapping[str, Any]) -> Dict[str, Dict[str, Any]]:
    """Return accepted source rows, retaining checksum-observed artifacts."""
    if audit.get("pass") is not True:
        raise ValueError("G6 scaling source audit did not pass")
    bundle_root = Path(str(audit.get("bundle_root", ""))).resolve()
    rows: Dict[str, Dict[str, Any]] = {}
    for raw in audit.get("subjects", []):
        if not isinstance(raw, Mapping) or raw.get("status") != "accepted":
            raise ValueError("G6 source audit contains a non-accepted subject")
        subject = str(raw.get("subject_id", ""))
        artifacts: Dict[str, Dict[str, Any]] = {}
        for artifact in raw.get("artifacts", []):
            if not isinstance(artifact, Mapping) or artifact.get("pass") is not True:
                raise ValueError("G6 accepted source has an invalid artifact")
            item = dict(artifact)
            item["absolute_path"] = str(bundle_root / str(item["path"]))
            artifacts[str(item["role"])] = item
        row = dict(raw)
        row["artifacts_by_role"] = artifacts
        rows[subject] = row
    expected = {str(value) for value in audit.get("expected_subjects", [])}
    if set(rows) != expected:
        raise ValueError("G6 source audit subject set is incomplete")
    return rows


def build_execution_plan(
    registry: Mapping[str, Any],
    source_ledger: Sequence[Mapping[str, Any]],
    source_audit: Mapping[str, Any],
    results_dir: Path,
    run_id: str,
    resume: bool = True,
) -> Dict[str, Any]:
    """Build the immutable 336-row plan; six absent trials remain explicit."""
    experiment = registry["experiments"]["E"]
    expected = expected_identities(experiment)
    expected_set = set(expected)
    by_identity: Dict[Tuple[str, str, str], Mapping[str, Any]] = {}
    duplicates: List[Tuple[str, str, str]] = []
    invalid_rows = 0
    for row in source_ledger:
        try:
            identity = trial_identity(row)
        except ValueError:
            invalid_rows += 1
            continue
        if identity in by_identity:
            duplicates.append(identity)
        else:
            by_identity[identity] = row

    source_map = source_map_from_audit(source_audit)
    rows: List[Dict[str, Any]] = []
    for identity in expected:
        subject, speed, trial = identity
        session_id = "ses-{}-{}".format(speed, trial)
        source = by_identity.get(identity)
        source_status = str((source or {}).get("source_status", "missing"))
        validation_path = results_dir / "study31_{}_{}_validation.json".format(
            subject, session_id
        )
        if source_status == "source-absent":
            status, reasons = "source-absent", []
        elif source_status != "available":
            status, reasons = "blocked", ["dynamic-source-{}".format(source_status)]
        elif resume:
            source_row = source_map[subject]
            static = source_row["artifacts_by_role"]["static_trial"]
            expected_provenance = {
                "source_manifest_sha256": source_audit.get("manifest_sha256"),
                "scaling_source_kind": source_row["source_kind"],
                "static_trial_sha256": static["observed_sha256"],
            }
            if source_row["source_kind"] == "traceable-subject-specific-model":
                expected_provenance["scaled_model_sha256"] = source_row[
                    "artifacts_by_role"
                ]["scaled_model"]["observed_sha256"]
            status, reasons = checkpoint_status(
                validation_path, identity, expected_provenance
            )
        else:
            status, reasons = "pending", []
        rows.append(
            {
                "study_id": "study31",
                "subject_id": subject,
                "speed": speed,
                "trial": trial,
                "session_id": session_id,
                "source_status": source_status,
                "execution_status": status,
                "reason_codes": reasons,
                "validation_path": str(validation_path),
                "scaling_source_kind": source_map[subject]["source_kind"],
            }
        )

    observed_set = set(by_identity)
    observed_absent = {
        identity
        for identity, source in by_identity.items()
        if str(source.get("source_status")) == "source-absent"
    }
    frozen_absent = {
        (
            str(row["subject_id"]),
            str(row["speed"]),
            str(row["trial"]),
        )
        for row in experiment.get("source_absent_trials", [])
    }
    audit_expected = {str(value) for value in experiment["subjects"]}
    checks = {
        "frozen_denominator": len(rows) == int(experiment["denominator"]),
        "ledger_identities_exact": observed_set == expected_set,
        "ledger_identities_unique": not duplicates and invalid_rows == 0,
        "scaling_source_audit_pass": source_audit.get("pass") is True,
        "scaling_subjects_exact": set(source_map) == audit_expected,
        "source_statuses_resolved": all(
            row["source_status"] in {"available", "source-absent"} for row in rows
        ),
        "source_absence_identities_exact": observed_absent == frozen_absent,
        "analysis_denominator_330": sum(
            row["source_status"] == "available" for row in rows
        )
        == 330,
    }
    counts = Counter(row["execution_status"] for row in rows)
    return {
        "schema_version": EXECUTION_SCHEMA_VERSION,
        "scope": "study31-g6-opensim-execution-plan",
        "run_id": run_id,
        "source_manifest_sha256": source_audit.get("manifest_sha256"),
        "counts": {
            "frozen": len(rows),
            "available": sum(row["source_status"] == "available" for row in rows),
            "source_absent": sum(
                row["source_status"] == "source-absent" for row in rows
            ),
            "pending": counts.get("pending", 0),
            "resume_complete": counts.get("resume-complete", 0),
            "blocked": counts.get("blocked", 0),
        },
        "checks": checks,
        "pass": all(checks.values()),
        "trials": rows,
    }


def enrich_validation(
    path: Path,
    trial: Mapping[str, Any],
    run_id: str,
    source_audit: Mapping[str, Any],
    source_row: Mapping[str, Any],
    scaled_model_path: Path,
) -> Dict[str, Any]:
    """Add exact G6 identity and checksum-bound scaling provenance in place."""
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise ValueError("validation JSON must be an object")
    subject, speed, trial_name = trial_identity(trial)
    artifacts = source_row["artifacts_by_role"]
    static = artifacts["static_trial"]
    prefix = "study31_{}_ses-{}-{}".format(subject, speed, trial_name)
    value.update(
        {
            "study_id": "study31",
            "subject_id": subject,
            "session_id": "ses-{}-{}".format(speed, trial_name),
            "speed": speed,
            "trial": trial_name,
            "run_id": run_id,
            "g6_provenance": {
                "run_id": run_id,
                "source_manifest_sha256": source_audit["manifest_sha256"],
                "scaling_source_kind": source_row["source_kind"],
                "static_trial_id": source_row["provenance"]["static_trial_id"],
                "static_trial_sha256": static["observed_sha256"],
                "scaled_model_sha256": sha256_file(scaled_model_path),
                "scaled_model_file": scaled_model_path.name,
                "rra_kinematics_sha256": value.get("rra_kinematics_sha256"),
                "rra_kinematics_file": "{}_RRA_Kinematics_q.sto".format(prefix),
                "rra_actuation_sha256": value.get("rra_actuation_sha256"),
                "rra_actuation_file": "{}_RRA_Actuation_force.sto".format(prefix),
                "rra_adjusted_model_sha256": value.get(
                    "rra_adjusted_model_sha256"
                ),
                "rra_adjusted_model_file": "{}_RRA_AdjustedModel.osim".format(
                    prefix
                ),
            },
        }
    )
    complete, reasons = validation_complete(value, (subject, speed, trial_name))
    if not complete:
        raise ValueError("G6 validation incomplete: {}".format(",".join(reasons)))
    path.write_text(json.dumps(value, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    return value


def _in_degrees(header: pd.DataFrame) -> bool:
    if header.empty:
        return False
    lookup = {str(key).lower(): value for key, value in header.iloc[0].items()}
    return str(lookup.get("indegrees", "no")).strip().lower() in {
        "yes",
        "true",
        "1",
    }


def rra_angle_change_metrics(ik_path: Path, rra_path: Path) -> Dict[str, Any]:
    """Calculate RMS angular change from IK to final RRA kinematics.

    Translation coordinates are excluded.  RRA samples are linearly
    interpolated to IK timestamps over their common time interval.
    """
    ik_header, ik = read_opensim_sto_trc(str(ik_path))
    rra_header, rra = read_opensim_sto_trc(str(rra_path))
    if "time" not in ik or "time" not in rra:
        raise ValueError("IK/RRA kinematics require a time column")
    common = sorted(
        column
        for column in set(ik.columns).intersection(rra.columns)
        if column != "time" and not _TRANSLATION.search(column)
    )
    if not common:
        raise ValueError("IK/RRA kinematics have no common angular coordinates")
    ik_time = pd.to_numeric(ik["time"], errors="coerce").to_numpy(float)
    rra_time = pd.to_numeric(rra["time"], errors="coerce").to_numpy(float)
    if not np.all(np.isfinite(ik_time)) or not np.all(np.isfinite(rra_time)):
        raise ValueError("IK/RRA time columns must be finite")
    if np.any(np.diff(ik_time) <= 0) or np.any(np.diff(rra_time) <= 0):
        raise ValueError("IK/RRA time columns must be strictly increasing")
    lower = max(float(np.nanmin(ik_time)), float(np.nanmin(rra_time)))
    upper = min(float(np.nanmax(ik_time)), float(np.nanmax(rra_time)))
    keep = np.isfinite(ik_time) & (ik_time >= lower) & (ik_time <= upper)
    if int(keep.sum()) < 2:
        raise ValueError("IK/RRA kinematics have insufficient temporal overlap")
    differences = []
    used_coordinates = []
    for column in common:
        ik_values = pd.to_numeric(ik[column], errors="coerce").to_numpy(float)
        rra_values = pd.to_numeric(rra[column], errors="coerce").to_numpy(float)
        valid_rra = np.isfinite(rra_time) & np.isfinite(rra_values)
        if int(valid_rra.sum()) < 2:
            continue
        interpolated = np.interp(ik_time[keep], rra_time[valid_rra], rra_values[valid_rra])
        base = ik_values[keep]
        if not _in_degrees(ik_header):
            base = np.degrees(base)
        if not _in_degrees(rra_header):
            interpolated = np.degrees(interpolated)
        diff = interpolated - base
        finite = diff[np.isfinite(diff)]
        if finite.size:
            differences.extend(finite.tolist())
            used_coordinates.append(column)
    if not differences:
        raise ValueError("IK/RRA kinematics have no finite paired angular values")
    array = np.asarray(differences, dtype=float)
    return {
        "rra_angle_change_rms_deg": float(np.sqrt(np.mean(np.square(array)))),
        "rra_angle_coordinate_count": len(used_coordinates),
        "rra_angle_value_count": int(array.size),
        "rra_ik_sha256": sha256_file(ik_path),
        "rra_kinematics_sha256": sha256_file(rra_path),
    }


def _rra_actuator_columns(data: pd.DataFrame, names: Sequence[str]) -> List[str]:
    by_name: Dict[str, List[str]] = {}
    for column in data.columns:
        terminal = str(column).strip().replace("\\", "/").split("/")[-1].upper()
        by_name.setdefault(terminal, []).append(str(column))
    resolved = []
    for name in names:
        matches = by_name.get(name.upper(), [])
        if len(matches) != 1:
            raise ValueError(
                "RRA actuation must contain exactly one {} column".format(name)
            )
        resolved.append(matches[0])
    return resolved


def rra_residual_metrics(actuation_path: Path) -> Dict[str, Any]:
    """Compute final-pass RRA residual vector RMS from FX/FY/FZ and MX/MY/MZ."""
    _, data = read_opensim_sto_trc(str(actuation_path))
    force_columns = _rra_actuator_columns(data, ("FX", "FY", "FZ"))
    moment_columns = _rra_actuator_columns(data, ("MX", "MY", "MZ"))
    force = data[force_columns].apply(pd.to_numeric, errors="coerce").to_numpy(float)
    moment = data[moment_columns].apply(pd.to_numeric, errors="coerce").to_numpy(float)
    if force.size == 0 or moment.size == 0:
        raise ValueError("RRA actuation has no residual samples")
    if not np.all(np.isfinite(force)) or not np.all(np.isfinite(moment)):
        raise ValueError("RRA residual samples must all be finite")
    force_norm = np.linalg.norm(force, axis=1)
    moment_norm = np.linalg.norm(moment, axis=1)
    force_rms = float(np.sqrt(np.mean(np.square(force_norm))))
    moment_rms = float(np.sqrt(np.mean(np.square(moment_norm))))
    digest = sha256_file(actuation_path)
    return {
        "residual_force_rms": force_rms,
        "residual_moment_rms": moment_rms,
        "rra_residual_force_rms": force_rms,
        "rra_residual_moment_rms": moment_rms,
        "residual_force_source": "final-rra-actuation-force",
        "residual_moment_source": "final-rra-actuation-force",
        "rra_actuation_sha256": digest,
        "rra_residual_sample_count": int(force.shape[0]),
    }
