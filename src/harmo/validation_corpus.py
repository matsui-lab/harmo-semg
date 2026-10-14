"""Checksum-bound execution of the frozen active-mapping validation corpus."""

from __future__ import annotations

import json
import os
import re
import shutil
import time
import zipfile
from datetime import datetime, timezone
from importlib.metadata import PackageNotFoundError, version
from pathlib import Path, PurePosixPath
from typing import Any, Dict, List, Mapping, Optional, Tuple
from urllib.request import Request, urlopen

import pandas as pd
import yaml

from harmo.real_data_validation import (
    _parse_once,
    file_digest,
    is_safe_zip_member,
    load_channel_definitions,
    load_marker_names,
    mapping_regex,
    normalized_records_digest,
    usable_zip_members,
)
from harmo.validation_study import canonical_sha256, environment_record, file_sha256


CORPUS_VERSION = "1.0.0"
CORPUS_REPRODUCIBILITY_MANIFEST_VERSION = "1.0.0"
CHECKSUM_LENGTHS = {"md5": 32, "sha256": 64}
RUNTIME_DISTRIBUTIONS = (
    "harmo",
    "numpy",
    "pandas",
    "scipy",
    "PyYAML",
    "duckdb",
    "ezc3d",
    "pyarrow",
)


def enabled_mapping_ids(config: Mapping[str, Any], study_id: str) -> List[str]:
    return [
        "{}-{:03d}".format(study_id, index)
        for index, mapping in enumerate(config.get("file_mappings") or [], 1)
        if mapping.get("enabled", True)
    ]


def load_validation_corpus(
    path: Path,
    root: Path,
    validation_registry: Mapping[str, Any],
) -> Dict[str, Any]:
    value = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    errors = validate_validation_corpus(value, root, validation_registry)
    if errors:
        raise ValueError("invalid validation corpus:\n- " + "\n- ".join(errors))
    return value


def validate_validation_corpus(
    value: Mapping[str, Any],
    root: Path,
    validation_registry: Mapping[str, Any],
) -> List[str]:
    errors: List[str] = []
    if value.get("corpus_version") != CORPUS_VERSION:
        errors.append("corpus_version must be {}".format(CORPUS_VERSION))
    if value.get("selection_rule") != (
        "lexicographically first source member matching each enabled mapping "
        "within its declared asset"
    ):
        errors.append("selection_rule is not the frozen deterministic rule")
    try:
        max_timepoints = int(value.get("max_timepoints_per_mapping"))
    except (TypeError, ValueError):
        max_timepoints = 0
    if max_timepoints <= 0:
        errors.append("max_timepoints_per_mapping must be positive")
    try:
        declared_denominator = int(value.get("mapping_denominator"))
    except (TypeError, ValueError):
        declared_denominator = 0
    if declared_denominator <= 0:
        errors.append("mapping_denominator must be positive")

    studies = value.get("studies") or {}
    registry_studies = {
        str(item["id"]): item for item in validation_registry.get("studies") or []
    }
    if set(studies) != set(registry_studies):
        errors.append("corpus studies must exactly match the validation registry")
    all_declared: List[str] = []
    local_names: List[str] = []
    for study_id, study in studies.items():
        expected_config = str(registry_studies.get(study_id, {}).get("config", ""))
        if study.get("config") != expected_config:
            errors.append(
                "{} config does not match validation registry".format(study_id)
            )
        config_path = root / expected_config
        if not config_path.is_file():
            errors.append("{} config is missing".format(study_id))
            enabled = []
        else:
            config = yaml.safe_load(config_path.read_text(encoding="utf-8")) or {}
            enabled = enabled_mapping_ids(config, study_id)
        declared: List[str] = []
        assets = study.get("assets") or {}
        if not isinstance(assets, Mapping) or not assets:
            errors.append("{} requires at least one corpus asset".format(study_id))
            continue
        for asset_id, asset in assets.items():
            prefix = "{} asset {}".format(study_id, asset_id)
            for key in (
                "name",
                "local_name",
                "source_record_doi",
                "file_id",
                "download_url",
                "size_bytes",
                "checksum_algorithm",
                "checksum",
                "container",
                "expected_mappings",
            ):
                if asset.get(key) in (None, ""):
                    errors.append("{} lacks {}".format(prefix, key))
            local_names.append(str(asset.get("local_name", "")))
            if not str(asset.get("download_url", "")).startswith("https://"):
                errors.append("{} download_url must use HTTPS".format(prefix))
            if asset.get("container") not in {"zip", "file"}:
                errors.append("{} container must be zip or file".format(prefix))
            try:
                if int(asset.get("size_bytes")) <= 0:
                    raise ValueError
            except (TypeError, ValueError):
                errors.append("{} size_bytes must be positive".format(prefix))
            algorithm = str(asset.get("checksum_algorithm", "")).lower()
            checksum = str(asset.get("checksum", "")).lower()
            length = CHECKSUM_LENGTHS.get(algorithm)
            if length is None or not re.fullmatch(
                r"[0-9a-f]{{{}}}".format(length or 0), checksum
            ):
                errors.append("{} checksum is invalid".format(prefix))
            mappings = asset.get("expected_mappings") or []
            if not isinstance(mappings, list) or not mappings:
                errors.append("{} expected_mappings must be non-empty".format(prefix))
            declared.extend(str(item) for item in mappings)
        if len(declared) != len(set(declared)):
            errors.append(
                "{} maps an enabled mapping to multiple assets".format(study_id)
            )
        if sorted(declared) != sorted(enabled):
            missing = sorted(set(enabled) - set(declared))
            extra = sorted(set(declared) - set(enabled))
            errors.append(
                "{} corpus coverage differs from config (missing={}, extra={})".format(
                    study_id, missing, extra
                )
            )
        all_declared.extend(declared)
    if len(local_names) != len(set(local_names)):
        errors.append("corpus local_name values must be unique")
    denominator = int(
        (validation_registry.get("experiments") or {})
        .get("A", {})
        .get("denominator", 0)
        or 0
    )
    if declared_denominator != denominator:
        errors.append(
            "corpus mapping_denominator {} does not equal registry {}".format(
                declared_denominator, denominator
            )
        )
    if len(all_declared) != denominator:
        errors.append(
            "corpus mapping denominator {} does not equal registry {}".format(
                len(all_declared), denominator
            )
        )
    return errors


