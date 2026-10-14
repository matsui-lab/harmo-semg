"""Fail-closed Study 31 OpenSim quality assessment.

The public release is the unit of provenance.  This module therefore audits
the frozen source archives before interpreting any derived OpenSim validation
JSON.  Source absence, an analysis that was not run, an incomplete metric set,
and a threshold failure remain distinct outcomes in the 336-trial ledger.
"""

from __future__ import annotations

import hashlib
import json
import re
import subprocess
from collections import Counter
from pathlib import Path, PurePosixPath
from typing import Any, Dict, List, Mapping, Optional, Sequence, Tuple
from zipfile import BadZipFile, ZipFile

import yaml

from harmo.validation_study import (
    environment_record,
    opensim_denominator,
)
from harmo.g6_execution import checkpoint_status, source_map_from_audit, trial_identity


OPEN_SIM_METRICS = (
    "ik_rms_error_m",
    "ik_max_error_m",
    "residual_force_fraction",
    "residual_moment_fraction",
    "rra_angle_change_rms_deg",
)

STATIC_PATTERN = re.compile(r"(?:^|[/_. -])(static|standing|calib(?:ration)?)(?:[/_. -]|$)", re.I)
SUBJECT_PATTERN = re.compile(
    r"(?:participant|subject|sub|p)[_ -]*0*(\d+)(?:[/_. -]|$)", re.I
)


def _stream_digest(path: Path, algorithm: str) -> str:
    digest = hashlib.new(algorithm)
    with path.open("rb") as stream:
        while True:
            chunk = stream.read(1024 * 1024)
            if not chunk:
                break
            digest.update(chunk)
    return digest.hexdigest()


def _asset_spec(root: Path, study: str, asset: str) -> Dict[str, Any]:
    corpus_path = root / "paper" / "validation_corpus.yaml"
    corpus = yaml.safe_load(corpus_path.read_text(encoding="utf-8")) or {}
    try:
        return dict(corpus["studies"][study]["assets"][asset])
    except KeyError as exc:
        raise ValueError(
            "validation corpus lacks {}/{} source asset".format(study, asset)
        ) from exc


def _verify_zip_asset(path: Path, spec: Mapping[str, Any]) -> Tuple[Dict[str, Any], List[Any]]:
    result: Dict[str, Any] = {
        "local_name": spec.get("local_name"),
        "source_record_doi": spec.get("source_record_doi"),
        "checksum_algorithm": spec.get("checksum_algorithm"),
        "expected_checksum": spec.get("checksum"),
        "expected_size_bytes": spec.get("size_bytes"),
        "present": path.is_file(),
        "size_bytes": None,
        "checksum": None,
        "size_pass": False,
        "checksum_pass": False,
        "archive_pass": False,
        "member_count": 0,
        "pass": False,
    }
    if not path.is_file():
        result["error"] = "source-asset-missing"
        return result, []
    result["size_bytes"] = path.stat().st_size
    result["size_pass"] = result["size_bytes"] == int(spec["size_bytes"])
    algorithm = str(spec["checksum_algorithm"]).lower()
    result["checksum"] = _stream_digest(path, algorithm)
    result["checksum_pass"] = result["checksum"] == str(spec["checksum"]).lower()
    try:
        with ZipFile(path) as archive:
            infos = archive.infolist()
            result["member_count"] = len(infos)
            # An exact whole-archive checksum already authenticates every byte;
            # reopening the central directory is sufficient to establish that
            # the authenticated object is structurally indexable.  Calling
            # testzip() here would needlessly decompress multi-gigabyte assets.
            result["archive_pass"] = True
    except BadZipFile:
        infos = []
        result["error"] = "invalid-zip"
    result["pass"] = bool(
        result["size_pass"] and result["checksum_pass"] and result["archive_pass"]
    )
    return result, infos


