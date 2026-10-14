"""Traceable acquisition and pilot validation for public source archives.

This module keeps downloaded data outside the repository.  A small, versioned
release registry supplies authoritative file identifiers and checksums; the
pilot then records a complete mapping denominator, parser outcomes, and stable
digests without silently dropping failures.
"""
from __future__ import annotations

import hashlib
import importlib
import json
import math
import os
import re
import shutil
import struct
import urllib.request
import zipfile
from pathlib import Path, PurePosixPath
from typing import Any, Dict, List, Mapping, Optional, Sequence, Tuple

import pandas as pd
import yaml

from harmo.validation_study import environment_record, file_sha256


SOURCE_REGISTRY_VERSION = "1.1.0"


def load_source_registry(path: Path) -> Dict[str, Any]:
    """Load and validate the public-source release registry."""
    value = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    errors = validate_source_registry(value)
    if errors:
        raise ValueError("invalid source registry:\n- " + "\n- ".join(errors))
    return value


def validate_source_registry(value: Mapping[str, Any]) -> List[str]:
    errors: List[str] = []
    if value.get("registry_version") != SOURCE_REGISTRY_VERSION:
        errors.append("registry_version must be {}".format(SOURCE_REGISTRY_VERSION))
    sources = value.get("sources")
    if not isinstance(sources, Mapping) or not sources:
        errors.append("sources must be a non-empty mapping")
        return errors
    for study_id, source in sources.items():
        if not isinstance(source, Mapping):
            errors.append("source {} must be a mapping".format(study_id))
            continue
        for key in (
            "doi",
            "landing_page",
            "license",
            "license_url",
            "redistribution",
            "license_evidence",
            "release",
        ):
            if not source.get(key):
                errors.append("source {} lacks {}".format(study_id, key))
        evidence = source.get("license_evidence") or {}
        if not isinstance(evidence, Mapping):
            errors.append("source {} license_evidence must be a mapping".format(study_id))
        else:
            if evidence.get("evidence_type") != "official-repository-metadata":
                errors.append(
                    "source {} license evidence must use official repository metadata".format(
                        study_id
                    )
                )
            urls = evidence.get("urls") or []
            if isinstance(urls, str):
                urls = [urls]
            if not urls or not all(str(url).startswith("https://") for url in urls):
                errors.append("source {} license evidence requires HTTPS URLs".format(study_id))
            if not evidence.get("checked_at"):
                errors.append("source {} license evidence lacks checked_at".format(study_id))
        release = source.get("release") or {}
        if not isinstance(release, Mapping):
            errors.append("source {} release must be a mapping".format(study_id))
        else:
            for key in ("repository", "record_type", "version_status"):
                if not release.get(key):
                    errors.append("source {} release lacks {}".format(study_id, key))
            if release.get("version_status") != "frozen":
                errors.append("source {} release version_status must be frozen".format(study_id))
            manifest = release.get("manifest")
            records = release.get("records")
            if not manifest and not records:
                errors.append("source {} release lacks manifest or records".format(study_id))
            if manifest:
                if not isinstance(manifest, Mapping):
                    errors.append("source {} release manifest must be a mapping".format(study_id))
                else:
                    for key in (
                        "article_count",
                        "article_ids",
                        "file_count",
                        "licenses",
                        "manifest_sha256",
                        "total_size_bytes",
                        "versions",
                    ):
                        if manifest.get(key) in (None, ""):
                            errors.append(
                                "source {} release manifest lacks {}".format(study_id, key)
                            )
                    if not re.match(
                        r"^[0-9a-f]{64}$", str(manifest.get("manifest_sha256", ""))
                    ):
                        errors.append(
                            "source {} release manifest has an invalid SHA-256".format(
                                study_id
                            )
                        )
            if records:
                if not isinstance(records, list) or not all(
                    isinstance(record, Mapping) and record.get("doi")
                    and record.get("version") and record.get("landing_page")
                    for record in records
                ):
                    errors.append("source {} release records are invalid".format(study_id))
        assets = source.get("assets")
        if assets is None:
            continue
        if not isinstance(assets, Mapping) or not assets:
            errors.append("source {} assets must be non-empty when present".format(study_id))
            continue
        for asset_id, asset in assets.items():
            prefix = "source {} asset {}".format(study_id, asset_id)
            if not isinstance(asset, Mapping):
                errors.append("{} must be a mapping".format(prefix))
                continue
            for key in ("file_id", "name", "download_url", "size_bytes", "md5"):
                if asset.get(key) in (None, ""):
                    errors.append("{} lacks {}".format(prefix, key))
            if not re.match(r"^[0-9a-f]{32}$", str(asset.get("md5", ""))):
                errors.append("{} has an invalid MD5".format(prefix))
            if not isinstance(asset.get("size_bytes"), int) or asset.get("size_bytes", 0) <= 0:
                errors.append("{} has an invalid size_bytes".format(prefix))
            absences = asset.get("declared_absent_mappings") or []
            if not isinstance(absences, list):
                errors.append("{} declared_absent_mappings must be a list".format(prefix))
                continue
            mapping_ids = []
            for absence in absences:
                if not isinstance(absence, Mapping):
                    errors.append("{} has an invalid declared absence".format(prefix))
                    continue
                mapping_id = str(absence.get("mapping_id", ""))
                mapping_ids.append(mapping_id)
                if not re.match(r"^study_[0-9]+-[0-9]{3}$", mapping_id):
                    errors.append("{} has an invalid absent mapping_id".format(prefix))
                if not absence.get("reason") or not absence.get("evidence_basis"):
                    errors.append(
                        "{} absence {} requires reason and evidence_basis".format(
                            prefix, mapping_id
                        )
                    )
            if len(mapping_ids) != len(set(mapping_ids)):
                errors.append("{} has duplicate declared absent mappings".format(prefix))
    return errors


def source_asset(
    registry: Mapping[str, Any], study_id: str, asset_id: str
) -> Tuple[Mapping[str, Any], Mapping[str, Any]]:
    try:
        source = registry["sources"][study_id]
        asset = source["assets"][asset_id]
    except KeyError as exc:
        raise KeyError("unknown source asset {}/{}".format(study_id, asset_id)) from exc
    return source, asset


def file_digest(path: Path, algorithm: str) -> str:
    digest = hashlib.new(algorithm)
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def verify_source_archive(path: Path, asset: Mapping[str, Any]) -> Dict[str, Any]:
    """Verify bytes against the official release record and audit ZIP safety."""
    size_bytes = path.stat().st_size
    md5 = file_digest(path, "md5")
    sha256 = file_sha256(path)
    expected_size = int(asset["size_bytes"])
    expected_md5 = str(asset["md5"]).lower()
    integrity_pass = size_bytes == expected_size and md5 == expected_md5

    member_count = 0
    unsafe_members: List[str] = []
    duplicate_members: List[str] = []
    seen = set()
    archive_test_error: Optional[str] = None
    try:
        with zipfile.ZipFile(str(path)) as archive:
            bad_member = archive.testzip()
            if bad_member:
                archive_test_error = "CRC failure: {}".format(bad_member)
            for info in archive.infolist():
                member_count += 1
                if not is_safe_zip_member(info.filename):
                    unsafe_members.append(info.filename)
                if info.filename in seen:
                    duplicate_members.append(info.filename)
                seen.add(info.filename)
    except zipfile.BadZipFile as exc:
        archive_test_error = str(exc)

    archive_pass = (
        integrity_pass
        and archive_test_error is None
        and not unsafe_members
        and not duplicate_members
    )
    return {
        "path": str(path),
        "size_bytes": size_bytes,
        "expected_size_bytes": expected_size,
        "md5": md5,
        "expected_md5": expected_md5,
        "sha256": sha256,
        "integrity_pass": integrity_pass,
        "member_count": member_count,
        "unsafe_members": unsafe_members,
        "duplicate_members": duplicate_members,
        "archive_test_error": archive_test_error,
        "pass": archive_pass,
    }