def verify_corpus_asset(path: Path, asset: Mapping[str, Any]) -> Dict[str, Any]:
    """Verify official bytes and inspect ZIP paths without extracting the corpus."""
    if not path.is_file():
        return {
            "path": str(path),
            "exists": False,
            "pass": False,
            "error": "file is missing",
        }
    size_bytes = path.stat().st_size
    algorithm = str(asset["checksum_algorithm"]).lower()
    observed = file_digest(path, algorithm)
    size_pass = size_bytes == int(asset["size_bytes"])
    checksum_pass = observed == str(asset["checksum"]).lower()
    result: Dict[str, Any] = {
        "path": str(path),
        "exists": True,
        "size_bytes": size_bytes,
        "expected_size_bytes": int(asset["size_bytes"]),
        "size_pass": size_pass,
        "checksum_algorithm": algorithm,
        "checksum": observed,
        "expected_checksum": str(asset["checksum"]).lower(),
        "checksum_pass": checksum_pass,
        "sha256": observed if algorithm == "sha256" else file_sha256(path),
        "container": asset["container"],
    }
    if asset["container"] == "zip":
        unsafe: List[str] = []
        duplicates: List[str] = []
        seen = set()
        try:
            with zipfile.ZipFile(path) as archive:
                for info in archive.infolist():
                    if not is_safe_zip_member(info.filename):
                        unsafe.append(info.filename)
                    if info.filename in seen:
                        duplicates.append(info.filename)
                    seen.add(info.filename)
                members = usable_zip_members(archive)
            result.update(
                {
                    "member_count": len(members),
                    "unsafe_members": unsafe,
                    "duplicate_members": duplicates,
                    "archive_index_pass": not unsafe and not duplicates,
                    "selected_member_crc_scope": "verified-while-extracting",
                }
            )
        except zipfile.BadZipFile as exc:
            result.update(
                {
                    "member_count": 0,
                    "unsafe_members": [],
                    "duplicate_members": [],
                    "archive_index_pass": False,
                    "error": "BadZipFile: {}".format(exc),
                }
            )
        result["pass"] = bool(
            size_pass and checksum_pass and result.get("archive_index_pass")
        )
    else:
        result["member_count"] = 1
        result["pass"] = bool(size_pass and checksum_pass)
    return result