def _static_candidates(infos: Sequence[Any]) -> List[str]:
    return sorted(
        info.filename
        for info in infos
        if not info.is_dir()
        and PurePosixPath(info.filename).suffix.lower()
        in {".c3d", ".trc", ".txt", ".mat"}
        and STATIC_PATTERN.search(info.filename)
    )


def _static_subjects(candidates: Sequence[str]) -> List[str]:
    subjects = []
    for candidate in candidates:
        match = SUBJECT_PATTERN.search(candidate)
        if match:
            subjects.append(str(int(match.group(1))))
    return sorted(set(subjects), key=int)


def _zip_member_record(info: Optional[Any]) -> Dict[str, Any]:
    if info is None:
        return {"present": False, "size_bytes": None, "crc32": None}
    return {
        "present": True,
        "size_bytes": int(info.file_size),
        "crc32": "{:08x}".format(int(info.CRC)),
    }


def _source_trials(
    registry: Mapping[str, Any],
    c3d_infos: Sequence[Any],
    ascii_infos: Sequence[Any],
) -> List[Dict[str, Any]]:
    experiment = registry["experiments"]["E"]
    c3d = {info.filename: info for info in c3d_infos if not info.is_dir()}
    ascii_members = {info.filename: info for info in ascii_infos if not info.is_dir()}
    rows = []
    for subject in experiment["subjects"]:
        for speed in experiment["speeds"]:
            for trial in experiment["trials"]:
                c3d_name = "c3d files/Participant{}/Raw_Data/{}/{}.c3d".format(
                    subject, speed, trial
                )
                ascii_names = {
                    name.lower(): (
                        "ASCII files/Participant{}/Raw_Data/{}/{}/{}.txt".format(
                            subject, speed, name, trial
                        )
                    )
                    for name in ("Trajectories", "GRF", "FPM", "CoP")
                }
                c3d_record = _zip_member_record(c3d.get(c3d_name))
                components = {
                    name: _zip_member_record(ascii_members.get(member))
                    for name, member in ascii_names.items()
                }
                presence = [c3d_record["present"]] + [
                    value["present"] for value in components.values()
                ]
                source_status = (
                    "available"
                    if all(presence)
                    else "source-absent"
                    if not any(presence)
                    else "source-inconsistent"
                )
                rows.append(
                    {
                        "study_id": "study31",
                        "subject_id": str(subject),
                        "speed": speed,
                        "trial": trial,
                        "session_id": "ses-{}-{}".format(speed, trial),
                        "source_status": source_status,
                        "c3d_member": c3d_name,
                        "c3d": c3d_record,
                        "ascii": components,
                    }
                )
    return rows


def probe_opensim_container(image: str) -> Dict[str, Any]:
    """Record the image identity and runtime-reported OpenSim version."""
    result: Dict[str, Any] = {
        "container_image": image,
        "probe_status": "failed",
        "image_id": None,
        "repo_digests": [],
        "architecture": None,
        "runtime_version": None,
        "rra_available": None,
        "moco_inverse_available": None,
    }
    try:
        inspected = subprocess.run(
            ["docker", "image", "inspect", image],
            check=True,
            capture_output=True,
            text=True,
        )
        info = json.loads(inspected.stdout)[0]
        result.update(
            {
                "image_id": info.get("Id"),
                "repo_digests": sorted(info.get("RepoDigests") or []),
                "architecture": info.get("Architecture"),
            }
        )
        code = (
            "import opensim as o; "
            "print(o.GetVersion()); "
            "print(str(hasattr(o,'RRATool')).lower()); "
            "print(str(hasattr(o,'MocoInverse')).lower())"
        )
        probed = subprocess.run(
            [
                "docker",
                "run",
                "--rm",
                "--platform",
                "linux/amd64",
                image,
                "python3",
                "-c",
                code,
            ],
            check=True,
            capture_output=True,
            text=True,
        )
        lines = [line.strip() for line in probed.stdout.splitlines() if line.strip()]
        if len(lines) != 3:
            raise ValueError("unexpected OpenSim probe output")
        result.update(
            {
                "probe_status": "pass",
                "runtime_version": lines[0],
                "rra_available": lines[1] == "true",
                "moco_inverse_available": lines[2] == "true",
            }
        )
    except (OSError, subprocess.CalledProcessError, ValueError, json.JSONDecodeError) as exc:
        result["error"] = "{}: {}".format(type(exc).__name__, exc)
    return result