def fetch_source_archive(
    asset: Mapping[str, Any], destination: Path, resume: bool = True
) -> Dict[str, Any]:
    """Download to a partial file, resume when possible, and verify before move."""
    destination.parent.mkdir(parents=True, exist_ok=True)
    if destination.is_file():
        verified = verify_source_archive(destination, asset)
        if verified["pass"]:
            verified["downloaded"] = False
            return verified

    partial = destination.with_suffix(destination.suffix + ".part")
    offset = partial.stat().st_size if resume and partial.is_file() else 0
    request = urllib.request.Request(str(asset["download_url"]))
    if offset:
        request.add_header("Range", "bytes={}-".format(offset))
    with urllib.request.urlopen(request) as response:  # nosec B310: registry URL is explicit
        status = getattr(response, "status", None) or response.getcode()
        append = bool(offset and status == 206)
        with partial.open("ab" if append else "wb") as stream:
            shutil.copyfileobj(response, stream, length=1024 * 1024)

    verified = verify_source_archive(partial, asset)
    if not verified["pass"]:
        raise ValueError(
            "downloaded archive failed verification: {}".format(
                json.dumps(verified, sort_keys=True)
            )
        )
    os.replace(str(partial), str(destination))
    verified["path"] = str(destination)
    verified["downloaded"] = True
    return verified


def is_safe_zip_member(name: str) -> bool:
    if not name or "\\" in name or "\x00" in name:
        return False
    path = PurePosixPath(name)
    return not path.is_absolute() and ".." not in path.parts and "" not in path.parts


def mapping_regex(mapping: Mapping[str, Any]) -> re.Pattern:
    template = str(mapping.get("input_path_template", ""))
    patterns = mapping.get("path_patterns") or {}
    regex_text = re.escape(template)
    seen = set()
    for name in re.findall(r"\{([^{}]+)\}", template):
        pattern = str(patterns.get(name, "[^/]+"))
        replacement = (
            "(?P={})".format(name)
            if name in seen
            else "(?P<{}>{})".format(name, pattern)
        )
        regex_text = regex_text.replace(
            re.escape("{" + name + "}"), replacement, 1
        )
        seen.add(name)
    return re.compile("^" + regex_text + "$")


def usable_zip_members(archive: zipfile.ZipFile) -> List[str]:
    members = []
    for info in archive.infolist():
        name = info.filename
        if (
            info.is_dir()
            or name.startswith("__MACOSX/")
            or PurePosixPath(name).name == ".DS_Store"
        ):
            continue
        if not is_safe_zip_member(name):
            continue
        members.append(name)
    return sorted(members)


def audit_archive_mappings(
    archive_path: Path, config: Mapping[str, Any], study_id: str
) -> Tuple[List[Dict[str, Any]], List[str]]:
    """Match every enabled config rule directly against archive member paths."""
    rows: List[Dict[str, Any]] = []
    with zipfile.ZipFile(str(archive_path)) as archive:
        members = usable_zip_members(archive)
    for index, mapping in enumerate(config.get("file_mappings") or []):
        if not mapping.get("enabled", True):
            continue
        mapping_id = "{}-{:03d}".format(study_id, index + 1)
        regex = mapping_regex(mapping)
        matches = sorted(name for name in members if regex.match(name))
        selected = matches[0] if matches else None
        groups = regex.match(selected).groupdict() if selected else {}
        session_template = str(mapping.get("session_id_template", ""))
        try:
            session_id = session_template.format(**groups) if session_template else None
        except KeyError:
            session_id = None
        rows.append({
            "study_id": study_id,
            "mapping_id": mapping_id,
            "mapping_name": mapping.get("name", mapping_id),
            "input_path_template": mapping.get("input_path_template"),
            "file_format": mapping.get("file_format"),
            "reader_plugin": mapping.get("reader_plugin"),
            "matched_files": len(matches),
            "selected_member": selected,
            "subject_id": groups.get("sub_id"),
            "session_id": session_id,
            "path_metadata": groups,
            "outcome": "discovered" if matches else "missing",
            "error": None if matches else "no archive member matched the configured template",
        })
    return rows, members


def reconcile_declared_absences(
    mapping_rows: Sequence[Mapping[str, Any]], asset: Mapping[str, Any]
) -> List[Dict[str, Any]]:
    """Bind registered source absences to missing mapping rows, fail closed."""
    rows = [dict(row) for row in mapping_rows]
    declarations = {
        str(item["mapping_id"]): dict(item)
        for item in asset.get("declared_absent_mappings") or []
    }
    known_mapping_ids = {str(row["mapping_id"]) for row in rows}
    unknown = sorted(set(declarations) - known_mapping_ids)
    if unknown:
        raise ValueError(
            "declared absences reference unknown mappings: {}".format(
                ", ".join(unknown)
            )
        )
    for row in rows:
        mapping_id = str(row["mapping_id"])
        declaration = declarations.get(mapping_id)
        if row.get("selected_member"):
            if declaration:
                raise ValueError(
                    "mapping {} is declared absent but matches source files".format(
                        mapping_id
                    )
                )
            continue
        if declaration:
            row["outcome"] = "source-absent-declared"
            row["declared_absence"] = declaration
            row["error"] = None
    return rows


def mapping_semantic_issues(mapping: Mapping[str, Any]) -> List[str]:
    """Check format-level metadata needed for unambiguous downstream queries."""
    file_format = str(mapping.get("file_format", "")).lower()
    if not file_format:
        suffix = Path(str(mapping.get("input_path_template", ""))).suffix
        file_format = suffix.lower().lstrip(".")
    metadata = mapping.get("static_metadata") or {}
    issues: List[str] = []
    expected = {
        "trc": {"sensor_type": "motion-capture", "data_type": "marker"},
        "mot": {"sensor_type": "force-plate", "data_type": "force-plate"},
        "sto": {"sensor_type": "force-plate", "data_type": "force-plate"},
    }.get(file_format, {})
    for field, value in expected.items():
        observed = metadata.get(field)
        if observed != value:
            issues.append(
                "{} metadata must be {!r}, observed {!r}".format(
                    field, value, observed
                )
            )
    if file_format == "trc" and not metadata.get("unit"):
        issues.append("TRC marker metadata requires an explicit unit")
    if file_format in {"csv", "txt", "tsv"} and not mapping.get("multi_rate_info"):
        if mapping.get("add_percentage_column"):
            if mapping.get("time_unit") != "%gait-cycle":
                issues.append(
                    "normalized delimited mapping requires time_unit='%gait-cycle'"
                )
            reference = str(mapping.get("normalized_axis_reference", ""))
            if not reference.startswith(("https://doi.org/", "http://doi.org/")):
                issues.append("normalized gait-cycle axis requires a DOI reference")
        elif mapping.get("time_unit") != "s":
            issues.append("delimited signal mapping requires time_unit='s'")
    if file_format == "mat" and mapping.get("reader_plugin"):
        reader_options = mapping.get("reader_options") or {}
        if reader_options.get("embedded_time_column_candidates"):
            if reader_options.get("time_unit") != "s":
                issues.append("embedded MAT time column requires time_unit='s'")
            return issues
        if reader_options.get("time_key") or reader_options.get("fs_key"):
            return issues
        options = mapping.get("plugin_options") or {}
        if options.get("time_axis_semantics") != "sample-index-from-zero":
            issues.append(
                "MAT plugin requires time_axis_semantics='sample-index-from-zero'"
            )
        try:
            sampling_rate_hz = float(options.get("sampling_rate_hz"))
        except (TypeError, ValueError):
            sampling_rate_hz = 0.0
        if not math.isfinite(sampling_rate_hz) or sampling_rate_hz <= 0:
            issues.append("MAT plugin requires a positive finite sampling_rate_hz")
        reference = str(options.get("sampling_rate_reference", ""))
        if not reference.startswith(("https://doi.org/", "http://doi.org/")):
            issues.append("MAT plugin requires a DOI sampling_rate_reference")
    return issues