def _download_asset(
    url: str,
    destination: Path,
    asset: Mapping[str, Any],
    opener=urlopen,
    retries: int = 3,
) -> Dict[str, Any]:
    """Download one checksum-bound asset atomically, retaining partial bytes."""
    destination.parent.mkdir(parents=True, exist_ok=True)
    partial = destination.with_name(destination.name + ".part")
    expected_size = int(asset["size_bytes"])
    last_error: Optional[Exception] = None
    for attempt in range(1, retries + 1):
        offset = partial.stat().st_size if partial.is_file() else 0
        if offset == expected_size:
            verification = verify_corpus_asset(partial, asset)
            if verification.get("pass"):
                partial.replace(destination)
                verification["path"] = destination.name
                return verification
            partial.unlink()
            offset = 0
        elif offset > expected_size:
            partial.unlink()
            offset = 0
        request = Request(url, headers={"User-Agent": "harmo-g5-validation/1.0"})
        if offset:
            request.add_header("Range", "bytes={}-".format(offset))
        try:
            with opener(request, timeout=120) as response:
                status = int(getattr(response, "status", 200) or 200)
                append = offset > 0 and status == 206
                mode = "ab" if append else "wb"
                with partial.open(mode) as output:
                    shutil.copyfileobj(response, output, length=1024 * 1024)
            if partial.stat().st_size != expected_size:
                raise ValueError(
                    "downloaded size {} does not equal expected {}".format(
                        partial.stat().st_size, expected_size
                    )
                )
            verification = verify_corpus_asset(partial, asset)
            if not verification.get("pass"):
                partial.unlink()
                raise ValueError(
                    "downloaded asset failed checksum or archive validation"
                )
            partial.replace(destination)
            verification["path"] = destination.name
            return verification
        except Exception as exc:
            last_error = exc
            if attempt < retries:
                time.sleep(min(2**attempt, 8))
    raise RuntimeError(
        "failed to download {} after {} attempts: {}".format(
            destination.name, retries, last_error
        )
    )


def download_validation_corpus_assets(
    corpus: Mapping[str, Any],
    data_root: Path,
    opener=urlopen,
    asset_downloader=None,
) -> Dict[str, Any]:
    """Acquire all public corpus assets with size, checksum, and ZIP checks."""
    data_root.mkdir(parents=True, exist_ok=True)
    missing_bytes = 0
    for study in corpus["studies"].values():
        for asset in study["assets"].values():
            destination = data_root / str(asset["local_name"])
            if not destination.exists():
                partial = destination.with_name(destination.name + ".part")
                missing_bytes += max(
                    0,
                    int(asset["size_bytes"])
                    - (partial.stat().st_size if partial.exists() else 0),
                )
    free_bytes = shutil.disk_usage(data_root).free
    reserve_bytes = 512 * 1024 * 1024
    if free_bytes < missing_bytes + reserve_bytes:
        raise OSError(
            "insufficient disk space: need {} bytes plus {} reserve, have {}".format(
                missing_bytes, reserve_bytes, free_bytes
            )
        )

    rows: List[Dict[str, Any]] = []
    for study_id, study in corpus["studies"].items():
        for asset_id, asset in study["assets"].items():
            destination = data_root / str(asset["local_name"])
            if destination.is_file():
                verification = verify_corpus_asset(destination, asset)
                if not verification.get("pass"):
                    raise ValueError(
                        "existing asset is invalid and was not overwritten: {}".format(
                            destination
                        )
                    )
                action = "reused-verified"
            else:
                verification = asset_downloader(asset, destination) if asset_downloader is not None else _download_asset(
                    str(asset["download_url"]), destination, asset, opener=opener
                )
                action = "downloaded-verified"
            rows.append(
                {
                    "study_id": study_id,
                    "asset_id": asset_id,
                    "local_name": asset["local_name"],
                    "action": action,
                    "size_bytes": verification["size_bytes"],
                    "sha256": verification["sha256"],
                    "pass": verification["pass"],
                    "acquisition_backend": verification.get("acquisition_backend", "existing-or-default"),
                    "acquisition": verification.get("acquisition"),
                }
            )
    return {
        "asset_count": len(rows),
        "verified_assets": sum(row["pass"] for row in rows),
        "verified_bytes": sum(int(row["size_bytes"]) for row in rows),
        "assets": rows,
        "pass": bool(rows) and all(row["pass"] for row in rows),
    }


