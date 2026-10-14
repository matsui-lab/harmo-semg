"""Fail-closed, source-labelled adapter for the held-out ULTRA-MoCap release.

This module is intentionally dataset-local.  It verifies the released archive,
enumerates every expected HDF5 trial, parses every numeric cell twice, and
produces compact manifests without copying the source signals into the
repository.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import platform
import re
import subprocess
import tempfile
import time
import zlib
import zipfile
from pathlib import Path, PurePosixPath
from typing import Any, Dict, Iterable, List, Mapping, Optional, Sequence, Tuple

import h5py
import numpy as np
import yaml


class AdapterValidationError(ValueError):
    """Raised when the source violates the frozen adapter contract."""


def _canonical_json(value: Any) -> str:
    return json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    )


def _canonical_sha256(value: Any) -> str:
    return hashlib.sha256(_canonical_json(value).encode("utf-8")).hexdigest()


def _write_json(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(
        json.dumps(
            value,
            ensure_ascii=False,
            sort_keys=True,
            indent=2,
            allow_nan=False,
        )
        + "\n",
        encoding="utf-8",
    )
    os.replace(str(temporary), str(path))


def _load_yaml(path: Path) -> Dict[str, Any]:
    value = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    if not isinstance(value, dict):
        raise AdapterValidationError("{} must contain a mapping".format(path))
    return value


def _hash_file(
    path: Path, algorithms: Sequence[str] = ("md5", "sha256")
) -> Dict[str, Any]:
    digests = {name: hashlib.new(name) for name in algorithms}
    size = 0
    with path.open("rb") as stream:
        while True:
            chunk = stream.read(8 * 1024 * 1024)
            if not chunk:
                break
            size += len(chunk)
            for digest in digests.values():
                digest.update(chunk)
    result = {"bytes": size}
    result.update({name: digest.hexdigest() for name, digest in digests.items()})
    return result


def _hash_and_crc32(path: Path) -> Dict[str, Any]:
    digest = hashlib.sha256()
    checksum = 0
    size = 0
    with path.open("rb") as stream:
        while True:
            chunk = stream.read(8 * 1024 * 1024)
            if not chunk:
                break
            size += len(chunk)
            checksum = zlib.crc32(chunk, checksum)
            digest.update(chunk)
    return {
        "bytes": size,
        "crc32": checksum & 0xFFFFFFFF,
        "sha256": digest.hexdigest(),
    }


def _unsafe_zip_member(name: str) -> bool:
    if "\\" in name:
        return True
    path = PurePosixPath(name)
    return path.is_absolute() or ".." in path.parts


def verify_processed_archive(
    archive_path: Path,
    protocol: Mapping[str, Any],
    member_basename: str,
) -> Dict[str, Any]:
    """Verify release identity, safe paths, uniqueness, and ZIP CRC."""
    expected_assets = [
        item
        for item in protocol.get("source_assets", [])
        if item.get("materialization") == "required"
    ]
    if len(expected_assets) != 1:
        raise AdapterValidationError(
            "protocol must declare exactly one required source asset"
        )
    expected = expected_assets[0]
    identity = _hash_file(archive_path)
    if identity["bytes"] != int(expected["bytes"]):
        raise AdapterValidationError(
            "archive size {} differs from frozen {}".format(
                identity["bytes"], expected["bytes"]
            )
        )
    if identity["md5"].lower() != str(expected["md5"]).lower():
        raise AdapterValidationError("archive MD5 differs from frozen release")

    with zipfile.ZipFile(str(archive_path), "r") as archive:
        infos = archive.infolist()
        names = [item.filename for item in infos]
        if len(names) != len(set(names)):
            raise AdapterValidationError("archive contains duplicate member names")
        unsafe = [name for name in names if _unsafe_zip_member(name)]
        if unsafe:
            raise AdapterValidationError(
                "archive contains unsafe paths: {}".format(unsafe[:3])
            )
        matches = [
            item
            for item in infos
            if not item.is_dir()
            and PurePosixPath(item.filename).name == member_basename
        ]
        if len(matches) != 1:
            raise AdapterValidationError(
                "archive must contain exactly one {}".format(member_basename)
            )
        corrupt = archive.testzip()
        if corrupt is not None:
            raise AdapterValidationError(
                "ZIP CRC failed for member {}".format(corrupt)
            )
        member = matches[0]
    return {
        "name": expected["name"],
        "file_id": int(expected["file_id"]),
        "bytes": identity["bytes"],
        "md5": identity["md5"],
        "sha256": identity["sha256"],
        "identity_matches_frozen_release": True,
        "zip_crc_pass": True,
        "member_count": len(names),
        "unsafe_member_count": 0,
        "duplicate_member_count": 0,
        "hdf5_member": member.filename,
        "hdf5_uncompressed_bytes": member.file_size,
        "hdf5_crc32": member.CRC,
    }


def verify_extracted_hdf5(
    hdf5_path: Path, archive_verification: Mapping[str, Any]
) -> Dict[str, Any]:
    identity = _hash_and_crc32(hdf5_path)
    if identity["bytes"] != int(archive_verification["hdf5_uncompressed_bytes"]):
        raise AdapterValidationError(
            "extracted HDF5 size differs from verified ZIP member"
        )
    if identity["crc32"] != int(archive_verification["hdf5_crc32"]):
        raise AdapterValidationError(
            "extracted HDF5 CRC32 differs from verified ZIP member"
        )
    identity["matches_verified_zip_member"] = True
    return identity


def _source_assets(
    protocol: Mapping[str, Any], archive_verification: Mapping[str, Any]
) -> List[Dict[str, Any]]:
    rows = []
    for item in protocol.get("source_assets", []):
        materialized = item.get("materialization") == "required"
        row = {
            "role": item["role"],
            "file_id": int(item["file_id"]),
            "name": item["name"],
            "expected_bytes": int(item["bytes"]),
            "expected_md5": item["md5"],
            "materialization": item["materialization"],
            "accounted": True,
            "materialized": materialized,
        }
        if materialized:
            row.update(
                {
                    "identity_verification": "byte-verified",
                    "observed_bytes": int(archive_verification["bytes"]),
                    "observed_md5": archive_verification["md5"],
                    "observed_sha256": archive_verification["sha256"],
                }
            )
        else:
            row.update(
                {
                    "identity_verification": "not-performed",
                    "reason": (
                        "registered for complete source accounting; the frozen "
                        "processed-only endpoint does not require materialization"
                    ),
                }
            )
        rows.append(row)
    return rows


def _expected_trials(
    protocol: Mapping[str, Any], schema: Mapping[str, Any]
) -> Dict[str, Dict[str, str]]:
    subject_numbers = {}
    template = str(schema["subject_id_template"])
    for number in range(1, 1000):
        subject_id = template.format(number=number)
        if subject_id in protocol["dataset"]["expected_subjects"]:
            subject_numbers[subject_id] = number
    if sorted(subject_numbers) != sorted(protocol["dataset"]["expected_subjects"]):
        raise AdapterValidationError("subject template cannot express frozen subjects")

    by_task = {}
    for raw_task, task in schema["tasks"].items():
        by_task[str(task["name"])] = (str(raw_task), task["conditions"])

    expected = {}
    for subject_id in protocol["dataset"]["expected_subjects"]:
        number = subject_numbers[subject_id]
        raw_subject = "subject_{}".format(number)
        for task_name, conditions in protocol["dataset"]["expected_tasks"].items():
            if task_name not in by_task:
                raise AdapterValidationError(
                    "schema lacks frozen task {}".format(task_name)
                )
            raw_task, condition_map = by_task[task_name]
            inverse = {str(value): str(key) for key, value in condition_map.items()}
            for condition in conditions:
                condition = str(condition)
                if condition not in inverse:
                    raise AdapterValidationError(
                        "schema lacks condition {} / {}".format(task_name, condition)
                    )
                raw_condition = inverse[condition]
                path = "{}/{}/{}".format(
                    raw_subject, raw_task, raw_condition
                )
                expected[path] = {
                    "subject_id": subject_id,
                    "task": str(task_name),
                    "condition": condition,
                    "source_path": path,
                }
    return expected


def _dataset_paths(hdf5: h5py.File) -> List[str]:
    paths = []

    def visitor(name: str, value: Any) -> None:
        if isinstance(value, h5py.Dataset):
            paths.append(name)

    hdf5.visititems(visitor)
    return sorted(paths)


def _decode_labels(value: Any) -> List[str]:
    labels = []
    for item in list(value):
        if isinstance(item, (bytes, np.bytes_)):
            labels.append(bytes(item).decode("utf-8"))
        else:
            labels.append(str(item))
    return labels


def _column_classifier(schema: Mapping[str, Any]):
    columns = schema["columns"]
    exact: Dict[str, str] = {
        str(columns["time"]["exact"]): str(columns["time"]["modality"])
    }
    for label in columns["auxiliary"]["exact"]:
        exact[str(label)] = str(columns["auxiliary"]["modality"])
    for label in columns["kinematics"]["exact"]:
        exact[str(label)] = str(columns["kinematics"]["modality"])
    patterns = [
        (re.compile(str(item["regex"])), str(item["modality"]))
        for item in columns["patterns"]
    ]

    def classify(label: str) -> Optional[str]:
        if label in exact:
            return exact[label]
        matches = [modality for pattern, modality in patterns if pattern.fullmatch(label)]
        if len(matches) > 1:
            raise AdapterValidationError(
                "column {} matches multiple modality rules".format(label)
            )
        return matches[0] if matches else None

    return classify


def _parse_numeric_chunk(
    raw: np.ndarray,
    source_path: str,
    source_missing_tokens: Sequence[str],
) -> Tuple[np.ndarray, np.ndarray]:
    missing = np.zeros(raw.shape, dtype=bool)
    for token in source_missing_tokens:
        missing |= raw == str(token).encode("utf-8")
        missing |= raw == str(token)
    parseable = raw.copy()
    parseable[missing] = b"nan"
    try:
        values = np.fromiter(
            (float(value) for value in parseable.ravel(order="C")),
            dtype=np.float64,
            count=parseable.size,
        )
    except (TypeError, ValueError, OverflowError) as error:
        raise AdapterValidationError(
            "{} contains a non-numeric HDF5 cell: {}".format(source_path, error)
        )
    return values.reshape(raw.shape), missing


def _normalized_numeric_bytes(values: np.ndarray) -> bytes:
    normalized = np.asarray(values, dtype="<f8").copy()
    normalized[np.isnan(normalized)] = np.nan
    normalized[np.isposinf(normalized)] = np.inf
    normalized[np.isneginf(normalized)] = -np.inf
    return normalized.tobytes(order="C")


def _scan_trial(
    dataset: h5py.Dataset,
    identity: Mapping[str, str],
    classify: Any,
    required_modalities: Sequence[str],
    source_missing_tokens: Sequence[str],
    chunk_rows: int,
) -> Dict[str, Any]:
    source_path = identity["source_path"]
    if dataset.ndim != 2:
        raise AdapterValidationError(
            "{} must be a two-dimensional array".format(source_path)
        )
    if "column_names" not in dataset.attrs:
        raise AdapterValidationError(
            "{} lacks source-declared column_names".format(source_path)
        )
    labels = _decode_labels(dataset.attrs["column_names"])
    if len(labels) != int(dataset.shape[1]):
        raise AdapterValidationError(
            "{} label count differs from array columns".format(source_path)
        )
    if len(labels) != len(set(labels)):
        raise AdapterValidationError(
            "{} contains duplicate source labels".format(source_path)
        )
    modalities = []
    unknown = []
    for label in labels:
        modality = classify(label)
        if modality is None:
            unknown.append(label)
        modalities.append(modality)
    if unknown:
        raise AdapterValidationError(
            "{} contains unknown labels: {}".format(source_path, sorted(unknown))
        )
    missing = sorted(set(required_modalities) - set(modalities))
    if missing:
        raise AdapterValidationError(
            "{} lacks required modalities: {}".format(source_path, missing)
        )
    if "time" not in modalities or modalities.count("time") != 1:
        raise AdapterValidationError(
            "{} must contain exactly one labelled time column".format(source_path)
        )

    time_index = modalities.index("time")
    digest = hashlib.sha256()
    digest.update(_canonical_json({"labels": labels, **identity}).encode("utf-8"))
    finite = {name: 0 for name in sorted(set(modalities))}
    nonfinite = {name: 0 for name in sorted(set(modalities))}
    source_missing = {name: 0 for name in sorted(set(modalities))}
    time_values: List[float] = []
    previous_time: Optional[float] = None

    for start in range(0, int(dataset.shape[0]), chunk_rows):
        stop = min(start + chunk_rows, int(dataset.shape[0]))
        raw = dataset[start:stop, :]
        values, missing_values = _parse_numeric_chunk(
            raw, source_path, source_missing_tokens
        )
        digest.update(_normalized_numeric_bytes(values))
        for modality in finite:
            indices = [i for i, value in enumerate(modalities) if value == modality]
            selected = values[:, indices]
            count = int(np.isfinite(selected).sum())
            finite[modality] += count
            nonfinite[modality] += int(selected.size) - count
            source_missing[modality] += int(missing_values[:, indices].sum())
        times = values[:, time_index]
        if not np.isfinite(times).all():
            raise AdapterValidationError(
                "{} contains non-finite source time".format(source_path)
            )
        for current in times.tolist():
            if previous_time is not None and not current > previous_time:
                raise AdapterValidationError(
                    "{} source time is not strictly increasing".format(source_path)
                )
            time_values.append(float(current))
            previous_time = float(current)

    if len(time_values) < 2:
        raise AdapterValidationError(
            "{} requires at least two time samples".format(source_path)
        )
    differences = np.diff(np.asarray(time_values, dtype=np.float64))
    median_step = float(np.median(differences))
    maximum_step_error = float(np.max(np.abs(differences - median_step)))
    uniform = maximum_step_error <= max(1e-9, abs(median_step) * 1e-7)
    counts = {
        modality: modalities.count(modality) for modality in sorted(set(modalities))
    }
    return {
        **dict(identity),
        "rows": int(dataset.shape[0]),
        "columns": int(dataset.shape[1]),
        "source_labels": labels,
        "source_labels_sha256": _canonical_sha256(labels),
        "modality_column_counts": counts,
        "finite_value_counts": finite,
        "nonfinite_value_counts": nonfinite,
        "source_missing_value_counts": source_missing,
        "source_missing_value_tokens": list(source_missing_tokens),
        "source_time": {
            "start_s": time_values[0],
            "end_s": time_values[-1],
            "median_step_s": median_step,
            "minimum_step_s": float(np.min(differences)),
            "maximum_step_s": float(np.max(differences)),
            "maximum_uniform_step_error_s": maximum_step_error,
            "strictly_increasing": True,
            "uniform": uniform,
            "sampling_rate_hz": (1.0 / median_step if uniform else None),
            "grid_policy": "source-synchronized-no-resampling",
        },
        "amplitude_transform": "none",
        "auxiliary_semantics": {
            "1": "source-auxiliary-unresolved-retained"
        },
        "normalized_numeric_sha256": digest.hexdigest(),
    }


def scan_hdf5(
    hdf5_path: Path,
    protocol: Mapping[str, Any],
    schema: Mapping[str, Any],
    chunk_rows: int = 512,
) -> Dict[str, Any]:
    expected = _expected_trials(protocol, schema)
    classify = _column_classifier(schema)
    required_modalities = [
        str(value) for value in schema["required_trial_modalities"]
    ]
    source_missing_tokens = [
        str(value)
        for value in schema["columns"].get("source_missing_value_tokens", [])
    ]
    rows = []
    with h5py.File(str(hdf5_path), "r") as hdf5:
        discovered = _dataset_paths(hdf5)
        if discovered != sorted(expected):
            missing = sorted(set(expected) - set(discovered))
            unexpected = sorted(set(discovered) - set(expected))
            raise AdapterValidationError(
                "HDF5 trial paths differ; missing={}, unexpected={}".format(
                    missing[:5], unexpected[:5]
                )
            )
        for source_path in sorted(expected):
            rows.append(
                _scan_trial(
                    hdf5[source_path],
                    expected[source_path],
                    classify,
                    required_modalities,
                    source_missing_tokens,
                    chunk_rows,
                )
            )
    subjects = sorted({row["subject_id"] for row in rows})
    manifest_sha256 = _canonical_sha256(rows)
    return {
        "dataset": schema["dataset_id"],
        "subject_count": len(subjects),
        "subjects": subjects,
        "trial_count": len(rows),
        "trials": rows,
        "normalized_manifest_sha256": manifest_sha256,
    }


def _git_changed_lines(
    repository_root: Path,
    baseline_commit: str,
    implementation_commit: str = "HEAD",
) -> Tuple[int, int]:
    completed = subprocess.run(
        [
            "git",
            "diff",
            "--numstat",
            "{}..{}".format(baseline_commit, implementation_commit),
        ],
        cwd=str(repository_root),
        check=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
    )
    core = 0
    adapter = 0
    for line in completed.stdout.splitlines():
        parts = line.split("\t")
        if len(parts) != 3 or not parts[0].isdigit() or not parts[1].isdigit():
            continue
        changed = int(parts[0]) + int(parts[1])
        path = parts[2]
        if path.startswith("src/harmo/adapters/") or path.startswith(
            "studies/ultra_mocap/"
        ):
            adapter += changed
        elif path.startswith("src/harmo/"):
            core += changed
    return core, adapter


def _core_dataset_branches(
    repository_root: Path, forbidden_tokens: Iterable[str]
) -> List[Dict[str, Any]]:
    findings = []
    root = repository_root / "src" / "harmo"
    for path in sorted(root.rglob("*.py")):
        if "adapters" in path.relative_to(root).parts:
            continue
        text = path.read_text(encoding="utf-8")
        for token in forbidden_tokens:
            for number, line in enumerate(text.splitlines(), 1):
                if str(token) in line:
                    findings.append(
                        {
                            "path": str(path.relative_to(repository_root)),
                            "line": number,
                            "token": str(token),
                        }
                    )
    return findings


def _check(name: str, passed: bool, detail: Any) -> Dict[str, Any]:
    return {"name": name, "pass": bool(passed), "detail": detail}


def run_adapter(
    repository_root: Path,
    protocol_path: Path,
    schema_path: Path,
    archive_path: Path,
    hdf5_path: Path,
    repeats: int = 2,
    chunk_rows: int = 512,
) -> Dict[str, Any]:
    """Execute the frozen confirmatory adapter and return all result artifacts."""
    if repeats < 2:
        raise AdapterValidationError("confirmatory execution requires at least two scans")
    started = time.monotonic()
    protocol = _load_yaml(protocol_path)
    schema = _load_yaml(schema_path)
    member_basename = str(schema["hdf5_member_basename"])
    archive = verify_processed_archive(archive_path, protocol, member_basename)
    hdf5_identity = verify_extracted_hdf5(hdf5_path, archive)
    scans = [
        scan_hdf5(hdf5_path, protocol, schema, chunk_rows=chunk_rows)
        for _ in range(repeats)
    ]
    scan_digests = [value["normalized_manifest_sha256"] for value in scans]
    repeated_identical = len(set(scan_digests)) == 1
    expected_subjects = sorted(protocol["dataset"]["expected_subjects"])
    expected_trials = int(protocol["dataset"]["expected_trial_count"])
    sources = _source_assets(protocol, archive)
    implementation_commit = str(protocol.get("implementation_commit", "HEAD"))
    core_lines, adapter_lines = _git_changed_lines(
        repository_root,
        str(protocol["baseline_commit"]),
        implementation_commit,
    )
    branch_findings = _core_dataset_branches(
        repository_root,
        protocol["adapter_boundary"]["forbidden_core_tokens"],
    )
    first = scans[0]
    trials = first["trials"]
    all_emg = all(row["modality_column_counts"].get("emg", 0) > 0 for row in trials)
    all_imu = all(
        row["modality_column_counts"].get("imu-accelerometer", 0) > 0
        and row["modality_column_counts"].get("imu-gyroscope", 0) > 0
        for row in trials
    )
    all_joint = all(
        row["modality_column_counts"].get("joint-angle", 0) > 0 for row in trials
    )
    all_time = all(
        row["source_time"]["strictly_increasing"] for row in trials
    )
    checks = [
        _check("processed-archive-identity", True, archive["sha256"]),
        _check("processed-archive-zip-crc", archive["zip_crc_pass"], True),
        _check(
            "all-source-assets-accounted",
            len(sources) == 3 and all(row["accounted"] for row in sources),
            {"accounted": sum(row["accounted"] for row in sources), "total": len(sources)},
        ),
        _check(
            "materialization-boundary",
            sum(row["materialized"] for row in sources) == 1,
            {"materialized": sum(row["materialized"] for row in sources)},
        ),
        _check(
            "subjects-exact",
            first["subjects"] == expected_subjects,
            first["subjects"],
        ),
        _check(
            "trials-exact",
            first["trial_count"] == expected_trials,
            first["trial_count"],
        ),
        _check("every-trial-has-emg", all_emg, len(trials)),
        _check("every-trial-has-imu", all_imu, len(trials)),
        _check("every-trial-has-joint-angle", all_joint, len(trials)),
        _check("all-source-time-increasing", all_time, len(trials)),
        _check(
            "repeated-normalized-manifest-identical",
            repeated_identical,
            scan_digests,
        ),
        _check("core-changed-lines-zero", core_lines == 0, core_lines),
        _check(
            "core-dataset-id-branches-zero",
            len(branch_findings) == 0,
            branch_findings,
        ),
    ]
    elapsed = time.monotonic() - started
    metrics = {
        "schema_version": "1.0",
        "protocol_id": protocol["protocol_id"],
        "dataset": schema["dataset_id"],
        "source_count": len(sources),
        "accounted_source_count": sum(row["accounted"] for row in sources),
        "materialized_source_count": sum(row["materialized"] for row in sources),
        "subject_count": first["subject_count"],
        "trial_count": first["trial_count"],
        "core_dataset_id_branches": len(branch_findings),
        "core_dataset_id_branch_findings": branch_findings,
        "core_changed_lines": core_lines,
        "adapter_changed_lines": adapter_lines,
        "core_comparison_baseline_commit": str(protocol["baseline_commit"]),
        "core_comparison_implementation_commit": implementation_commit,
        "active_work_seconds": elapsed,
        "active_work_scope": "confirmatory-adapter-execution-wall-time",
        "repeat_count": repeats,
        "normalized_manifest_sha256": scan_digests[0],
        "repeat_manifest_sha256": scan_digests,
        "processed_archive_sha256": archive["sha256"],
        "processed_hdf5_sha256": hdf5_identity["sha256"],
        "all_sources_accounted": len(sources) > 0
        and all(row["accounted"] for row in sources),
        "checks": checks,
        "pass": all(item["pass"] for item in checks),
        "claim_boundary": protocol["claim_boundary"],
    }
    source_manifest = {
        "dataset": schema["dataset_id"],
        "article_doi": protocol["dataset"]["article_doi"],
        "repository_doi": protocol["dataset"]["repository_doi"],
        "license": protocol["dataset"]["license"],
        "assets": sources,
        "processed_archive_verification": archive,
        "processed_hdf5_verification": hdf5_identity,
    }
    reproducibility = {
        "dataset": schema["dataset_id"],
        "repeat_count": repeats,
        "normalized_manifest_sha256": scan_digests,
        "identical": repeated_identical,
        "pass": repeated_identical,
    }
    return {
        "metrics": metrics,
        "trial_manifest": first,
        "source_manifest": source_manifest,
        "reproducibility": reproducibility,
    }


def write_results(output_dir: Path, result: Mapping[str, Any]) -> Dict[str, str]:
    paths = {
        "metrics": output_dir / "adapter_metrics.json",
        "trial_manifest": output_dir / "ultra_mocap_trial_manifest.json",
        "source_manifest": output_dir / "ultra_mocap_source_manifest.json",
        "reproducibility": output_dir
        / "ultra_mocap_adapter_reproducibility.json",
    }
    for key, path in paths.items():
        _write_json(path, result[key])
    return {key: str(path) for key, path in paths.items()}


def _execution_record(
    repository_root: Path,
    result: Mapping[str, Any],
) -> Dict[str, Any]:
    def git_text(*arguments: str) -> str:
        try:
            return subprocess.check_output(
                ["git"] + list(arguments),
                cwd=str(repository_root),
                stderr=subprocess.DEVNULL,
                text=True,
            ).strip()
        except (OSError, subprocess.CalledProcessError):
            return "unknown"

    metrics = result["metrics"]
    return {
        "schema_version": "1.0",
        "experiment": "F held-out ULTRA-MoCap adapter",
        "platform": platform.platform(),
        "machine": platform.machine(),
        "python": platform.python_version(),
        "implementation": platform.python_implementation(),
        "code_commit": git_text("rev-parse", "HEAD"),
        "code_dirty": bool(git_text("status", "--porcelain")),
        "protocol_id": metrics["protocol_id"],
        "result_pass": bool(metrics["pass"]),
        "normalized_manifest_sha256": metrics["normalized_manifest_sha256"],
        "processed_archive_sha256": metrics["processed_archive_sha256"],
        "processed_hdf5_sha256": metrics["processed_hdf5_sha256"],
        "core_comparison_baseline_commit": metrics[
            "core_comparison_baseline_commit"
        ],
        "core_comparison_implementation_commit": metrics[
            "core_comparison_implementation_commit"
        ],
    }


def _extract_verified_member(
    archive_path: Path,
    archive_verification: Mapping[str, Any],
    destination: Path,
) -> Path:
    destination.mkdir(parents=True, exist_ok=True)
    target = destination / PurePosixPath(
        str(archive_verification["hdf5_member"])
    ).name
    with zipfile.ZipFile(str(archive_path), "r") as archive:
        with archive.open(str(archive_verification["hdf5_member"]), "r") as source:
            with target.open("wb") as output:
                while True:
                    chunk = source.read(8 * 1024 * 1024)
                    if not chunk:
                        break
                    output.write(chunk)
    return target


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Run the frozen held-out ULTRA-MoCap adapter validation"
    )
    parser.add_argument("--root", default=".")
    parser.add_argument(
        "--protocol", default="paper/g7_ultra_mocap_protocol.yaml"
    )
    parser.add_argument(
        "--schema", default="studies/ultra_mocap/adapter_schema.yaml"
    )
    parser.add_argument("--archive", required=True)
    parser.add_argument(
        "--hdf5",
        default=None,
        help="verified extracted HDF5; omitted means safe temporary extraction",
    )
    parser.add_argument(
        "--output-dir", default="paper/results/validation"
    )
    parser.add_argument("--repeats", type=int, default=2)
    parser.add_argument("--chunk-rows", type=int, default=512)
    return parser


def main(argv: Optional[Sequence[str]] = None) -> int:
    args = _parser().parse_args(argv)
    root = Path(args.root).resolve()

    def within_root(value: str) -> Path:
        path = Path(value)
        return path.resolve() if path.is_absolute() else (root / path).resolve()

    protocol_path = within_root(args.protocol)
    schema_path = within_root(args.schema)
    archive_path = Path(args.archive).resolve()
    output_dir = within_root(args.output_dir)
    if args.hdf5:
        result = run_adapter(
            root,
            protocol_path,
            schema_path,
            archive_path,
            Path(args.hdf5).resolve(),
            repeats=args.repeats,
            chunk_rows=args.chunk_rows,
        )
    else:
        protocol = _load_yaml(protocol_path)
        schema = _load_yaml(schema_path)
        verification = verify_processed_archive(
            archive_path, protocol, str(schema["hdf5_member_basename"])
        )
        with tempfile.TemporaryDirectory(prefix="harmo-ultra-mocap-") as temporary:
            hdf5_path = _extract_verified_member(
                archive_path, verification, Path(temporary)
            )
            result = run_adapter(
                root,
                protocol_path,
                schema_path,
                archive_path,
                hdf5_path,
                repeats=args.repeats,
                chunk_rows=args.chunk_rows,
            )
    outputs = write_results(output_dir, result)
    run_record_path = output_dir / "ultra_mocap_adapter_run.json"
    _write_json(run_record_path, _execution_record(root, result))
    outputs["run_record"] = str(run_record_path)
    summary = dict(result["metrics"])
    summary["outputs"] = outputs
    print(json.dumps(summary, ensure_ascii=False, indent=2, sort_keys=True))
    return 0 if result["metrics"]["pass"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