def load_channel_definitions(
    config_path: Path, config: Mapping[str, Any]
) -> Tuple[Dict[str, List[Dict[str, Any]]], Dict[str, Any]]:
    """Load the configured channel dimension used to enrich MAT output."""
    definition = (config.get("definitions") or {}).get("channels") or {}
    raw_path = definition.get("path")
    if not raw_path:
        raise ValueError("study config has no channel definition path")
    path = (config_path.parent / str(raw_path)).resolve()
    value = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    key = definition.get("yaml_key", "channels")
    rows = value.get(key) or []
    channels: Dict[str, List[Dict[str, Any]]] = {}
    for row in rows:
        name = str(row.get("column_name", ""))
        if not name:
            raise ValueError("channel definition lacks column_name")
        channels.setdefault(name, []).append(dict(row))
    return channels, {
        "path": str(path.relative_to(config_path.parents[2])),
        "sha256": file_sha256(path),
        "definitions": len(rows),
        "channels": len(channels),
        "ambiguous_channel_names": sorted(
            name for name, definitions in channels.items() if len(definitions) > 1
        ),
    }


def _resolve_channel_definition(
    definitions: Any, mapping_metadata: Mapping[str, Any]
) -> Optional[Dict[str, Any]]:
    """Resolve repeated source labels using the mapping's modality context."""
    if isinstance(definitions, Mapping):
        candidates = [dict(definitions)]
    else:
        candidates = [dict(value) for value in (definitions or [])]
    if len(candidates) == 1:
        return candidates[0]
    contextual_fields = ("sensor_type", "data_type", "physical_quantity", "unit")
    compatible = []
    for candidate in candidates:
        conflict = any(
            mapping_metadata.get(field) not in (None, "")
            and candidate.get(field) not in (None, "")
            and str(mapping_metadata[field]).lower() != str(candidate[field]).lower()
            for field in contextual_fields
        )
        if not conflict:
            compatible.append(candidate)
    return compatible[0] if len(compatible) == 1 else None


def load_marker_names(
    config_path: Path, config: Mapping[str, Any]
) -> Tuple[List[str], Dict[str, Any]]:
    """Load marker names from either a flat or body-segment YAML definition."""
    definition = (config.get("definitions") or {}).get("markers") or {}
    raw_path = definition.get("path")
    if not raw_path:
        return [], {"path": None, "sha256": None, "markers": 0}
    path = (config_path.parent / str(raw_path)).resolve()
    value = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    key = definition.get("yaml_key", "marker_config")
    root = value.get(key) or []
    names = set()

    def walk(node: Any) -> None:
        if isinstance(node, Mapping):
            for candidate in ("marker", "column_name", "name"):
                if node.get(candidate):
                    names.add(str(node[candidate]).strip())
                    break
            for child in node.values():
                walk(child)
        elif isinstance(node, list):
            for child in node:
                walk(child)

    walk(root)
    return sorted(names), {
        "path": str(path.relative_to(config_path.parents[2])),
        "sha256": file_sha256(path),
        "markers": len(names),
    }


def extract_selected_members(
    archive_path: Path,
    selected_members: Sequence[str],
    destination: Path,
) -> Dict[str, Path]:
    """Extract only the deterministic mapping representatives."""
    destination.mkdir(parents=True, exist_ok=True)
    outputs: Dict[str, Path] = {}
    with zipfile.ZipFile(str(archive_path)) as archive:
        by_name = {info.filename: info for info in archive.infolist()}
        for name in sorted(set(selected_members)):
            if not is_safe_zip_member(name):
                raise ValueError("unsafe ZIP member: {}".format(name))
            info = by_name.get(name)
            if info is None or info.is_dir():
                raise KeyError("selected ZIP member is unavailable: {}".format(name))
            target = destination.joinpath(*PurePosixPath(name).parts)
            target.parent.mkdir(parents=True, exist_ok=True)
            with archive.open(info) as source, target.open("wb") as output:
                shutil.copyfileobj(source, output, length=1024 * 1024)
            outputs[name] = target
    return outputs


def _deterministic_numeric(values: pd.Series) -> pd.Series:
    """Coerce scalars with CPython's correctly rounded float parser."""

    def convert(value: Any) -> float:
        try:
            if value is None or bool(pd.isna(value)):
                return float("nan")
        except (TypeError, ValueError):
            return float("nan")
        try:
            return float(value)
        except (TypeError, ValueError, OverflowError):
            return float("nan")

    return values.map(convert).astype("float64")


def _wide_to_long_sample(
    frame: pd.DataFrame,
    max_timepoints: int,
    time_column_candidates: Sequence[str] = ("time",),
    sampling_rate_hz: Optional[float] = None,
) -> Tuple[pd.DataFrame, int, str]:
    source_rows = len(frame)
    candidate_names = {str(name).lower() for name in time_column_candidates}
    time_columns = [
        name for name in frame.columns if str(name).lower() in candidate_names
    ]
    if not time_columns:
        if sampling_rate_hz is None or not math.isfinite(sampling_rate_hz):
            raise ValueError("parsed frame has no time column or finite sampling rate")
        if sampling_rate_hz <= 0:
            raise ValueError("sampling rate must be positive")
        frame = frame.copy()
        frame.insert(0, "time", pd.Series(range(len(frame))) / sampling_rate_hz)
        time_columns = ["time"]
        time_provenance = "mapping-derived-from-sampling-rate"
    else:
        time_provenance = "source-declared"
    time_col = time_columns[0]
    excluded = {time_col, "frame_num", "frame#"}
    channels = []
    converted_columns: Dict[Any, pd.Series] = {}
    for column in frame.columns:
        if column in excluded:
            continue
        converted = _deterministic_numeric(frame[column])
        if converted.notna().any():
            converted_columns[column] = converted
            channels.append(column)
    if not channels:
        raise ValueError("parsed frame has no numeric signal channels")
    if len(frame) <= max_timepoints:
        selected_indices = list(range(len(frame)))
    elif max_timepoints == 1:
        selected_indices = [0]
    else:
        selected_indices = sorted(
            set(
                int(round(index * (len(frame) - 1) / (max_timepoints - 1)))
                for index in range(max_timepoints)
            )
        )
    sample = frame.iloc[selected_indices].copy()
    for column in channels:
        sample[column] = converted_columns[column].iloc[selected_indices].to_numpy()
    sample[time_col] = _deterministic_numeric(sample[time_col])
    long = sample.melt(
        id_vars=[time_col], value_vars=channels, var_name="channel", value_name="value"
    ).rename(columns={time_col: "time"})
    return long, source_rows, time_provenance