def _runtime_versions() -> Dict[str, Optional[str]]:
    values: Dict[str, Optional[str]] = {}
    for distribution in RUNTIME_DISTRIBUTIONS:
        try:
            values[distribution] = version(distribution)
        except PackageNotFoundError:
            values[distribution] = None
    return values


def _dependency_lock(path: Path) -> Dict[str, str]:
    locked: Dict[str, str] = {}
    for raw_line in path.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#"):
            continue
        if "==" not in line:
            raise ValueError("G5 dependency lock entries must use exact == pins")
        name, expected = line.split("==", 1)
        locked[name] = expected
    if not locked:
        raise ValueError("G5 dependency lock is empty")
    return locked


def create_corpus_reproducibility_manifest(
    root: Path,
    registry: Mapping[str, Any],
    corpus: Mapping[str, Any],
    results_dir: Path,
    environment_id: str,
    run_id: str,
) -> Dict[str, Any]:
    """Create a portable, checksum-complete G5 manifest from one corpus run."""
    expected_environments = registry["experiments"]["D"]["environments"]
    if environment_id not in expected_environments:
        raise ValueError(
            "environment_id must be one of {}".format(expected_environments)
        )
    if not run_id.strip():
        raise ValueError("run_id must be non-empty")

    summary = json.loads(
        (results_dir / "corpus_execution.json").read_text(encoding="utf-8")
    )
    sources = json.loads(
        (results_dir / "source_inventory.json").read_text(encoding="utf-8")
    )
    mappings = json.loads(
        (results_dir / "mapping_outcomes.json").read_text(encoding="utf-8")
    )
    expected_scope = registry["experiments"]["D"]["confirmatory_scope"]
    if summary.get("scope") != expected_scope or summary.get("trust_ready") is not True:
        raise ValueError(
            "corpus result is not a trust-ready {} run".format(expected_scope)
        )
    if len(sources) != 12 or len(mappings) != int(corpus["mapping_denominator"]):
        raise ValueError(
            "corpus result does not retain the frozen source/mapping denominator"
        )

    stable_summary = {
        key: value
        for key, value in summary.items()
        if key not in {"environment", "source_outputs", "mapping_outputs"}
    }
    ordered_sources = sorted(
        sources, key=lambda row: (row["study_id"], row["asset_id"])
    )
    ordered_mappings = sorted(mappings, key=lambda row: row["mapping_id"])
    lock_path = root / "paper/g5-requirements.txt"
    locked_dependencies = _dependency_lock(lock_path)
    runtime_versions = _runtime_versions()
    dependency_lock_pass = all(
        runtime_versions.get(name) == expected
        for name, expected in locked_dependencies.items()
    )
    environment = dict(summary["environment"])
    environment["packages"] = runtime_versions
    environment["github"] = {
        key.lower(): os.environ.get(key)
        for key in ("GITHUB_ACTIONS", "GITHUB_RUN_ID", "GITHUB_RUN_ATTEMPT")
        if os.environ.get(key)
    }
    normalized_record_sha256 = list(summary["normalized_record_sha256"])
    manifest = {
        "manifest_version": CORPUS_REPRODUCIBILITY_MANIFEST_VERSION,
        "validation_scope": expected_scope,
        "run_id": run_id,
        "created_at": datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),
        "environment_id": environment_id,
        "code_commit": environment.get("code_commit"),
        "code_dirty": environment.get("code_dirty") == "true",
        "registry_sha256": canonical_sha256(registry),
        "corpus_sha256": canonical_sha256(corpus),
        "dependency_lock_sha256": file_sha256(lock_path),
        "locked_dependencies": locked_dependencies,
        "dependency_lock_pass": dependency_lock_pass,
        "corpus_summary": stable_summary,
        "source_ledger_sha256": canonical_sha256(ordered_sources),
        "mapping_ledger_sha256": canonical_sha256(ordered_mappings),
        "source_assets": [
            {
                "study_id": row["study_id"],
                "asset_id": row["asset_id"],
                "sha256": row["sha256"],
                "size_bytes": row["size_bytes"],
                "pass": row["pass"],
            }
            for row in ordered_sources
        ],
        "mapping_digests": [
            {
                "mapping_id": row["mapping_id"],
                "source_sha256": row["source_sha256"],
                "config_sha256": row["config_sha256"],
                "normalized_sample_sha256": row["normalized_sample_sha256"],
                "repeat_normalized_sample_sha256": row[
                    "repeat_normalized_sample_sha256"
                ],
                "trust_state": row["trust_state"],
                "semantic_issues": row["semantic_issues"],
            }
            for row in ordered_mappings
        ],
        "numeric_comparison": {
            "mode": "exact-canonical-normalized-record-digest",
            "normalized_record_sha256": normalized_record_sha256,
            "within_run_exact": len(set(normalized_record_sha256)) == 1,
        },
        "steps": [
            "verify 12 checksum-bound public source assets",
            "select the lexicographically first source member for each of 78 mappings",
            "parse every selected mapping twice and retain non-finite values",
            "canonicalize normalized records and compute SHA-256",
        ],
        "environment": environment,
    }
    manifest["pass"] = bool(
        manifest["code_dirty"] is False
        and manifest["dependency_lock_pass"]
        and manifest["numeric_comparison"]["within_run_exact"]
        and summary["pass"] is True
        and all(row["pass"] for row in manifest["source_assets"])
        and all(
            row["normalized_sample_sha256"] == row["repeat_normalized_sample_sha256"]
            for row in manifest["mapping_digests"]
        )
    )
    return manifest