def probe_opensim_python(python_executable: Path) -> Dict[str, Any]:
    """Probe an isolated Python/OpenSim environment without storing local paths."""
    result: Dict[str, Any] = {
        "runtime_kind": "python-environment",
        "probe_status": "failed",
        "python_executable_name": python_executable.name,
        "runtime_version": None,
        "runtime_version_date": None,
        "rra_available": None,
        "moco_inverse_available": None,
        "python_version": None,
        "platform": None,
        "machine": None,
        "package": None,
    }
    code = (
        "import json,opensim as o,platform,sys;"
        "print(json.dumps({'runtime_version':o.GetVersion(),"
        "'runtime_version_date':o.GetVersionAndDate(),"
        "'rra_available':hasattr(o,'RRATool'),"
        "'moco_inverse_available':hasattr(o,'MocoInverse'),"
        "'python_version':platform.python_version(),"
        "'platform':platform.platform(),"
        "'machine':platform.machine()}))"
    )
    try:
        probed = subprocess.run(
            [str(python_executable), "-c", code],
            check=True,
            capture_output=True,
            text=True,
        )
        result.update(json.loads(probed.stdout))
        result["probe_status"] = "pass"
        metadata = sorted(
            (python_executable.parent.parent / "conda-meta").glob("opensim-*.json")
        )
        if len(metadata) == 1:
            package = json.loads(metadata[0].read_text(encoding="utf-8"))
            result["package"] = {
                key: package.get(key)
                for key in (
                    "name",
                    "version",
                    "build",
                    "build_number",
                    "channel",
                    "subdir",
                    "url",
                    "md5",
                    "sha256",
                )
            }
    except (OSError, subprocess.CalledProcessError, ValueError, json.JSONDecodeError) as exc:
        result["error"] = "{}: {}".format(type(exc).__name__, exc)
    return result


def _runtime_version_pass(runtime: Mapping[str, Any], required: str) -> bool:
    observed = str(runtime.get("runtime_version") or "")
    return runtime.get("probe_status") == "pass" and observed.startswith(required)


def _runtime_identity_pinned(runtime: Mapping[str, Any]) -> bool:
    package = runtime.get("package") or {}
    return bool(package.get("sha256") or runtime.get("repo_digests"))


def _reason_for(row: Mapping[str, Any], metric_statuses: Sequence[str]) -> str:
    if row["source_status"] != "available":
        return row["source_status"]
    if row.get("source_validation") is None:
        return "analysis-not-run"
    if any(status == "fail" for status in metric_statuses):
        return "threshold-exceeded"
    if any(status == "unassessed" for status in metric_statuses):
        return "metric-incomplete"
    return "all-thresholds-pass"


def _stratified_counts(
    rows: Sequence[Mapping[str, Any]], key: str
) -> List[Dict[str, Any]]:
    result = []
    for value in sorted({str(row[key]) for row in rows}):
        group = [row for row in rows if str(row[key]) == value]
        outcomes = Counter(str(row["outcome"]) for row in group)
        source_available = sum(row["source_status"] == "available" for row in group)
        completely_assessed = sum(
            row["source_status"] == "available"
            and all(
                row[metric + "_status"] != "unassessed"
                for metric in OPEN_SIM_METRICS
            )
            for row in group
        )
        result.append(
            {
                key: value,
                "frozen_trials": len(group),
                "source_available": source_available,
                "source_absent": sum(
                    row["source_status"] == "source-absent" for row in group
                ),
                "completely_assessed": completely_assessed,
                "pass": int(outcomes.get("pass", 0)),
                "fail": int(outcomes.get("fail", 0)),
                "unassessed": int(outcomes.get("unassessed", 0)),
                "pass_fraction_among_source_available": (
                    float(outcomes.get("pass", 0)) / source_available
                    if source_available
                    else None
                ),
            }
        )
    return result