def _configured_sampling_rate(path: Path, mapping: Mapping[str, Any]) -> Optional[float]:
    """Resolve an explicit mapping rate, including filename-qualified rates."""
    options = mapping.get("plugin_options") or {}
    raw_rate = options.get("sampling_rate_hz")
    if raw_rate is not None:
        try:
            rate = float(raw_rate)
        except (TypeError, ValueError):
            return None
        return rate if math.isfinite(rate) and rate > 0 else None
    name = path.name
    for row in mapping.get("multi_rate_info") or []:
        pattern = str(row.get("pattern", ""))
        match_type = str(row.get("match_type", "contains"))
        matched = (
            pattern in name
            if match_type == "contains"
            else bool(re.search(pattern, name))
        )
        if matched:
            try:
                rate = float(row.get("sampling_rate"))
            except (TypeError, ValueError):
                return None
            return rate if math.isfinite(rate) and rate > 0 else None
    return None


def _apply_channel_filter(
    long: pd.DataFrame, mapping: Mapping[str, Any]
) -> pd.DataFrame:
    """Apply the simple channel-regex predicate used by current mappings."""
    expression = str(mapping.get("filter_condition", ""))
    if not expression:
        return long
    matched = re.fullmatch(r"\s*channel\s+rlike\s+(['\"])(.*?)\1\s*", expression)
    if matched is None:
        raise ValueError("unsupported filter_condition: {}".format(expression))
    filtered = long[long["channel"].astype(str).str.contains(matched.group(2), regex=True)]
    if filtered.empty:
        raise ValueError("filter_condition selected no channels")
    return filtered.reset_index(drop=True)


def _parse_multirate_csv_sample(
    frame: pd.DataFrame,
    mapping: Mapping[str, Any],
    max_timepoints: int,
) -> Tuple[pd.DataFrame, int, List[float]]:
    """Normalize source-header-declared multi-rate channels independently."""
    rate_rules = mapping.get("multi_rate_info") or []
    parts: List[pd.DataFrame] = []
    observed_rates = set()
    for column in frame.columns:
        labels = column if isinstance(column, tuple) else (column,)
        clean_labels = [
            str(value).strip()
            for value in labels
            if str(value).strip() and not str(value).startswith("Unnamed:")
        ]
        label = " | ".join(clean_labels)
        matched_rule = None
        for rule in rate_rules:
            pattern = str(rule.get("pattern", ""))
            match_type = str(rule.get("match_type", "contains"))
            matched = (
                pattern in label
                if match_type == "contains"
                else bool(re.search(pattern, label))
            )
            if matched:
                matched_rule = rule
                break
        if matched_rule is None:
            continue
        header_rates = re.findall(r"(\d+(?:\.\d+)?)\s*Hz", label, re.IGNORECASE)
        if not header_rates:
            raise ValueError(
                "multi-rate channel header lacks a source-declared Hz value: {}".format(
                    label
                )
            )
        unique_header_rates = sorted(set(float(value) for value in header_rates))
        declared_rate = float(matched_rule["sampling_rate"])
        if unique_header_rates != [declared_rate]:
            raise ValueError(
                "multi-rate header/config mismatch for {}: {} versus {}".format(
                    label, unique_header_rates, declared_rate
                )
            )
        values = _deterministic_numeric(frame[column]).dropna().head(
            max_timepoints
        )
        if values.empty:
            continue
        observed_rates.add(declared_rate)
        parts.append(
            pd.DataFrame(
                {
                    "time": pd.Series(range(len(values)), dtype="float64")
                    / declared_rate,
                    "channel": label,
                    "value": values.to_numpy(dtype="float64"),
                }
            )
        )
    if not parts:
        raise ValueError("multi-rate mapping selected no numeric source channels")
    return pd.concat(parts, ignore_index=True), len(frame), sorted(observed_rates)