def _logical_zip_members(
    archive: zipfile.ZipFile, asset: Mapping[str, Any]
) -> List[Tuple[str, str]]:
    prefix = str(asset.get("member_prefix_to_strip", "")).strip("/")
    rows = []
    for raw in usable_zip_members(archive):
        logical = raw
        if prefix:
            marker = prefix + "/"
            if not raw.startswith(marker):
                continue
            logical = raw[len(marker) :]
        rows.append((logical, raw))
    return sorted(rows)


def _extract_member(archive_path: Path, raw_member: str, destination: Path) -> Path:
    if not is_safe_zip_member(raw_member):
        raise ValueError("unsafe ZIP member: {}".format(raw_member))
    target = destination.joinpath(*PurePosixPath(raw_member).parts)
    target.parent.mkdir(parents=True, exist_ok=True)
    temporary = target.with_name(target.name + ".part")
    with zipfile.ZipFile(archive_path) as archive:
        info = archive.getinfo(raw_member)
        with archive.open(info) as source, temporary.open("wb") as output:
            shutil.copyfileobj(source, output, length=1024 * 1024)
    temporary.replace(target)
    return target


def _mapping_table(
    config: Mapping[str, Any], study_id: str
) -> Dict[str, Mapping[str, Any]]:
    return {
        "{}-{:03d}".format(study_id, index): mapping
        for index, mapping in enumerate(config.get("file_mappings") or [], 1)
        if mapping.get("enabled", True)
    }


def _mapping_digest(frame: pd.DataFrame, mapping_id: str) -> Optional[str]:
    selected = frame[frame["mapping_id"] == mapping_id]
    return normalized_records_digest(selected) if not selected.empty else None