def _external_checkpoint_audit(
    validation_files: Sequence[Path],
    scaling_source_audit: Mapping[str, Any],
    available_identities: Sequence[Tuple[str, str, str]],
) -> Dict[str, Any]:
    """Independently verify per-trial G6 checkpoint artifacts for final gating."""
    expected = set(available_identities)
    complete = set()
    seen = set()
    rejected: List[Dict[str, Any]] = []
    duplicates = set()
    try:
        sources = source_map_from_audit(scaling_source_audit)
    except (KeyError, TypeError, ValueError) as exc:
        return {
            "required": True,
            "expected": len(expected),
            "complete": 0,
            "missing": len(expected),
            "duplicates": 0,
            "rejected": 0,
            "reason_codes": ["source-audit-unusable:{}".format(type(exc).__name__)],
            "pass": False,
        }

    for path in sorted(validation_files):
        try:
            value = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            continue
        if not isinstance(value, Mapping):
            continue
        try:
            identity = trial_identity(value)
        except ValueError:
            continue
        if identity not in expected:
            continue
        if identity in seen:
            duplicates.add(identity)
            continue
        seen.add(identity)
        source = sources[identity[0]]
        expected_provenance = {
            "source_manifest_sha256": scaling_source_audit.get("manifest_sha256"),
            "scaling_source_kind": source["source_kind"],
            "static_trial_sha256": source["artifacts_by_role"]["static_trial"][
                "observed_sha256"
            ],
        }
        if source["source_kind"] == "traceable-subject-specific-model":
            expected_provenance["scaled_model_sha256"] = source[
                "artifacts_by_role"
            ]["scaled_model"]["observed_sha256"]
        status, reasons = checkpoint_status(path, identity, expected_provenance)
        if status == "resume-complete":
            complete.add(identity)
        else:
            rejected.append(
                {
                    "identity": list(identity),
                    "validation": path.name,
                    "reason_codes": reasons,
                }
            )
    missing = expected - complete
    return {
        "required": True,
        "expected": len(expected),
        "complete": len(complete),
        "missing": len(missing),
        "duplicates": len(duplicates),
        "rejected": len(rejected),
        "reason_codes": [],
        "rejected_records": rejected,
        "pass": len(complete) == len(expected) and not duplicates and not rejected,
    }