def parse_mapping_sample(
    path: Path,
    mapping: Mapping[str, Any],
    max_timepoints: int,
) -> Tuple[pd.DataFrame, Dict[str, Any]]:
    """Exercise the configured reader and return a bounded normalized sample."""
    configured_format = str(mapping.get("file_format", "")).lower()
    suffix_format = path.suffix.lower().lstrip(".")
    file_format = configured_format or suffix_format
    plugin_name = mapping.get("reader_plugin")
    plugin_version: Optional[str] = None
    source_rows: Optional[int] = None
    sampling_rate_hz: Optional[float] = None
    sampling_rates_hz: Optional[List[float]] = None
    sampling_rate_reference: Optional[str] = None
    time_axis_semantics: Optional[str] = None
    time_unit: Optional[str] = None
    time_transform = "identity"
    if file_format == "mat" and plugin_name:
        module = importlib.import_module("harmo.ingest.plugins.{}".format(plugin_name))
        plugin_factory = getattr(module, "Plugin", None) or getattr(
            module, "PluginInstance", None
        )
        if plugin_factory is None:
            raise AttributeError("plugin {} has no Plugin entry point".format(plugin_name))
        plugin = plugin_factory() if callable(plugin_factory) else plugin_factory
        plugin_version = str(getattr(plugin, "version", "unknown"))
        options = dict(mapping.get("plugin_options") or {})
        options["_rule_config"] = dict(mapping)
        options["max_rows"] = max_timepoints
        long = plugin.extract(str(path), options)
        plugin_time_provenance = sorted(
            set(str(value) for value in long.get("__time_provenance", []).dropna())
            if "__time_provenance" in long
            else []
        )
        plugin_sampling_rates = sorted(
            set(
                float(value)
                for value in pd.to_numeric(
                    long.get("__sampling_rate_hz", pd.Series(dtype=float)),
                    errors="coerce",
                ).dropna()
                if math.isfinite(float(value)) and float(value) > 0
            )
        )
        plugin_time_keys = sorted(
            set(str(value) for value in long.get("__time_source_key", []).dropna())
            if "__time_source_key" in long
            else []
        )
        time_axis_semantics = options.get("time_axis_semantics")
        sampling_rate_reference = options.get("sampling_rate_reference")
        try:
            sampling_rate_hz = float(options.get("sampling_rate_hz"))
        except (TypeError, ValueError):
            sampling_rate_hz = None
        if plugin_time_provenance == ["source-file-sampling-rate"]:
            sampling_rate_hz = (
                plugin_sampling_rates[0] if len(plugin_sampling_rates) == 1 else None
            )
            sampling_rate_reference = (
                "source-file-key:" + plugin_time_keys[0]
                if len(plugin_time_keys) == 1
                else "source-file-keys:" + ",".join(plugin_time_keys)
            )
            time_axis_semantics = "seconds-from-source-file-sampling-rate"
            time_provenance = "source-file-derived"
            time_unit = "s"
            time_transform = "sample-index-divided-by-source-file-sampling-rate"
        elif plugin_time_provenance == ["source-file-time-vector"]:
            sampling_rate_reference = (
                "source-file-key:" + plugin_time_keys[0]
                if len(plugin_time_keys) == 1
                else "source-file-keys:" + ",".join(plugin_time_keys)
            )
            time_axis_semantics = "source-file-time-vector"
            time_provenance = "source-file-derived"
            time_unit = (mapping.get("reader_options") or {}).get(
                "time_unit", "source-declared"
            )
        elif (
            time_axis_semantics == "sample-index-from-zero"
            and sampling_rate_hz is not None
            and math.isfinite(sampling_rate_hz)
            and sampling_rate_hz > 0
            and str(sampling_rate_reference).startswith(
                ("https://doi.org/", "http://doi.org/")
            )
        ):
            sample_indices = pd.to_numeric(long["time"], errors="coerce")
            unique_indices = (
                sample_indices.dropna().drop_duplicates().sort_values().reset_index(drop=True)
            )
            expected_indices = pd.Series(
                range(len(unique_indices)), dtype="float64"
            )
            if not unique_indices.astype("float64").equals(expected_indices):
                raise ValueError(
                    "MAT plugin time is not a zero-based contiguous sample index"
                )
            long = long.copy()
            long["time"] = sample_indices / sampling_rate_hz
            time_provenance = "source-publication-derived"
            time_unit = "s"
            time_transform = "sample-index-divided-by-sampling-rate"
        else:
            time_provenance = "unverified-plugin-time"
    elif file_format in ("trc", "mot", "sto", "opensim"):
        opensim_format = suffix_format if file_format == "opensim" else file_format
        if opensim_format == "trc":
            from harmo.ingest.readers.trc import read_trc_file

            _header, wide = read_trc_file(str(path))
        elif opensim_format in ("mot", "sto"):
            from harmo.ingest.readers.sto import read_sto_file

            _header, wide = read_sto_file(str(path))
        else:
            raise ValueError(
                "OpenSim mapping cannot infer TRC/MOT/STO reader from {}".format(path)
            )
        long, source_rows, time_provenance = _wide_to_long_sample(
            wide, max_timepoints
        )
        time_axis_semantics = "format-defined-time-column"
        time_unit = "s"
        long = _apply_channel_filter(long, mapping)
    elif file_format == "c3d":
        from harmo.ingest.readers.c3d import read_c3d_file
        try:
            import ezc3d

            plugin_version = str(getattr(ezc3d, "__version__", "unknown"))
        except ImportError:
            plugin_version = None

        wide = read_c3d_file(
            str(path),
            data_source=str(mapping.get("c3d_data_source", "point")),
            channel_keywords=list(mapping.get("analog_channel_keywords") or []) or None,
        )
        long, source_rows, time_provenance = _wide_to_long_sample(
            wide, max_timepoints
        )
        time_axis_semantics = "format-defined-time-column"
        time_unit = "s"
    elif file_format in ("csv", "txt", "tsv"):
        reader_options = dict(mapping.get("reader_options") or {})
        reader_options.setdefault("engine", "python")
        reader_options.setdefault("dtype", str)
        if file_format == "tsv" and "sep" not in reader_options:
            reader_options["sep"] = "\t"
        wide = pd.read_csv(path, **reader_options)
        if mapping.get("multi_rate_info"):
            long, source_rows, sampling_rates_hz = _parse_multirate_csv_sample(
                wide, mapping, max_timepoints
            )
            sampling_rate_hz = (
                sampling_rates_hz[0] if len(sampling_rates_hz) == 1 else None
            )
            time_provenance = "source-file-derived"
            time_axis_semantics = "per-channel-seconds-from-source-header-rate"
            time_unit = "s"
            sampling_rate_reference = "source-file-column-header"
        else:
            candidates = (
                (mapping.get("melt_options") or {}).get("time_column_candidates")
                or ["time", "Time"]
            )
            normalized_cycle = False
            candidate_names = {str(value).lower() for value in candidates}
            has_time_column = any(
                str(column).lower() in candidate_names for column in wide.columns
            )
            if not has_time_column and mapping.get("add_percentage_column"):
                if len(wide) < 2:
                    raise ValueError(
                        "normalized gait-cycle data require at least two samples"
                    )
                wide = wide.copy()
                wide.insert(
                    0,
                    "time",
                    pd.Series(range(len(wide)), dtype="float64")
                    / float(len(wide) - 1)
                    * 100.0,
                )
                candidates = ["time"]
                normalized_cycle = True
            sampling_rate_hz = _configured_sampling_rate(path, mapping)
            long, source_rows, time_provenance = _wide_to_long_sample(
                wide,
                max_timepoints,
                time_column_candidates=candidates,
                sampling_rate_hz=sampling_rate_hz,
            )
            if normalized_cycle:
                time_provenance = "publication-derived-normalized-cycle"
                time_axis_semantics = "normalized-gait-cycle-percent"
                time_unit = "%gait-cycle"
                sampling_rate_reference = mapping.get("normalized_axis_reference")
            else:
                time_axis_semantics = (
                    "seconds-from-mapping-sampling-rate"
                    if time_provenance == "mapping-derived-from-sampling-rate"
                    else "source-time-column"
                )
                time_unit = (
                    "s" if sampling_rate_hz is not None else mapping.get("time_unit")
                )
    else:
        raise ValueError("pilot reader does not support format {}".format(file_format))

    missing = {"time", "channel", "value"}.difference(long.columns)
    if missing:
        raise ValueError("normalized reader output lacks {}".format(sorted(missing)))
    normalized = long[["time", "channel", "value"]].copy()
    normalized["time"] = _deterministic_numeric(normalized["time"])
    normalized["value"] = _deterministic_numeric(normalized["value"])
    finite_time = normalized["time"].map(math.isfinite)
    finite_value = normalized["value"].map(math.isfinite)
    finite = finite_time & finite_value
    seconds_ready = bool(
        time_provenance
        in (
            "source-declared",
            "source-file-derived",
            "source-publication-derived",
        )
        and time_unit == "s"
    )
    normalized_cycle_ready = bool(
        time_provenance == "publication-derived-normalized-cycle"
        and time_unit == "%gait-cycle"
        and str(sampling_rate_reference).startswith(
            ("https://doi.org/", "http://doi.org/")
        )
    )
    summary = {
        "parser": plugin_name or "harmo.ingest.readers.{}".format(file_format),
        "parser_version": plugin_version,
        "source_rows": source_rows,
        "sample_rows": len(normalized),
        "sample_timepoints": int(normalized["time"].nunique(dropna=True)),
        "sample_channels": int(normalized["channel"].nunique(dropna=True)),
        "nonfinite_rows": int((~finite).sum()),
        "nonfinite_time_rows": int((~finite_time).sum()),
        "nonfinite_value_rows": int((~finite_value).sum()),
        "missing_value_policy": "retain-source-nonfinite-values",
        "time_min": _finite_or_none(normalized["time"].min()),
        "time_max": _finite_or_none(normalized["time"].max()),
        "time_provenance": time_provenance,
        "time_axis_semantics": time_axis_semantics,
        "time_unit": time_unit,
        "time_transform": time_transform,
        "sampling_rate_hz": sampling_rate_hz,
        "sampling_rates_hz": sampling_rates_hz,
        "sampling_rate_reference": sampling_rate_reference,
        "trust_state": (
            "traceable-format-ready"
            if (seconds_ready or normalized_cycle_ready)
            and bool(finite_time.all())
            and bool(finite_value.any())
            else "conditionally-comparable"
        ),
    }
    return normalized, summary


def _finite_or_none(value: Any) -> Optional[float]:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    return number if math.isfinite(number) else None


def normalized_records_digest(frame: pd.DataFrame) -> str:
    """Hash normalized values with stable binary float encoding."""
    digest = hashlib.sha256()
    fields = [
        "mapping_id",
        "source_member",
        "subject_id",
        "session_id",
        "channel",
        "file_format",
        "unit",
        "sensor_type",
        "data_type",
        "physical_quantity",
        "side",
        "axis",
        "scale_factor",
        "value_transform",
    ]
    ordered = frame.sort_values(
        fields + ["time", "source_value", "value"], kind="mergesort"
    )
    for row in ordered.itertuples(index=False):
        values = row._asdict()
        for field in fields:
            digest.update(str(values.get(field, "")).encode("utf-8"))
            digest.update(b"\0")
        digest.update(struct.pack("!d", float(values["time"])))
        digest.update(struct.pack("!d", float(values["source_value"])))
        digest.update(struct.pack("!d", float(values["value"])))
    return digest.hexdigest()