def execute_validation_corpus(
    root: Path,
    corpus: Mapping[str, Any],
    data_root: Path,
    work_root: Path,
    allow_subset: bool = False,
) -> Tuple[List[Dict[str, Any]], List[Dict[str, Any]], Dict[str, Any]]:
    """Execute every frozen mapping twice and preserve every outcome."""
    source_rows: List[Dict[str, Any]] = []
    mapping_rows: List[Dict[str, Any]] = []
    repeat_mapping_rows: List[Dict[str, Any]] = []
    first_frames: List[pd.DataFrame] = []
    second_frames: List[pd.DataFrame] = []
    max_timepoints = int(corpus["max_timepoints_per_mapping"])

    for study_id, study in corpus["studies"].items():
        config_path = root / str(study["config"])
        config = yaml.safe_load(config_path.read_text(encoding="utf-8")) or {}
        mappings = _mapping_table(config, study_id)
        if allow_subset:
            selected_ids = {mid for asset in study["assets"].values() for mid in asset["expected_mappings"]}
            if not selected_ids <= set(mappings):
                raise ValueError("subset contains unknown mapping IDs")
            mappings = {mid: mapping for mid, mapping in mappings.items() if mid in selected_ids}
        has_mat_mapping = any(
            str(mapping.get("file_format", "")).lower() == "mat"
            for mapping in mappings.values()
        )
        if has_mat_mapping:
            channels, channel_record = load_channel_definitions(config_path, config)
        else:
            channels, channel_record = (
                {},
                {
                    "path": None,
                    "sha256": None,
                    "channels": 0,
                    "status": "not-required-no-active-mat-mappings",
                },
            )
        markers, marker_record = load_marker_names(config_path, config)
        rows_by_id: Dict[str, Dict[str, Any]] = {}
        extracted: Dict[str, Path] = {}
        for asset_id, asset in study["assets"].items():
            source_path = data_root / str(asset["local_name"])
            verification = verify_corpus_asset(source_path, asset)
            verification["path"] = str(asset["local_name"])
            verification["path_scope"] = "data-root-relative"
            source_row = {
                "study_id": study_id,
                "asset_id": asset_id,
                "source_record_doi": asset["source_record_doi"],
                "file_id": asset["file_id"],
                "name": asset["name"],
                "local_name": asset["local_name"],
                "expected_mappings": list(asset["expected_mappings"]),
                **verification,
            }
            source_rows.append(source_row)
            if not verification["pass"]:
                for mapping_id in asset["expected_mappings"]:
                    mapping = mappings[mapping_id]
                    rows_by_id[mapping_id] = {
                        "study_id": study_id,
                        "mapping_id": mapping_id,
                        "mapping_name": mapping.get("name", mapping_id),
                        "input_path_template": mapping.get("input_path_template"),
                        "file_format": mapping.get("file_format"),
                        "reader_plugin": mapping.get("reader_plugin"),
                        "asset_id": asset_id,
                        "matched_files": 0,
                        "selected_member": None,
                        "source_checksum_pass": False,
                        "outcome": "failed",
                        "error": "source asset failed checksum/index verification",
                    }
                continue

            if asset["container"] == "zip":
                with zipfile.ZipFile(source_path) as archive:
                    member_pairs = _logical_zip_members(archive, asset)
            else:
                member_pairs = [
                    (
                        str(asset.get("logical_path") or asset["name"]),
                        str(asset["name"]),
                    )
                ]
            for mapping_id in asset["expected_mappings"]:
                mapping = mappings[mapping_id]
                regex = mapping_regex(mapping)
                matches = [
                    (logical, raw)
                    for logical, raw in member_pairs
                    if regex.match(logical)
                ]
                selected = matches[0] if matches else None
                groups = regex.match(selected[0]).groupdict() if selected else {}
                session_template = str(mapping.get("session_id_template", ""))
                session_error = None
                try:
                    session_id = (
                        session_template.format(**groups) if session_template else None
                    )
                except KeyError as exc:
                    session_id = None
                    session_error = "session_id_template lacks path group {}".format(
                        exc
                    )
                key = "{}::{}".format(asset_id, selected[1]) if selected else None
                row = {
                    "study_id": study_id,
                    "mapping_id": mapping_id,
                    "mapping_name": mapping.get("name", mapping_id),
                    "input_path_template": mapping.get("input_path_template"),
                    "file_format": mapping.get("file_format"),
                    "reader_plugin": mapping.get("reader_plugin"),
                    "asset_id": asset_id,
                    "matched_files": len(matches),
                    "selected_member": key,
                    "source_archive_member": selected[1] if selected else None,
                    "logical_source_member": selected[0] if selected else None,
                    "subject_id": groups.get("sub_id"),
                    "session_id": session_id,
                    "path_metadata": groups,
                    "source_checksum_pass": True,
                    "outcome": "discovered" if selected else "missing",
                    "error": session_error
                    or (
                        None
                        if selected
                        else "no source member matched the configured template"
                    ),
                }
                rows_by_id[mapping_id] = row
                if selected:
                    if asset["container"] == "zip":
                        extracted[key] = _extract_member(
                            source_path,
                            selected[1],
                            work_root / study_id / asset_id,
                        )
                    else:
                        extracted[key] = source_path

        ordered_rows = [rows_by_id[mapping_id] for mapping_id in sorted(mappings)]
        first_frame, first_ledger = _parse_once(
            ordered_rows,
            config,
            extracted,
            channels,
            max_timepoints,
            marker_names=markers,
        )
        second_frame, second_ledger = _parse_once(
            ordered_rows,
            config,
            extracted,
            channels,
            max_timepoints,
            marker_names=markers,
        )
        second_by_id = {row["mapping_id"]: row for row in second_ledger}
        for row in first_ledger:
            mapping_id = row["mapping_id"]
            repeat = second_by_id[mapping_id]
            first_digest = _mapping_digest(first_frame, mapping_id)
            second_digest = _mapping_digest(second_frame, mapping_id)
            row["outcome"] = row.pop("parse_outcome")
            row["repeat_outcome"] = repeat.get("parse_outcome")
            row["normalized_sample_sha256"] = first_digest
            row["repeat_normalized_sample_sha256"] = second_digest
            row["reproducibility_pass"] = bool(
                row["outcome"] == "converted"
                and repeat.get("parse_outcome") == "converted"
                and first_digest == second_digest
            )
            row["config_sha256"] = file_sha256(config_path)
            row["channel_definitions"] = channel_record
            row["marker_definitions"] = marker_record
            mapping_rows.append(row)
        repeat_mapping_rows.extend(second_ledger)
        if not first_frame.empty:
            first_frames.append(first_frame)
        if not second_frame.empty:
            second_frames.append(second_frame)

    first_all = (
        pd.concat(first_frames, ignore_index=True) if first_frames else pd.DataFrame()
    )
    second_all = (
        pd.concat(second_frames, ignore_index=True) if second_frames else pd.DataFrame()
    )
    first_digest = normalized_records_digest(first_all) if not first_all.empty else None
    second_digest = (
        normalized_records_digest(second_all) if not second_all.empty else None
    )
    outcome_counts = {
        state: sum(row.get("outcome") == state for row in mapping_rows)
        for state in ("converted", "failed", "missing", "source-absent-declared")
    }
    summary = {
        "corpus_version": corpus["corpus_version"],
        "corpus_digest": canonical_sha256(corpus),
        "scope": "frozen-{}-mapping-corpus".format(len(mapping_rows)),
        "selection_rule": corpus["selection_rule"],
        "max_timepoints_per_mapping": max_timepoints,
        "source_asset_count": len(source_rows),
        "verified_source_assets": sum(row.get("pass") is True for row in source_rows),
        "verified_source_bytes": sum(
            int(row.get("size_bytes", 0))
            for row in source_rows
            if row.get("pass") is True
        ),
        "mapping_denominator": len(mapping_rows),
        "outcomes": outcome_counts,
        "selected_source_members": len(
            {
                row.get("selected_member")
                for row in mapping_rows
                if row.get("selected_member")
            }
        ),
        "normalized_sample_rows": len(first_all),
        "sampled_timepoints": sum(
            int(row.get("sample_timepoints", 0)) for row in mapping_rows
        ),
        "sampled_channels": sum(
            int(row.get("sample_channels", 0)) for row in mapping_rows
        ),
        "nonfinite_sample_rows": sum(
            int(row.get("nonfinite_rows", 0)) for row in mapping_rows
        ),
        "traceable_format_ready_mappings": sum(
            row.get("trust_state") == "traceable-format-ready" for row in mapping_rows
        ),
        "conditionally_comparable_mappings": sum(
            row.get("trust_state") == "conditionally-comparable" for row in mapping_rows
        ),
        "semantic_issue_count": sum(
            len(row.get("semantic_issues") or []) for row in mapping_rows
        ),
        "normalized_record_sha256": [first_digest, second_digest],
        "within_environment_reproducible": bool(
            first_digest is not None
            and first_digest == second_digest
            and all(row.get("reproducibility_pass") for row in mapping_rows)
        ),
        "environment": environment_record(),
    }
    summary["computational_pass"] = bool(
        len(mapping_rows) == int(corpus["mapping_denominator"])
        and outcome_counts["converted"] == int(corpus["mapping_denominator"])
        and summary["verified_source_assets"] == summary["source_asset_count"]
        and summary["within_environment_reproducible"]
    )
    summary["trust_ready"] = bool(
        summary["computational_pass"]
        and summary["conditionally_comparable_mappings"] == 0
        and summary["semantic_issue_count"] == 0
    )
    summary["pass"] = summary["trust_ready"]
    return source_rows, mapping_rows, summary