def run_study31_opensim_quality(
    root: Path,
    registry: Mapping[str, Any],
    data_root: Path,
    validation_files: Sequence[Path],
    runtime: Optional[Mapping[str, Any]] = None,
    scaling_source_audit: Optional[Mapping[str, Any]] = None,
) -> Tuple[List[Dict[str, Any]], Dict[str, Any]]:
    """Create the fixed-denominator ledger and its fail-closed summary."""
    experiment = registry["experiments"]["E"]
    c3d_spec = _asset_spec(root, "study31", "c3d")
    ascii_spec = _asset_spec(root, "study31", "ascii")
    mat_spec = _asset_spec(root, "study31", "mat")
    c3d_asset, c3d_infos = _verify_zip_asset(
        data_root / str(c3d_spec["local_name"]), c3d_spec
    )
    ascii_asset, ascii_infos = _verify_zip_asset(
        data_root / str(ascii_spec["local_name"]), ascii_spec
    )
    mat_asset, mat_infos = _verify_zip_asset(
        data_root / str(mat_spec["local_name"]), mat_spec
    )
    source_rows = _source_trials(registry, c3d_infos, ascii_infos)
    quality_rows = opensim_denominator(validation_files, registry)
    quality_by_key = {
        (row["subject_id"], row["speed"], row["trial"]): row
        for row in quality_rows
    }

    ledger: List[Dict[str, Any]] = []
    for source in source_rows:
        key = (source["subject_id"], source["speed"], source["trial"])
        quality = quality_by_key[key]
        metric_statuses = [quality[name + "_status"] for name in OPEN_SIM_METRICS]
        quality["source_status"] = source["source_status"]
        quality["source_c3d_member"] = source["c3d_member"]
        quality["source_c3d_size_bytes"] = source["c3d"]["size_bytes"]
        quality["source_c3d_crc32"] = source["c3d"]["crc32"]
        quality["source_ascii_components_present"] = sorted(
            name for name, value in source["ascii"].items() if value["present"]
        )
        quality["reason_code"] = _reason_for(quality, metric_statuses)
        if source["source_status"] != "available":
            quality["outcome"] = "unassessed"
        ledger.append(quality)

    expected = int(experiment["denominator"])
    identities = {
        (row["subject_id"], row["speed"], row["trial"]) for row in ledger
    }
    source_counts = Counter(row["source_status"] for row in ledger)
    outcome_counts = Counter(row["outcome"] for row in ledger)
    reason_counts = Counter(row["reason_code"] for row in ledger)
    metric_counts = {
        metric: dict(
            Counter(row[metric + "_status"] for row in ledger)
        )
        for metric in OPEN_SIM_METRICS
    }
    source_available = int(source_counts.get("available", 0))
    completely_assessed = sum(
        row["source_status"] == "available"
        and all(row[metric + "_status"] != "unassessed" for metric in OPEN_SIM_METRICS)
        for row in ledger
    )
    static_candidates = sorted(
        set(
            _static_candidates(c3d_infos)
            + _static_candidates(ascii_infos)
            + _static_candidates(mat_infos)
        )
    )
    expected_subjects = [str(value) for value in experiment["subjects"]]
    released_static_subjects = _static_subjects(static_candidates)
    released_static_complete = set(released_static_subjects) == set(
        expected_subjects
    )
    external_audit = dict(scaling_source_audit or {})
    external_bundle_complete = bool(
        external_audit.get("pass") is True
        and external_audit.get("scope")
        == "study31-subject-specific-scaling-source-intake"
        and set(str(value) for value in external_audit.get("expected_subjects", []))
        == set(expected_subjects)
        and external_audit.get("counts", {}).get("accepted")
        == len(expected_subjects)
    )
    scaling_sources_available = bool(
        released_static_complete or external_bundle_complete
    )
    available_identities = [
        (row["subject_id"], row["speed"], row["trial"])
        for row in source_rows
        if row["source_status"] == "available"
    ]
    checkpoint_audit = (
        _external_checkpoint_audit(
            validation_files,
            external_audit,
            available_identities,
        )
        if external_bundle_complete
        else {
            "required": False,
            "expected": len(available_identities),
            "complete": None,
            "missing": None,
            "duplicates": None,
            "rejected": None,
            "reason_codes": [],
            "pass": True,
        }
    )
    source_absent_trials = [
        {
            "subject_id": row["subject_id"],
            "speed": row["speed"],
            "trial": row["trial"],
            "session_id": row["session_id"],
        }
        for row in ledger
        if row["source_status"] == "source-absent"
    ]
    runtime_record = dict(runtime or {"probe_status": "not-run"})
    required_version = str(experiment.get("required_opensim_version", ""))
    checks = {
        "ledger_denominator": len(ledger) == expected,
        "ledger_identities_unique": len(identities) == expected,
        "c3d_asset_verified": c3d_asset["pass"],
        "ascii_asset_verified": ascii_asset["pass"],
        "mat_asset_verified": mat_asset["pass"],
        "source_denominator_resolved": (
            source_available + int(source_counts.get("source-absent", 0))
            == expected
            and int(source_counts.get("source-inconsistent", 0)) == 0
        ),
        "subject_specific_scaling_sources_available": scaling_sources_available,
        "runtime_probe_pass": runtime_record.get("probe_status") == "pass",
        "runtime_version_match": _runtime_version_pass(
            runtime_record, required_version
        ),
        "runtime_identity_pinned": _runtime_identity_pinned(runtime_record),
        "rra_tool_available": runtime_record.get("rra_available") is True,
        "moco_inverse_available": (
            runtime_record.get("moco_inverse_available") is True
        ),
        "external_checkpoint_contract_complete": checkpoint_audit["pass"],
        "all_source_available_trials_completely_assessed": (
            source_available > 0 and completely_assessed == source_available
        ),
        "no_threshold_failures": int(outcome_counts.get("fail", 0)) == 0,
    }
    summary = {
        "assessment_version": "1.2.0",
        "scope": "checksum-bound-study31-opensim-quality",
        "protocol": {
            "study": experiment["study"],
            "subjects": experiment["subjects"],
            "speeds": experiment["speeds"],
            "trials": experiment["trials"],
            "denominator": expected,
            "source_absence_policy": experiment.get(
                "source_absence_policy", "retain"
            ),
            "static_calibration_required": experiment.get(
                "static_calibration_required", True
            ),
            "required_opensim_version": required_version,
            "metrics": {
                metric: ledger[0][metric + "_threshold"] if ledger else None
                for metric in OPEN_SIM_METRICS
            },
        },
        "source_assets": {
            "ascii": ascii_asset,
            "mat": mat_asset,
            "c3d": c3d_asset,
        },
        "source_denominator": {
            "frozen_trials": expected,
            "source_available": source_available,
            "source_absent": int(source_counts.get("source-absent", 0)),
            "source_inconsistent": int(source_counts.get("source-inconsistent", 0)),
            "source_absent_trials": source_absent_trials,
        },
        "static_calibration": {
            "described_in_source_article": True,
            "released_candidate_count": len(static_candidates),
            "released_candidates": static_candidates,
            "released_subjects": released_static_subjects,
            "released_complete": released_static_complete,
            "archives_scanned": ["ascii", "mat", "c3d"],
            "external_bundle": {
                "provided": scaling_source_audit is not None,
                "pass": external_bundle_complete,
                "manifest": external_audit.get("manifest"),
                "manifest_sha256": external_audit.get("manifest_sha256"),
                "accepted_subjects": (
                    external_audit.get("counts", {}).get("accepted", 0)
                    if external_audit
                    else 0
                ),
            },
            "eligible_source": (
                "released-static-trials"
                if released_static_complete
                else "verified-external-bundle"
                if external_bundle_complete
                else None
            ),
            "status": "available" if scaling_sources_available else "source-absent",
        },
        "analysis_denominator": {
            "validation_files_considered": len(validation_files),
            "trials_with_validation": sum(
                row.get("source_validation") is not None for row in ledger
            ),
            "source_available_trials_completely_assessed": completely_assessed,
            "outcomes": dict(sorted(outcome_counts.items())),
            "reason_codes": dict(sorted(reason_counts.items())),
            "metrics": metric_counts,
            "checkpoint_contract": checkpoint_audit,
            "by_subject": _stratified_counts(ledger, "subject_id"),
            "by_speed": _stratified_counts(ledger, "speed"),
        },
        "runtime": runtime_record,
        "checks": checks,
        "audit_complete": bool(
            checks["ledger_denominator"]
            and checks["ledger_identities_unique"]
            and checks["c3d_asset_verified"]
            and checks["ascii_asset_verified"]
            and checks["mat_asset_verified"]
        ),
        "pass": all(checks.values()),
        "environment": environment_record(),
    }
    return ledger, summary