def _parse_once(
    mapping_rows: Sequence[Mapping[str, Any]],
    config: Mapping[str, Any],
    extracted: Mapping[str, Path],
    channel_definitions: Mapping[str, Any],
    max_timepoints: int,
    marker_names: Optional[Sequence[str]] = None,
) -> Tuple[pd.DataFrame, List[Dict[str, Any]]]:
    mappings_by_id: Dict[str, Mapping[str, Any]] = {}
    study_id = str(mapping_rows[0]["study_id"]) if mapping_rows else "study"
    for index, mapping in enumerate(config.get("file_mappings") or []):
        if mapping.get("enabled", True):
            mappings_by_id["{}-{:03d}".format(study_id, index + 1)] = mapping

    frames: List[pd.DataFrame] = []
    ledger: List[Dict[str, Any]] = []
    for mapping_row in mapping_rows:
        item = dict(mapping_row)
        mapping = mappings_by_id[str(item["mapping_id"])]
        semantic_issues = mapping_semantic_issues(mapping)
        item["static_metadata"] = dict(mapping.get("static_metadata") or {})
        item["semantic_issues"] = semantic_issues
        selected = item.get("selected_member")
        if not selected:
            if item.get("outcome") in {"failed", "source-absent-declared"}:
                item["parse_outcome"] = item["outcome"]
            else:
                item["parse_outcome"] = "missing"
            ledger.append(item)
            continue
        path = extracted[str(selected)]
        item["source_size_bytes"] = path.stat().st_size
        item["source_sha256"] = file_sha256(path)
        try:
            normalized, summary = parse_mapping_sample(
                path, mapping, max_timepoints
            )
            for column, value in (
                ("study_id", item["study_id"]),
                ("mapping_id", item["mapping_id"]),
                ("mapping_name", item["mapping_name"]),
                ("source_member", selected),
                ("source_sha256", item["source_sha256"]),
                ("subject_id", item.get("subject_id")),
                ("session_id", item.get("session_id")),
                ("file_format", item.get("file_format")),
            ):
                normalized[column] = value
            metadata = mapping.get("static_metadata") or {}
            metadata_fields = (
                "unit",
                "sensor_type",
                "data_type",
                "physical_quantity",
                "side",
                "axis",
                "scale_factor",
            )
            data_type = str(metadata.get("data_type", "")).lower()
            if (
                str(item.get("file_format", "")).lower() == "mat"
                and data_type == "marker"
            ):
                known_markers = set(marker_names or [])
                observed_channels = sorted(
                    str(value).strip()
                    for value in normalized["channel"].dropna().unique()
                )
                unknown_channels = []
                for channel in observed_channels:
                    candidate = (
                        channel[1:]
                        if channel[:1].upper() in {"X", "Y", "Z"}
                        else re.sub(r"_[xyzXYZ]$", "", channel)
                    )
                    if candidate not in known_markers:
                        unknown_channels.append(channel)
                item["defined_channels"] = len(observed_channels) - len(unknown_channels)
                item["channel_definition_coverage"] = (
                    item["defined_channels"] / len(observed_channels)
                    if observed_channels else 0.0
                )
                item["unknown_channels"] = unknown_channels
                if unknown_channels:
                    semantic_issues.append(
                        "{} parsed MAT marker channels lack definitions".format(
                            len(unknown_channels)
                        )
                    )
                for field in metadata_fields:
                    normalized[field] = metadata.get(field)
            elif str(item.get("file_format", "")).lower() == "mat":
                observed_channels = sorted(
                    str(value) for value in normalized["channel"].dropna().unique()
                )
                resolved_definitions = {
                    value: _resolve_channel_definition(
                        channel_definitions.get(value), metadata
                    )
                    for value in observed_channels
                }
                unknown_channels = [
                    value
                    for value, definition in resolved_definitions.items()
                    if definition is None
                ]
                item["defined_channels"] = len(observed_channels) - len(unknown_channels)
                item["channel_definition_coverage"] = (
                    item["defined_channels"] / len(observed_channels)
                    if observed_channels else 0.0
                )
                item["unknown_channels"] = unknown_channels
                if unknown_channels:
                    semantic_issues.append(
                        "{} parsed MAT channels lack definitions".format(
                            len(unknown_channels)
                        )
                    )
                for field in metadata_fields:
                    normalized[field] = normalized["channel"].map(
                        lambda channel: (
                            resolved_definitions.get(str(channel)) or {}
                        ).get(field)
                    )
            else:
                for field in metadata_fields:
                    normalized[field] = metadata.get(field)
            for field in metadata_fields:
                if field == "scale_factor":
                    normalized[field] = pd.to_numeric(
                        normalized[field], errors="coerce"
                    ).astype("float64")
                else:
                    normalized[field] = normalized[field].astype("string")
            normalized["source_value"] = normalized["value"].astype("float64")
            normalized["value"] = normalized["value"].astype("float64")
            scaled = normalized["scale_factor"].notna()
            normalized["value_transform"] = pd.Series(
                "identity", index=normalized.index, dtype="string"
            )
            normalized.loc[scaled, "value"] = (
                normalized.loc[scaled, "source_value"]
                * normalized.loc[scaled, "scale_factor"]
            )
            normalized.loc[scaled, "value_transform"] = "multiply-by-scale-factor"
            item["scaled_sample_rows"] = int(scaled.sum())
            frames.append(normalized)
            item.update(summary)
            item["semantic_issues"] = semantic_issues
            if semantic_issues:
                item["trust_state"] = "conditionally-comparable"
            item["parse_outcome"] = "converted"
            item["error"] = None
        except Exception as exc:
            item["parse_outcome"] = "failed"
            item["semantic_issues"] = semantic_issues
            item["error"] = "{}: {}".format(type(exc).__name__, exc)
        ledger.append(item)
    if frames:
        records = pd.concat(frames, ignore_index=True)
    else:
        records = pd.DataFrame(
            columns=[
                "time", "channel", "source_value", "value", "study_id", "mapping_id",
                "mapping_name", "source_member", "source_sha256", "subject_id",
                "session_id", "file_format", "unit", "sensor_type", "data_type",
                "physical_quantity", "side", "axis", "scale_factor",
                "value_transform",
            ]
        )
    return records, ledger


def duckdb_parquet_audit(frame: pd.DataFrame, path: Path) -> Dict[str, Any]:
    """Materialize the AI-ready sample and verify it through DuckDB."""
    import duckdb

    path.parent.mkdir(parents=True, exist_ok=True)
    frame.to_parquet(path, index=False)
    connection = duckdb.connect(database=":memory:")
    try:
        row = connection.execute(
            """
            SELECT
                count(*) AS rows,
                count(DISTINCT mapping_id) AS mappings,
                count(DISTINCT source_member) AS source_files,
                sum(CASE WHEN NOT isfinite(time) OR NOT isfinite(value) THEN 1 ELSE 0 END)
                    AS nonfinite_rows,
                sum(CASE WHEN NOT isfinite(source_value) THEN 1 ELSE 0 END)
                    AS nonfinite_source_rows,
                min(time) AS time_min,
                max(time) AS time_max,
                sum(
                    CASE WHEN file_format = 'mat'
                        AND (unit IS NULL OR sensor_type IS NULL OR data_type IS NULL)
                    THEN 1 ELSE 0 END
                ) AS mat_rows_missing_required_metadata,
                sum(CASE WHEN value_transform = 'multiply-by-scale-factor' THEN 1 ELSE 0 END)
                    AS scaled_rows
            FROM read_parquet(?)
            """,
            [str(path)],
        ).fetchone()
    finally:
        connection.close()
    return {
        "path": str(path),
        "sha256": file_sha256(path),
        "size_bytes": path.stat().st_size,
        "rows": int(row[0]),
        "mappings": int(row[1]),
        "source_files": int(row[2]),
        "nonfinite_rows": int(row[3] or 0),
        "nonfinite_source_rows": int(row[4] or 0),
        "time_min": _finite_or_none(row[5]),
        "time_max": _finite_or_none(row[6]),
        "mat_rows_missing_required_metadata": int(row[7] or 0),
        "scaled_rows": int(row[8] or 0),
    }


def run_real_data_pilot(
    root: Path,
    source: Mapping[str, Any],
    asset: Mapping[str, Any],
    archive_path: Path,
    extract_root: Path,
    parquet_path: Path,
    study_id: str,
    config_path: Path,
    repeats: int = 2,
    max_timepoints: int = 100,
) -> Dict[str, Any]:
    """Run a bounded, repeated real-data conversion pilot."""
    if repeats < 2:
        raise ValueError("real-data pilot requires at least two repeats")
    if max_timepoints <= 0:
        raise ValueError("max_timepoints must be positive")
    archive = verify_source_archive(archive_path, asset)
    if not archive["pass"]:
        raise ValueError("source archive failed checksum or ZIP-integrity verification")
    config = yaml.safe_load(config_path.read_text(encoding="utf-8")) or {}
    channel_definitions, channel_definition_record = load_channel_definitions(
        config_path, config
    )
    mapping_rows, members = audit_archive_mappings(archive_path, config, study_id)
    if not mapping_rows:
        raise ValueError("study config has no enabled file mappings")
    mapping_rows = reconcile_declared_absences(mapping_rows, asset)
    selected = [str(row["selected_member"]) for row in mapping_rows if row["selected_member"]]
    extracted = extract_selected_members(archive_path, selected, extract_root)

    repeat_digests: List[str] = []
    first_records: Optional[pd.DataFrame] = None
    first_ledger: Optional[List[Dict[str, Any]]] = None
    for _index in range(repeats):
        records, ledger = _parse_once(
            mapping_rows,
            config,
            extracted,
            channel_definitions,
            max_timepoints=max_timepoints,
        )
        repeat_digests.append(normalized_records_digest(records))
        if first_records is None:
            first_records = records
            first_ledger = ledger
    assert first_records is not None and first_ledger is not None
    query = duckdb_parquet_audit(first_records, parquet_path)
    matched = sum(row["outcome"] == "discovered" for row in mapping_rows)
    converted = sum(row["parse_outcome"] == "converted" for row in first_ledger)
    declared_missing = sum(
        row["parse_outcome"] == "source-absent-declared" for row in first_ledger
    )
    unaccounted_missing = sum(
        row["parse_outcome"] == "missing" for row in first_ledger
    )
    failed = sum(row["parse_outcome"] == "failed" for row in first_ledger)
    conditional = sum(
        row.get("trust_state") == "conditionally-comparable" for row in first_ledger
    )
    nonfinite = sum(int(row.get("nonfinite_rows", 0)) for row in first_ledger)
    semantic_issue_count = sum(
        len(row.get("semantic_issues") or []) for row in first_ledger
    )
    reproducible = len(set(repeat_digests)) == 1 and len(repeat_digests) == repeats
    passed = (
        bool(archive["pass"])
        and converted + declared_missing == len(mapping_rows)
        and failed == 0
        and unaccounted_missing == 0
        and nonfinite == 0
        and query["nonfinite_source_rows"] == 0
        and query["rows"] == len(first_records)
        and query["mappings"] == converted
        and query["mat_rows_missing_required_metadata"] == 0
        and semantic_issue_count == 0
        and reproducible
    )
    return {
        "schema_version": "1.0.0",
        "scope": "single-subject-real-data-pilot-not-confirmatory-A-G",
        "study_id": study_id,
        "source": {
            "doi": source["doi"],
            "landing_page": source["landing_page"],
            "api_record": source["api_record"],
            "license": source["license"],
            "attribution": source.get("attribution"),
            "file_id": asset["file_id"],
            "asset_name": asset["name"],
            "download_url": asset["download_url"],
        },
        "archive": archive,
        "config": {
            "path": str(config_path.relative_to(root)),
            "sha256": file_sha256(config_path),
            "enabled_mappings": len(mapping_rows),
            "channel_definitions": channel_definition_record,
        },
        "archive_inventory": {
            "usable_files": len(members),
            "matched_mappings": matched,
            "declared_absent_mappings": declared_missing,
            "accounted_mappings": matched + declared_missing,
            "unique_selected_files": len(set(selected)),
        },
        "conversion": {
            "converted_mappings": converted,
            "declared_absent_mappings": declared_missing,
            "unaccounted_missing_mappings": unaccounted_missing,
            "failed_mappings": failed,
            "normalized_sample_rows": len(first_records),
            "nonfinite_rows": nonfinite,
            "conditionally_comparable_mappings": conditional,
            "semantic_issue_count": semantic_issue_count,
            "max_timepoints_per_mapping": max_timepoints,
            "mapping_ledger": first_ledger,
        },
        "reproducibility": {
            "repeats": repeats,
            "normalized_record_sha256": repeat_digests,
            "pass": reproducible,
        },
        "duckdb_parquet_audit": query,
        "environment": environment_record(),
        "trust_ready": passed and conditional == 0,
        "complete_mapping_coverage": declared_missing == 0,
        "limitations": [
            "This pilot covers one healthy participant and cannot establish population or clinical generalizability.",
            "Normalized values are a bounded parser sample; the full source files are read but not redistributed.",
            "MAT sample indices are converted to seconds using the 2000 Hz rate reported in the peer-reviewed Data Descriptor; selected arrays do not carry original timestamps.",
            "The peer-reviewed 0.000286 raw-EMG-to-mV factor is applied while source_value is retained.",
            "A successful parser result establishes computational readability, not physiological validity.",
        ],
        "pass": passed,
    }


def summarize_real_data_cohort(
    study_id: str,
    source: Mapping[str, Any],
    expected_assets: Mapping[str, Mapping[str, Any]],
    participant_results: Mapping[str, Mapping[str, Any]],
    failures: Optional[Mapping[str, str]] = None,
    result_files: Optional[Mapping[str, Mapping[str, str]]] = None,
    run_environment: Optional[Mapping[str, str]] = None,
) -> Dict[str, Any]:
    """Create a fail-closed cohort ledger from participant pilot results.

    Every registered participant remains in the denominator.  A cohort passes
    only when all participants pass their computational pilot, use one config
    and channel definition, reproduce within participant, and produce distinct
    normalized digests across participants.
    """
    failures = failures or {}
    result_files = result_files or {}
    expected_ids = sorted(expected_assets)
    unexpected = sorted(
        (set(participant_results) | set(failures) | set(result_files))
        - set(expected_ids)
    )
    if unexpected:
        raise ValueError(
            "cohort records contain unregistered assets: {}".format(
                ", ".join(unexpected)
            )
        )

    participant_ledger: List[Dict[str, Any]] = []
    config_digests = set()
    channel_digests = set()
    participant_digests: Dict[str, str] = {}
    total_mappings = 0
    matched_mappings = 0
    converted_mappings = 0
    declared_absent_mappings = 0
    unaccounted_missing_mappings = 0
    failed_mappings = 0
    normalized_rows = 0
    conditional_mappings = 0
    semantic_issues = 0
    scaled_rows = 0

    for asset_id in expected_ids:
        asset = expected_assets[asset_id]
        result = participant_results.get(asset_id)
        error = failures.get(asset_id)
        row: Dict[str, Any] = {
            "asset_id": asset_id,
            "asset_name": asset.get("name"),
            "file_id": asset.get("file_id"),
            "expected_md5": asset.get("md5"),
            "expected_size_bytes": asset.get("size_bytes"),
        }
        if result is None:
            row.update(
                {
                    "outcome": "failed" if error else "missing",
                    "error": error or "participant result was not produced",
                    "pass": False,
                    "trust_ready": False,
                }
            )
            participant_ledger.append(row)
            continue

        config = result.get("config") or {}
        channel_record = config.get("channel_definitions") or {}
        conversion = result.get("conversion") or {}
        inventory = result.get("archive_inventory") or {}
        reproducibility = result.get("reproducibility") or {}
        parquet = result.get("duckdb_parquet_audit") or {}
        digests = reproducibility.get("normalized_record_sha256") or []
        first_digest = str(digests[0]) if digests else None
        if config.get("sha256"):
            config_digests.add(str(config["sha256"]))
        if channel_record.get("sha256"):
            channel_digests.add(str(channel_record["sha256"]))
        if first_digest:
            participant_digests[asset_id] = first_digest

        enabled = int(config.get("enabled_mappings") or 0)
        total_mappings += enabled
        matched_mappings += int(inventory.get("matched_mappings") or 0)
        converted_mappings += int(conversion.get("converted_mappings") or 0)
        declared_absent_mappings += int(
            conversion.get("declared_absent_mappings") or 0
        )
        unaccounted_missing_mappings += int(
            conversion.get("unaccounted_missing_mappings") or 0
        )
        failed_mappings += int(conversion.get("failed_mappings") or 0)
        normalized_rows += int(conversion.get("normalized_sample_rows") or 0)
        conditional_mappings += int(
            conversion.get("conditionally_comparable_mappings") or 0
        )
        semantic_issues += int(conversion.get("semantic_issue_count") or 0)
        scaled_rows += int(parquet.get("scaled_rows") or 0)

        row.update(
            {
                "outcome": "completed",
                "error": error,
                "archive_sha256": (result.get("archive") or {}).get("sha256"),
                "archive_integrity_pass": (result.get("archive") or {}).get("pass"),
                "usable_files": inventory.get("usable_files"),
                "enabled_mappings": enabled,
                "matched_mappings": inventory.get("matched_mappings"),
                "converted_mappings": conversion.get("converted_mappings"),
                "declared_absent_mappings": conversion.get(
                    "declared_absent_mappings"
                ),
                "unaccounted_missing_mappings": conversion.get(
                    "unaccounted_missing_mappings"
                ),
                "failed_mappings": conversion.get("failed_mappings"),
                "normalized_sample_rows": conversion.get("normalized_sample_rows"),
                "conditionally_comparable_mappings": conversion.get(
                    "conditionally_comparable_mappings"
                ),
                "semantic_issue_count": conversion.get("semantic_issue_count"),
                "scaled_rows": parquet.get("scaled_rows"),
                "config_sha256": config.get("sha256"),
                "channel_definitions_sha256": channel_record.get("sha256"),
                "normalized_record_sha256": first_digest,
                "within_participant_reproducible": reproducibility.get("pass"),
                "pass": bool(result.get("pass")),
                "trust_ready": bool(result.get("trust_ready")),
                "complete_mapping_coverage": bool(
                    result.get("complete_mapping_coverage")
                ),
            }
        )
        row.update(result_files.get(asset_id) or {})
        participant_ledger.append(row)

    digest_to_assets: Dict[str, List[str]] = {}
    for asset_id, digest in participant_digests.items():
        digest_to_assets.setdefault(digest, []).append(asset_id)
    duplicate_digests = [
        {"normalized_record_sha256": digest, "asset_ids": sorted(asset_ids)}
        for digest, asset_ids in sorted(digest_to_assets.items())
        if len(asset_ids) > 1
    ]

    completed = sum(row["outcome"] == "completed" for row in participant_ledger)
    passed_participants = sum(bool(row["pass"]) for row in participant_ledger)
    trust_ready_participants = sum(
        bool(row["trust_ready"]) for row in participant_ledger
    )
    complete_mapping_participants = sum(
        bool(row.get("complete_mapping_coverage")) for row in participant_ledger
    )
    reproducible_participants = sum(
        row.get("within_participant_reproducible") is True
        for row in participant_ledger
    )
    config_consistent = (
        completed == len(expected_ids)
        and len(config_digests) == 1
        and len(channel_digests) == 1
    )
    all_digests_present = len(participant_digests) == len(expected_ids)
    passed = (
        completed == len(expected_ids)
        and passed_participants == len(expected_ids)
        and reproducible_participants == len(expected_ids)
        and config_consistent
        and all_digests_present
        and not duplicate_digests
    )
    return {
        "schema_version": "1.0.0",
        "scope": "complete-study-real-data-cohort-computational-validation",
        "study_id": study_id,
        "source": {
            "doi": source.get("doi"),
            "landing_page": source.get("landing_page"),
            "api_record": source.get("api_record"),
            "release_version": source.get("release_version"),
            "license": source.get("license"),
            "attribution": source.get("attribution"),
        },
        "denominator": {
            "expected_participants": len(expected_ids),
            "completed_participants": completed,
            "failed_or_missing_participants": len(expected_ids) - completed,
            "passed_participants": passed_participants,
            "trust_ready_participants": trust_ready_participants,
            "complete_mapping_participants": complete_mapping_participants,
            "participant_ids": expected_ids,
        },
        "conversion": {
            "enabled_mappings": total_mappings,
            "matched_mappings": matched_mappings,
            "converted_mappings": converted_mappings,
            "declared_absent_mappings": declared_absent_mappings,
            "unaccounted_missing_mappings": unaccounted_missing_mappings,
            "accounted_mappings": converted_mappings + declared_absent_mappings,
            "failed_mappings": failed_mappings,
            "normalized_sample_rows": normalized_rows,
            "conditionally_comparable_mappings": conditional_mappings,
            "semantic_issue_count": semantic_issues,
            "scaled_rows": scaled_rows,
        },
        "cross_participant_checks": {
            "config_consistent": config_consistent,
            "config_sha256": sorted(config_digests),
            "channel_definitions_sha256": sorted(channel_digests),
            "within_participant_reproducible": reproducible_participants,
            "normalized_digests_present": len(participant_digests),
            "unique_normalized_digests": len(set(participant_digests.values())),
            "duplicate_normalized_digests": duplicate_digests,
        },
        "participant_ledger": participant_ledger,
        "environment": dict(run_environment or environment_record()),
        "trust_ready": passed and trust_ready_participants == len(expected_ids),
        "complete_mapping_coverage": (
            complete_mapping_participants == len(expected_ids)
        ),
        "limitations": [
            "This cohort contains healthy adults and cannot establish clinical generalizability.",
            "Each participant uses a bounded deterministic parser sample rather than redistributed full normalized signals.",
            "MAT sample indices are converted using the 2000 Hz rate reported in the peer-reviewed Data Descriptor; selected arrays do not carry original timestamps.",
            "The peer-reviewed 0.000286 raw-EMG-to-mV factor is applied while source_value is retained.",
            "Computational cohort success does not establish physiological validity.",
        ],
        "pass": passed,
    }
