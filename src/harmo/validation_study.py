"""Reproducible empirical-validation utilities for the harmo-semg paper.

The module deliberately keeps experimental bookkeeping separate from the public
evidence contracts.  It can inventory protected source trees without copying
data, creates complete denominators (including failures), and emits stable JSON
that can be compared between machines.
"""

from __future__ import annotations

import csv
import hashlib
import json
import math
import os
import platform
import random
import re
import subprocess
import time
from collections import Counter, defaultdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Mapping, Optional, Sequence, Tuple

import yaml

from harmo.evidence.models import (
    ArrayReference,
    ChannelDescriptor,
    HarmoRecordingBundle,
    SourceAsset,
    Timebase,
    TrustStatus,
)
from harmo.evidence.validation import (
    RecordingProfile,
    assess_bundle,
    compare_numeric_series,
)
from harmo.mechanics_disposition import validate_mechanics_disposition
from harmo.source_release import source_rights_report

VALIDATION_VERSION = "1.0.0"
OUTCOMES = {
    "discovered",
    "converted",
    "failed",
    "excluded",
    "not-applicable",
    "missing",
}
VOLATILE_MANIFEST_FIELDS = {
    "run_id",
    "created_at",
    "started_at",
    "completed_at",
    "environment",
    "environment_id",
}


def canonical_json(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def canonical_sha256(value: Any) -> str:
    return hashlib.sha256(canonical_json(value).encode("utf-8")).hexdigest()


def file_sha256(path: Path, chunk_size: int = 1024 * 1024) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        while True:
            chunk = stream.read(chunk_size)
            if not chunk:
                break
            digest.update(chunk)
    return digest.hexdigest()


def write_json(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(
        json.dumps(value, ensure_ascii=False, sort_keys=True, indent=2) + "\n",
        encoding="utf-8",
    )
    os.replace(str(temporary), str(path))


def write_rows(path: Path, rows: Sequence[Mapping[str, Any]]) -> Dict[str, str]:
    """Write JSON and CSV; add Parquet when an installed engine supports it."""
    normalized = [dict(row) for row in rows]
    write_json(path, normalized)
    outputs = {"json": str(path)}
    csv_path = path.with_suffix(".csv")
    fields = sorted({key for row in normalized for key in row})
    with csv_path.open("w", encoding="utf-8", newline="") as stream:
        writer = csv.DictWriter(stream, fieldnames=fields, lineterminator="\n")
        writer.writeheader()
        for row in normalized:
            writer.writerow({key: _cell(row.get(key)) for key in fields})
    outputs["csv"] = str(csv_path)
    try:
        import pandas as pd

        parquet_path = path.with_suffix(".parquet")
        pd.DataFrame(normalized).to_parquet(parquet_path, index=False)
        outputs["parquet"] = str(parquet_path)
    except (ImportError, ValueError):
        pass
    return outputs


def _cell(value: Any) -> Any:
    if isinstance(value, (dict, list, tuple)):
        return canonical_json(value)
    return value


def load_registry(path: Path) -> Dict[str, Any]:
    data = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    errors = validate_registry(data)
    if errors:
        raise ValueError("invalid validation registry:\n- " + "\n- ".join(errors))
    return data


def validate_registry(data: Mapping[str, Any]) -> List[str]:
    errors = []
    if data.get("registry_version") != VALIDATION_VERSION:
        errors.append("registry_version must be {}".format(VALIDATION_VERSION))
    studies = data.get("studies") or []
    ids = [str(item.get("id", "")) for item in studies]
    if len(studies) != 8:
        errors.append("exactly 8 active studies must be frozen")
    if any(not value for value in ids) or len(ids) != len(set(ids)):
        errors.append("study IDs must be non-empty and unique")
    for study in studies:
        for key in (
            "id",
            "config",
            "doi",
            "license",
            "redistribution",
            "emg_available",
            "selection_source",
            "catalog_label",
            "catalog_url",
        ):
            if key not in study:
                errors.append("study {} lacks {}".format(study.get("id", "?"), key))
    workbook_studies = [
        item for item in studies if item.get("selection_source") == "workbook"
    ]
    additions = [
        item
        for item in studies
        if item.get("selection_source") == "post-workbook-addition"
    ]
    if sorted(item.get("selection_row") for item in workbook_studies) != [
        28,
        31,
        32,
        33,
        38,
        43,
    ]:
        errors.append(
            "workbook-derived studies must freeze datasheet rows 28, 31, 32, 33, 38, and 43"
        )
    if sorted(item.get("id") for item in additions) != ["study_1", "study_120"]:
        errors.append("post-workbook additions must be study_1 and study_120")
    catalog = data.get("catalog_source") or {}
    if not re.match(r"^[0-9a-f]{64}$", str(catalog.get("sha256", ""))):
        errors.append("catalog_source requires the workbook SHA-256 digest")
    experiments = data.get("experiments") or {}
    if set(experiments) != set("ABCDEFG"):
        errors.append("experiments A-G must all be registered")
    fidelity = experiments.get("B", {})
    if fidelity.get("subjects") != list(range(1, 17)):
        errors.append("experiment B must freeze subjects 1-16")
    if fidelity.get("pilot_subject") != 1:
        errors.append("experiment B must record subject 1 as the format pilot")
    if fidelity.get("confirmatory_subjects") != list(range(2, 17)):
        errors.append("experiment B must freeze subjects 2-16 as confirmatory")
    if fidelity.get("speed") != "V25" or fidelity.get("trial") != "T1":
        errors.append("experiment B must freeze V25/T1")
    required_fidelity_pairs = {
        ("emg", "ascii", "mat"),
        ("trajectories", "ascii", "mat"),
    }
    observed_fidelity_pairs = {
        tuple(value) for value in fidelity.get("required_equivalence_pairs") or []
    }
    if observed_fidelity_pairs != required_fidelity_pairs:
        errors.append("experiment B required format-equivalence pairs differ")
    required_c3d_audits = {
        ("trajectories", "ascii", "c3d"),
        ("trajectories", "mat", "c3d"),
        ("emg", "ascii", "c3d"),
        ("emg", "mat", "c3d"),
    }
    observed_c3d_audits = {
        tuple(value) for value in fidelity.get("c3d_interchangeability_audits") or []
    }
    if observed_c3d_audits != required_c3d_audits:
        errors.append("experiment B C3D interchangeability audits differ")
    fidelity_signals = fidelity.get("signals") or {}
    if (fidelity_signals.get("emg") or {}).get("source_absent_subjects") != [6, 7]:
        errors.append(
            "experiment B must retain V25 EMG source absence for subjects 6 and 7"
        )
    reproducibility = experiments.get("D", {})
    if reproducibility.get("environments") != [
        "macOS-native",
        "github-actions-ubuntu",
    ]:
        errors.append("experiment D must freeze macOS and GitHub Actions Ubuntu")
    if reproducibility.get("reruns_per_environment") != 3:
        errors.append("experiment D must freeze three reruns per environment")
    if reproducibility.get("confirmatory_scope") != "frozen-78-mapping-corpus":
        errors.append("experiment D must cover the frozen 78-mapping corpus")
    opensim = experiments.get("E", {})
    if opensim.get("subjects") != list(range(1, 17)):
        errors.append("experiment E must freeze subjects 1-16")
    if opensim.get("speeds") != ["V1", "V15", "V2", "V25", "V3", "V35", "V4"]:
        errors.append("experiment E speeds differ from the frozen protocol")
    if opensim.get("trials") != ["T1", "T2", "T3"]:
        errors.append("experiment E must freeze T1-T3")
    if opensim.get("denominator") != 336:
        errors.append("experiment E denominator must be 336")
    if opensim.get("source_absence_policy") != "retain":
        errors.append("experiment E must retain source-absent trials")
    if opensim.get("static_calibration_required") is not True:
        errors.append("experiment E must require released static calibration")
    if opensim.get("required_opensim_version") != "4.5.2":
        errors.append("experiment E must freeze OpenSim 4.5.2")
    required_opensim_metrics = {
        "ik_rms_error_m": 0.02,
        "ik_max_error_m": 0.04,
        "residual_force_fraction": 0.05,
        "residual_moment_fraction": 0.01,
        "rra_angle_change_rms_deg": 5.0,
    }
    if opensim.get("metric_thresholds") != required_opensim_metrics:
        errors.append("experiment E OpenSim thresholds differ from the frozen protocol")
    g6b = (data.get("extensions") or {}).get("G6b", {})
    if not g6b:
        errors.append("G6b extension must be registered separately from experiment E")
    else:
        if g6b.get("static_role") != "hidden-reference-only":
            errors.append("G6b static data must remain hidden-reference-only")
        if g6b.get("measurement_pilot_subjects") != ["01"]:
            errors.append("G6b must freeze Subject 01 as the implementation pilot")
        if g6b.get("model_template_subject") != "01":
            errors.append("G6b-2 must use Subject 01 only as the model template")
        if g6b.get("model_target_subject") != "02":
            errors.append("G6b-2 must freeze Subject 02 as the target")
        if g6b.get("model_static_reference_role") != (
            "hidden-until-model-synthesis-completes"
        ):
            errors.append("G6b-2 target static model must remain hidden during synthesis")
        if g6b.get("model_endpoint_status") not in {
            "protocol-frozen-before-outcome",
            "accepted-implementation-pilot",
            "failed-frozen-thresholds",
        }:
            errors.append("G6b-2 model endpoint has an invalid registered status")
        if g6b.get("mechanics_target_subject") != "02":
            errors.append("G6b-3 must retain Subject 02 as the mechanics target")
        if g6b.get("mechanics_heldout_trial") != "4ms":
            errors.append("G6b-3 must retain the 4 m/s held-out trial")
        if g6b.get("downstream_mechanics_endpoint_status") not in {
            "protocol-frozen-before-outcome",
            "accepted-implementation-pilot",
            "failed-frozen-thresholds",
        }:
            errors.append("G6b-3 mechanics endpoint has an invalid status")
        if g6b.get("registration_excluded_inputs") != [
            "target-static-model",
            "heldout-4ms-trial",
        ]:
            errors.append("G6b-3R must exclude static and held-out inputs")
        if g6b.get("registration_endpoint_status") not in {
            "protocol-frozen-before-outcome",
            "accepted-feasibility-pilot",
            "failed-frozen-thresholds",
        }:
            errors.append("G6b-3R registration endpoint has an invalid status")
        if g6b.get("registered_mechanics_endpoint_status") not in {
            "protocol-frozen-before-outcome",
            "accepted-exploratory-feasibility",
            "failed-frozen-thresholds",
        }:
            errors.append("G6b-3R-H mechanics endpoint has an invalid status")
        if g6b.get("registered_mechanics_endpoint_status") != (
            "protocol-frozen-before-outcome"
        ) and not g6b.get("registered_mechanics_result"):
            errors.append("G6b-3R-H finalized status requires a result artifact")
    thresholds = data.get("thresholds") or {}
    required_thresholds = {
        "time_samples": 0.5,
        "event_samples": 1,
        "critical_fault_sensitivity": 1.0,
        "overall_fault_sensitivity": 0.95,
    }
    for key, expected in required_thresholds.items():
        if thresholds.get(key) != expected:
            errors.append("threshold {} must be {}".format(key, expected))
    return errors


def environment_record() -> Dict[str, str]:
    commit = _git(["rev-parse", "HEAD"])
    dirty = bool(_git(["status", "--porcelain"]))
    return {
        "platform": platform.platform(),
        "python": platform.python_version(),
        "implementation": platform.python_implementation(),
        "machine": platform.machine(),
        "code_commit": commit,
        "code_dirty": str(dirty).lower(),
    }


def _git(arguments: Sequence[str]) -> str:
    try:
        return subprocess.check_output(
            ["git"] + list(arguments), stderr=subprocess.DEVNULL, text=True
        ).strip()
    except (OSError, subprocess.CalledProcessError):
        return "unknown"


def inventory_source_tree(
    root: Path,
    registry: Mapping[str, Any],
    container_root: Optional[Path] = None,
    host_root: Optional[Path] = None,
    hash_files: bool = False,
) -> Tuple[List[Dict[str, Any]], List[Dict[str, Any]]]:
    source_rows = []
    mapping_rows = []
    studies = {str(item["id"]): item for item in registry["studies"]}
    for study_id, study in sorted(studies.items()):
        config_path = root / str(study["config"])
        config = yaml.safe_load(config_path.read_text(encoding="utf-8")) or {}
        aliases = config.get("input_dir_aliases") or {}
        file_records = {}
        for mapping_index, mapping in enumerate(config.get("file_mappings") or []):
            if not mapping.get("enabled", True):
                continue
            mapping_id = "{}-{:03d}".format(study_id, mapping_index + 1)
            alias = str(mapping.get("base_input_dir_alias", ""))
            base = _resolve_source_base(
                config_path, aliases.get(alias), container_root, host_root
            )
            started = time.monotonic()
            matches, error = _mapping_matches(base, mapping)
            mapping_rows.append(
                {
                    "study_id": study_id,
                    "mapping_id": mapping_id,
                    "mapping_name": mapping.get("name", mapping_id),
                    "base_path": str(base) if base else None,
                    "input_path_template": mapping.get("input_path_template"),
                    "matched_files": len(matches),
                    "selected_reproducibility_file": str(matches[0])
                    if matches
                    else None,
                    "outcome": "discovered" if matches else "missing",
                    "error": error,
                    "elapsed_seconds": round(time.monotonic() - started, 6),
                }
            )
            for path in matches:
                key = str(path)
                if key not in file_records:
                    stat = path.stat()
                    file_records[key] = {
                        "study_id": study_id,
                        "source_path": key,
                        "size_bytes": stat.st_size,
                        "sha256": file_sha256(path) if hash_files else None,
                        "license": study["license"],
                        "redistribution": study["redistribution"],
                        "doi": study["doi"],
                        "catalog_label": study["catalog_label"],
                        "catalog_url": study["catalog_url"],
                        "selection_source": study["selection_source"],
                        "selection_row": study.get("selection_row"),
                        "emg_available": study["emg_available"],
                        "outcome": "discovered",
                    }
        source_rows.extend(file_records.values())
    return source_rows, mapping_rows


def _resolve_source_base(
    config_path: Path,
    raw_value: Any,
    container_root: Optional[Path],
    host_root: Optional[Path],
) -> Optional[Path]:
    if raw_value is None:
        return None
    raw = Path(str(raw_value))
    if not raw.is_absolute():
        return (config_path.parent / raw).resolve()
    if container_root and host_root:
        try:
            relative = raw.relative_to(container_root)
            return (host_root / relative).resolve()
        except ValueError:
            pass
    return raw


def _mapping_matches(
    base: Optional[Path], mapping: Mapping[str, Any]
) -> Tuple[List[Path], Optional[str]]:
    if base is None:
        return [], "base input alias is undefined"
    if not base.is_dir():
        return [], "base input directory does not exist"
    template = str(mapping.get("input_path_template", ""))
    if not template:
        return [], "input_path_template is missing"

    # Adjacent placeholders are valid in the configuration contract. Replacing
    # each placeholder with ``*`` can, however, create an invalid pathlib glob
    # such as ``***.c3d``. Enumerate by the literal suffix and let the compiled
    # template regex perform the exact path selection instead.
    suffix = Path(template).suffix
    discovery_pattern = "*{}".format(suffix) if suffix else "*"
    candidates = sorted(
        path for path in base.rglob(discovery_pattern) if path.is_file()
    )
    patterns = mapping.get("path_patterns") or {}
    regex_text = re.escape(template)
    for name in re.findall(r"\{([^{}]+)\}", template):
        pattern = str(patterns.get(name, "[^/]+"))
        regex_text = regex_text.replace(
            re.escape("{" + name + "}"), "(?:" + pattern + ")", 1
        )
    try:
        regex = re.compile("^" + regex_text + "$")
    except re.error as error:
        return [], "invalid configured path pattern: {}".format(error)
    matches = [
        path for path in candidates if regex.match(path.relative_to(base).as_posix())
    ]
    return matches, None if matches else "no file matched the configured template"


def compare_signal_records(
    reference: Mapping[str, Any], candidate: Mapping[str, Any]
) -> Dict[str, Any]:
    reference_channels = reference.get("channels") or {}
    candidate_channels = candidate.get("channels") or {}
    shared = sorted(set(reference_channels) & set(candidate_channels))
    missing_channels = sorted(set(reference_channels) - set(candidate_channels))
    extra_channels = sorted(set(candidate_channels) - set(reference_channels))
    metrics = {}
    for name in shared:
        metrics[name] = compare_numeric_series(
            reference_channels[name], candidate_channels[name]
        )
    reference_times = reference.get("times") or []
    candidate_times = candidate.get("times") or []
    time_metrics = compare_numeric_series(reference_times, candidate_times)
    fs = float(reference["sampling_rate_hz"])
    precision = float(reference.get("source_precision", 0.0))
    numeric_tolerance = precision / 2.0 if precision > 0 else 1e-12
    reference_events = reference.get("event_samples") or []
    candidate_events = candidate.get("event_samples") or []
    event_differences = [
        abs(int(left) - int(right))
        for left, right in zip(reference_events, candidate_events)
    ]
    event_complete = len(reference_events) == len(candidate_events)
    max_numeric_error = max(
        [value["max_absolute_error"] for value in metrics.values()] or [0.0]
    )
    result = {
        "subject_id": reference.get("subject_id"),
        "reference_format": reference.get("format"),
        "candidate_format": candidate.get("format"),
        "sampling_rate_hz": fs,
        "channel_complete": not missing_channels and not extra_channels,
        "missing_channels": missing_channels,
        "extra_channels": extra_channels,
        "event_complete": event_complete,
        "max_event_difference_samples": max(event_differences or [0]),
        "max_time_difference_samples": time_metrics["max_absolute_error"] * fs,
        "numeric_tolerance": numeric_tolerance,
        "max_numeric_error": max_numeric_error,
        "channel_metrics": metrics,
    }
    result["pass"] = bool(
        result["channel_complete"]
        and result["event_complete"]
        and result["max_event_difference_samples"] <= 1
        and result["max_time_difference_samples"] <= 0.5
        and result["max_numeric_error"] <= numeric_tolerance
    )
    return result


def fault_injection_benchmark() -> Dict[str, Any]:
    cases = [
        ("unit", True, lambda value: setattr(value.channels[0], "unit", "mV")),
        ("side", True, lambda value: setattr(value.channels[0], "side", "right")),
        ("muscle", False, lambda value: setattr(value.channels[0], "muscle", "soleus")),
        (
            "sampling_rate",
            False,
            lambda value: setattr(value.timebase, "sampling_rate_hz", 999.0),
        ),
        (
            "time_offset",
            False,
            lambda value: setattr(value.timebase, "start_seconds", 0.001),
        ),
        (
            "coordinate",
            True,
            lambda value: setattr(
                value.channels[0], "coordinates", {"x": -1.0, "y": 0.0, "z": 0.0}
            ),
        ),
        (
            "required_channel",
            False,
            lambda value: setattr(value.channels[0], "name", "unknown"),
        ),
        ("source_hash", False, lambda value: setattr(value.sources[0], "sha256", "")),
    ]
    profile = _fault_profile()
    clean = assess_bundle(_fault_fixture(), profile)
    clean_specific = clean.dimension_status("semantic_fidelity") == TrustStatus.PASS
    rows = []
    for name, critical, mutate in cases:
        bundle = _fault_fixture()
        mutate(bundle)
        report = assess_bundle(bundle, profile)
        detected = any(item.status == TrustStatus.FAIL for item in report.evidence)
        rows.append({"fault": name, "critical": critical, "detected": detected})
    detected = sum(row["detected"] for row in rows)
    critical_rows = [row for row in rows if row["critical"]]
    summary = {
        "benchmark_version": VALIDATION_VERSION,
        "scope": "controlled pre-data semantic fault benchmark",
        "fault_detection": rows,
        "faults_detected": detected,
        "faults_total": len(rows),
        "overall_sensitivity": detected / float(len(rows)),
        "critical_sensitivity": sum(row["detected"] for row in critical_rows)
        / float(len(critical_rows)),
        "clean_specificity": 1.0 if clean_specific else 0.0,
    }
    summary["pass"] = bool(
        summary["overall_sensitivity"] >= 0.95
        and summary["critical_sensitivity"] == 1.0
        and summary["clean_specificity"] >= 0.95
    )
    return summary


def _fault_fixture() -> HarmoRecordingBundle:
    return HarmoRecordingBundle(
        recording_id="study-1/sub-1/ses-1/emg",
        study_id="study-1",
        subject_id="sub-1",
        session_id="ses-1",
        samples=ArrayReference(
            uri="s3://atlas/signal.zarr",
            format="zarr-v3",
            dtype="float32",
            shape=[100, 2],
            checksum="3" * 64,
        ),
        timebase=Timebase(1000.0, 100),
        channels=[
            ChannelDescriptor(
                "ch-1",
                "TA",
                "V",
                "surface-emg",
                "tibialis anterior",
                "left",
                {"x": 1.0, "y": 0.0, "z": 0.0},
            ),
            ChannelDescriptor(
                "ch-2",
                "GM",
                "V",
                "surface-emg",
                "medial gastrocnemius",
                "left",
                {"x": 0.0, "y": 1.0, "z": 0.0},
            ),
        ],
        sources=[SourceAsset("https://example.test/raw.c3d", "0" * 64, 123)],
    )


def _fault_profile() -> RecordingProfile:
    return RecordingProfile(
        expected_sampling_rate_hz=1000.0,
        expected_start_seconds=0.0,
        units_by_sensor_type={"surface-emg": ["V"]},
        required_channel_names=["TA", "GM"],
        expected_sides_by_channel={"ch-1": "left", "ch-2": "left"},
        expected_muscles_by_channel={
            "ch-1": "tibialis anterior",
            "ch-2": "medial gastrocnemius",
        },
        expected_coordinates_by_channel={
            "ch-1": {"x": 1.0, "y": 0.0, "z": 0.0},
            "ch-2": {"x": 0.0, "y": 1.0, "z": 0.0},
        },
    )


def scientific_manifest(value: Mapping[str, Any]) -> Dict[str, Any]:
    """Remove only fields declared volatile by the frozen protocol."""

    def visit(item: Any) -> Any:
        if isinstance(item, dict):
            return {
                key: visit(val)
                for key, val in sorted(item.items())
                if key not in VOLATILE_MANIFEST_FIELDS
            }
        if isinstance(item, list):
            return [visit(value) for value in item]
        return item

    return visit(dict(value))


def compare_reproducibility_runs(
    paths: Sequence[Path],
    expected_environments: Optional[Sequence[str]] = None,
    reruns_per_environment: Optional[int] = None,
) -> Dict[str, Any]:
    """Compare run manifests and optionally enforce the frozen G5 design."""
    if len(paths) < 2:
        raise ValueError("at least two run manifests are required")
    records = []
    raw_runs = [json.loads(path.read_text(encoding="utf-8")) for path in paths]
    reference = scientific_manifest(raw_runs[0])
    reference_digest = canonical_sha256(reference)
    for path, raw in zip(paths, raw_runs):
        scientific = scientific_manifest(raw)
        digest = canonical_sha256(scientific)
        environment = raw.get("environment", {})
        environment_id = raw.get("environment_id") or canonical_json(environment)
        records.append(
            {
                "manifest": path.name,
                "run_id": raw.get("run_id"),
                "environment_id": environment_id,
                "environment": environment,
                "code_commit": raw.get("code_commit"),
                "code_dirty": raw.get("code_dirty"),
                "normalized_record_sha256": (raw.get("numeric_comparison") or {}).get(
                    "normalized_record_sha256"
                ),
                "scientific_digest": digest,
                "matches_reference": digest == reference_digest,
            }
        )
    environment_counts = Counter(row["environment_id"] for row in records)
    scopes = {raw.get("validation_scope", "unspecified") for raw in raw_runs}
    commits = {row["code_commit"] for row in records if row["code_commit"]}
    run_ids = [row["run_id"] for row in records]
    same_scope = len(scopes) == 1
    same_commit = len(commits) == 1 and all(row["code_commit"] for row in records)
    clean_runs = all(row["code_dirty"] is False for row in records)
    unique_run_ids = all(run_ids) and len(run_ids) == len(set(run_ids))
    run_protocol_pass = all(raw.get("pass") is True for raw in raw_runs)
    scientific_match = all(row["matches_reference"] for row in records)
    normalized_outputs = [
        canonical_json(row["normalized_record_sha256"])
        for row in records
        if row["normalized_record_sha256"] is not None
    ]
    exact_numeric_match = bool(normalized_outputs) and len(set(normalized_outputs)) == 1

    design_requested = expected_environments is not None
    expected_counts = {
        str(environment): int(reruns_per_environment or 0)
        for environment in (expected_environments or [])
    }
    design_pass = bool(
        not design_requested
        or (
            reruns_per_environment is not None
            and reruns_per_environment > 0
            and dict(environment_counts) == expected_counts
            and unique_run_ids
            and same_commit
            and clean_runs
            and run_protocol_pass
        )
    )
    return {
        "scope": next(iter(scopes)) if len(scopes) == 1 else "mixed",
        "run_count": len(records),
        "environment_count": len(environment_counts),
        "environment_run_counts": dict(sorted(environment_counts.items())),
        "expected_environment_run_counts": expected_counts or None,
        "design_requested": design_requested,
        "design_pass": design_pass,
        "same_scope": same_scope,
        "same_commit": same_commit,
        "code_commit": next(iter(commits)) if len(commits) == 1 else None,
        "clean_runs": clean_runs,
        "unique_run_ids": unique_run_ids,
        "run_protocol_pass": run_protocol_pass,
        "exact_numeric_match": exact_numeric_match,
        "max_observed_numeric_difference": 0.0 if exact_numeric_match else None,
        "numeric_acceptance_rule": "exact canonical normalized-record digest equality",
        "reference_scientific_digest": reference_digest,
        "runs": records,
        "pass": bool(scientific_match and same_scope and design_pass),
    }


def create_reproducibility_snapshot(registry: Mapping[str, Any]) -> Dict[str, Any]:
    """Create the same scientific snapshot on macOS and GitHub Actions/Linux."""
    fault = fault_injection_benchmark()
    environment = environment_record()
    created_at = datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")
    return {
        "snapshot_version": VALIDATION_VERSION,
        "validation_scope": "contract-smoke",
        "run_id": "contract-smoke-{}".format(created_at),
        "created_at": created_at,
        "code_commit": environment["code_commit"],
        "code_dirty": environment["code_dirty"] == "true",
        "registry_digest": canonical_sha256(registry),
        "catalog_workbook_digest": registry["catalog_source"]["sha256"],
        "fault_result_digest": canonical_sha256(fault),
        "faults_detected": fault["faults_detected"],
        "faults_total": fault["faults_total"],
        "steps": [
            {
                "name": "validate-registry",
                "software": "harmo",
                "parameters": {"registry_version": registry["registry_version"]},
            },
            {
                "name": "semantic-fault-injection",
                "software": "harmo",
                "parameters": {"fault_count": fault["faults_total"]},
            },
        ],
        "environment": environment,
    }


def opensim_denominator(
    validation_files: Sequence[Path],
    registry: Mapping[str, Any],
    display_root: Optional[Path] = None,
) -> List[Dict[str, Any]]:
    experiment = registry["experiments"]["E"]
    expected_keys = {
        (subject, speed, trial)
        for subject in experiment["subjects"]
        for speed in experiment["speeds"]
        for trial in experiment["trials"]
    }
    observed = {}
    for path in sorted(validation_files):
        value = json.loads(path.read_text(encoding="utf-8"))
        values = value if isinstance(value, list) else [value]
        for row in values:
            if not isinstance(row, dict):
                continue
            subject_match = re.search(r"(\d+)$", str(row.get("subject_id", "")))
            if subject_match is None:
                continue
            key = (
                int(subject_match.group(1)),
                str(row.get("speed") or _session_part(row.get("session_id"), "V")),
                str(row.get("trial") or _session_part(row.get("session_id"), "T")),
            )
            if key not in expected_keys:
                raise ValueError(
                    "OpenSim validation identity is outside the frozen protocol: {} in {}".format(
                        key, path
                    )
                )
            if key in observed:
                raise ValueError(
                    "duplicate OpenSim validation identity {} in {} and {}".format(
                        key, observed[key][1], path
                    )
                )
            observed[key] = (row, path)
    rows = []
    for subject in experiment["subjects"]:
        for speed in experiment["speeds"]:
            for trial in experiment["trials"]:
                key = (subject, speed, trial)
                raw, path = observed.get(key, ({}, None))
                metrics = _opensim_metrics(raw, experiment["metric_thresholds"])
                statuses = {
                    name: "unassessed"
                    if value is None
                    else ("pass" if value <= limit else "fail")
                    for name, (value, limit) in metrics.items()
                }
                overall = (
                    "unassessed"
                    if not raw
                    else (
                        "pass"
                        if all(value == "pass" for value in statuses.values())
                        else "fail"
                        if any(value == "fail" for value in statuses.values())
                        else "unassessed"
                    )
                )
                row = {
                    "study_id": "study31",
                    "subject_id": str(subject),
                    "speed": speed,
                    "trial": trial,
                    "session_id": "ses-{}-{}".format(speed, trial),
                    "source_validation": _display_validation_path(path, display_root),
                    "source_validation_sha256": file_sha256(path) if path else None,
                    "outcome": overall,
                }
                for name, (value, limit) in metrics.items():
                    row[name] = value
                    row[name + "_threshold"] = limit
                    row[name + "_status"] = statuses[name]
                rows.append(row)
    return rows


def _display_validation_path(
    path: Optional[Path], display_root: Optional[Path]
) -> Optional[str]:
    if path is None:
        return None
    if display_root is not None:
        try:
            return path.resolve().relative_to(display_root.resolve()).as_posix()
        except ValueError:
            pass
    return path.name


def _session_part(session: Any, prefix: str) -> str:
    match = re.search(r"(?:^|-)({}\d+)(?:-|$)".format(prefix), str(session or ""))
    return match.group(1) if match else ""


def _ratio(row: Mapping[str, Any], numerator: str, denominator: str) -> Optional[float]:
    left = row.get(numerator)
    right = row.get(denominator)
    if left is None or right in (None, 0, 0.0):
        return None
    ratio = abs(float(left) / float(right))
    if not math.isfinite(ratio):
        raise ValueError(
            "OpenSim ratio {}/{} must be finite".format(numerator, denominator)
        )
    return ratio


def _optional_float(value: Any) -> Optional[float]:
    if value is None:
        return None
    number = float(value)
    if not math.isfinite(number):
        raise ValueError("OpenSim metric must be finite, got {!r}".format(value))
    return number


def _opensim_metrics(
    row: Mapping[str, Any],
    thresholds: Mapping[str, Any],
) -> Dict[str, Tuple[Optional[float], float]]:
    return {
        "ik_rms_error_m": (
            _optional_float(row.get("ik_rms_error")),
            float(thresholds["ik_rms_error_m"]),
        ),
        "ik_max_error_m": (
            _optional_float(row.get("ik_max_error")),
            float(thresholds["ik_max_error_m"]),
        ),
        "residual_force_fraction": (
            _ratio(row, "residual_force_rms", "ex_loads_rms"),
            float(thresholds["residual_force_fraction"]),
        ),
        "residual_moment_fraction": (
            _ratio(row, "residual_moment_rms", "ex_moment_rms"),
            float(thresholds["residual_moment_fraction"]),
        ),
        "rra_angle_change_rms_deg": (
            _optional_float(row.get("rra_angle_change_rms_deg")),
            float(thresholds["rra_angle_change_rms_deg"]),
        ),
    }


def subject_hierarchical_bootstrap(
    rows: Sequence[Mapping[str, Any]],
    value_key: str,
    subject_key: str = "subject_id",
    iterations: int = 10000,
    seed: int = 20260720,
) -> Dict[str, float]:
    grouped = defaultdict(list)
    for row in rows:
        value = row.get(value_key)
        if value is not None:
            grouped[str(row[subject_key])].append(float(value))
    if not grouped:
        raise ValueError("bootstrap requires at least one assessed subject")
    subjects = sorted(grouped)
    subject_means = {key: sum(values) / len(values) for key, values in grouped.items()}
    rng = random.Random(seed)
    draws = []
    for _ in range(iterations):
        sample = [subjects[rng.randrange(len(subjects))] for _ in subjects]
        draws.append(sum(subject_means[key] for key in sample) / len(sample))
    draws.sort()
    return {
        "subjects": float(len(subjects)),
        "estimate": sum(subject_means.values()) / len(subject_means),
        "ci_lower": _quantile(draws, 0.025),
        "ci_upper": _quantile(draws, 0.975),
        "iterations": float(iterations),
        "seed": float(seed),
    }


def physiology_timing_metrics(
    rows: Sequence[Mapping[str, Any]], registry: Mapping[str, Any]
) -> Dict[str, Any]:
    """Derive timing-only gait controls without pooling incompatible amplitudes."""
    experiment = registry["experiments"]["G"]
    muscles = set(experiment["primary_muscles"])
    minimum_subjects = int(experiment["minimum_subjects_per_study"])
    detailed = []
    subjects_by_study = defaultdict(set)
    normalization_by_study = defaultdict(set)
    for row in rows:
        muscle = str(row.get("muscle", ""))
        if muscle not in muscles:
            continue
        values = [float(value) for value in row.get("values", [])]
        if len(values) < 2 or any(not math.isfinite(value) for value in values):
            continue
        nonnegative = [max(value, 0.0) for value in values]
        total = sum(nonnegative)
        if total <= 0:
            peak = None
            centroid = None
            outcome = "unassessed"
        else:
            denominator = float(len(values) - 1)
            peak = nonnegative.index(max(nonnegative)) / denominator * 100.0
            centroid = (
                sum(
                    (index / denominator * 100.0) * value
                    for index, value in enumerate(nonnegative)
                )
                / total
            )
            outcome = "assessed"
        study_id = str(row.get("study_id", ""))
        subject_id = str(row.get("subject_id", ""))
        subjects_by_study[study_id].add(subject_id)
        normalization_by_study[study_id].add(
            str(row.get("normalization_method", "unassessed"))
        )
        detailed.append(
            {
                "study_id": study_id,
                "subject_id": subject_id,
                "muscle": muscle,
                "side": row.get("side"),
                "normalization_method": row.get("normalization_method", "unassessed"),
                "peak_timing_percent_gait_cycle": peak,
                "activation_centroid_percent_gait_cycle": centroid,
                "outcome": outcome,
            }
        )
    eligibility = [
        {
            "study_id": study_id,
            "subject_count": len(subjects),
            "eligible": len(subjects) >= minimum_subjects,
            "normalization_methods": sorted(normalization_by_study[study_id]),
            "amplitude_pooling_permitted": False,
        }
        for study_id, subjects in sorted(subjects_by_study.items())
    ]
    return {
        "metrics": detailed,
        "study_eligibility": eligibility,
        "primary_muscles": sorted(muscles),
        "amplitude_pooled_across_studies": False,
    }


def validate_adapter_metrics(value: Mapping[str, Any]) -> Dict[str, Any]:
    required = {
        "dataset",
        "core_dataset_id_branches",
        "core_changed_lines",
        "adapter_changed_lines",
        "source_count",
        "accounted_source_count",
        "active_work_seconds",
    }
    missing = sorted(required - set(value))
    if missing:
        raise ValueError("adapter metrics lack fields: {}".format(missing))
    source_count = int(value["source_count"])
    accounted = int(value["accounted_source_count"])
    result = dict(value)
    result["all_sources_accounted"] = source_count > 0 and accounted == source_count
    result["pass"] = bool(
        int(value["core_dataset_id_branches"]) == 0 and result["all_sources_accounted"]
    )
    return result


def _quantile(values: Sequence[float], probability: float) -> float:
    index = (len(values) - 1) * probability
    lower = int(math.floor(index))
    upper = int(math.ceil(index))
    if lower == upper:
        return values[lower]
    weight = index - lower
    return values[lower] * (1.0 - weight) + values[upper] * weight


def gate_audit(
    registry: Mapping[str, Any],
    results_dir: Path,
    source_registry_path: Optional[Path] = None,
) -> Dict[str, Any]:
    checks = []

    def add(
        gate: str,
        condition: bool,
        message: str,
        submission_blocking: bool = True,
    ) -> None:
        checks.append(
            {
                "gate": gate,
                "pass": bool(condition),
                "message": message,
                "submission_blocking": bool(submission_blocking),
            }
        )

    mapping_path = results_dir / "mapping_outcomes.json"
    mapping_denominator = int(registry["experiments"]["A"]["denominator"])
    if mapping_path.is_file():
        rows = json.loads(mapping_path.read_text(encoding="utf-8"))
        add(
            "G4",
            len(rows) == mapping_denominator,
            "mapping denominator must be {}".format(mapping_denominator),
        )
        add(
            "G4",
            all(row.get("outcome") in OUTCOMES for row in rows),
            "all mapping outcomes are explicit",
        )
        add(
            "G4",
            all(row.get("matched_files", 0) > 0 for row in rows),
            "all active mappings match source files",
        )
        add(
            "G4",
            all(row.get("outcome") == "converted" for row in rows),
            "all active mappings complete conversion and evidence generation",
        )
        add(
            "G4",
            all(row.get("source_checksum_pass") is True for row in rows),
            "every mapping is bound to a checksum-verified source asset",
        )
        add(
            "G4",
            all(row.get("reproducibility_pass") is True for row in rows),
            "every mapping has identical normalized output on repeat execution",
        )
        add(
            "G4",
            all(row.get("trust_state") == "traceable-format-ready" for row in rows),
            "every mapping has a traceable, comparable time axis",
        )
        add(
            "G4",
            all(not (row.get("semantic_issues") or []) for row in rows),
            "every mapping passes semantic metadata and channel checks",
        )
    else:
        add("G4", False, "mapping_outcomes.json is missing")
    corpus_path = results_dir / "corpus_execution.json"
    if corpus_path.is_file():
        corpus = json.loads(corpus_path.read_text(encoding="utf-8"))
        expected_scope = "frozen-{}-mapping-corpus".format(mapping_denominator)
        add(
            "G4",
            corpus.get("scope") == expected_scope,
            "corpus execution scope is {}".format(expected_scope),
        )
        add(
            "G4",
            corpus.get("verified_source_assets") == corpus.get("source_asset_count")
            and int(corpus.get("source_asset_count", 0)) > 0,
            "all frozen source assets pass size, checksum, and archive-index checks",
        )
        add(
            "G4",
            corpus.get("within_environment_reproducible") is True,
            "complete corpus is reproducible within the execution environment",
        )
        add(
            "G4",
            corpus.get("trust_ready") is True,
            "complete corpus passes the trust-readiness criteria",
        )
        add(
            "G4",
            (corpus.get("environment") or {}).get("code_dirty") == "false",
            "corpus evidence was generated from a clean committed tree",
        )
    else:
        add("G4", False, "corpus_execution.json is missing")
    fault_path = results_dir / "fault_detection.json"
    if fault_path.is_file():
        fault = json.loads(fault_path.read_text(encoding="utf-8"))
        add(
            "G3",
            fault.get("overall_sensitivity", 0) >= 0.95,
            "overall fault sensitivity >= 0.95",
        )
        add(
            "G3",
            fault.get("critical_sensitivity") == 1.0,
            "critical fault sensitivity == 1.0",
        )
    else:
        add("G3", False, "fault_detection.json is missing")
    fidelity_path = results_dir / "format_fidelity.json"
    if fidelity_path.is_file():
        fidelity = json.loads(fidelity_path.read_text(encoding="utf-8"))
        if isinstance(fidelity, list):
            add(
                "G3",
                bool(fidelity) and all(row.get("pass") is True for row in fidelity),
                "legacy frozen format-fidelity comparisons pass",
            )
        else:
            format_experiment = registry["experiments"]["B"]
            subjects = len(format_experiment["subjects"])
            required = subjects * len(format_experiment["required_equivalence_pairs"])
            c3d_audits = subjects * len(
                format_experiment["c3d_interchangeability_audits"]
            )
            denominator = fidelity.get("denominator") or {}
            add(
                "G3",
                fidelity.get("scope")
                == "checksum-bound-study31-source-format-fidelity",
                "format fidelity uses the frozen checksum-bound Study 31 scope",
            )
            add(
                "G3",
                len(fidelity.get("source_assets") or {}) == 3
                and all(
                    row.get("pass") is True
                    for row in (fidelity.get("source_assets") or {}).values()
                ),
                "ASCII, MAT, and C3D source assets pass checksum and archive checks",
            )
            add(
                "G3",
                denominator.get("comparison_attempts") == required + c3d_audits,
                "all frozen subject-format comparison attempts remain in the denominator",
            )
            add(
                "G3",
                denominator.get("required_equivalence_comparisons") == required
                and denominator.get("required_equivalence_passed") == required,
                "all ASCII-MAT equivalence controls pass or retain registered source absence",
            )
            add(
                "G3",
                denominator.get("c3d_interchangeability_audits") == c3d_audits
                and denominator.get("c3d_audits_classified") == c3d_audits,
                "all C3D interchangeability audits are classified and discordance is quarantined",
            )
            add(
                "G3",
                (fidelity.get("reproducibility") or {}).get("pass") is True,
                "format-fidelity result is identical on repeated execution",
            )
            add(
                "G3",
                (fidelity.get("environment") or {}).get("code_dirty") == "false",
                "format-fidelity evidence was generated from a clean committed tree",
            )
            add(
                "G3",
                fidelity.get("pass") is True,
                "frozen format-fidelity protocol result passes",
            )
    else:
        add("G3", False, "format_fidelity.json is missing")
    reproducibility_path = results_dir / "reproducibility.json"
    if reproducibility_path.is_file():
        value = json.loads(reproducibility_path.read_text(encoding="utf-8"))
        expected_repro = registry["experiments"]["D"]
        expected_environment_counts = {
            str(environment): int(expected_repro["reruns_per_environment"])
            for environment in expected_repro["environments"]
        }
        add("G5", value.get("run_count") == 6, "two environments x three runs")
        add(
            "G5",
            value.get("environment_run_counts") == expected_environment_counts,
            "each frozen independent environment contributes exactly three runs",
        )
        add(
            "G5",
            value.get("same_commit") is True
            and value.get("clean_runs") is True
            and value.get("unique_run_ids") is True,
            "all six unique runs use the same clean commit",
        )
        add(
            "G5",
            value.get("run_protocol_pass") is True,
            "every source run passes its checksum, denominator, and within-run gates",
        )
        add(
            "G5",
            value.get("exact_numeric_match") is True
            and value.get("max_observed_numeric_difference") == 0.0,
            "canonical normalized records are exactly equal across all six runs",
        )
        add(
            "G5",
            value.get("design_pass") is True and value.get("pass") is True,
            "confirmatory design and scientific manifest equality pass",
        )
        expected_scope = "frozen-{}-mapping-corpus".format(mapping_denominator)
        add(
            "G5",
            value.get("scope") == expected_scope,
            "confirmatory reproducibility covers the {}".format(expected_scope),
        )
    else:
        add("G5", False, "reproducibility.json is missing")
    opensim_path = results_dir / "opensim_quality.json"
    opensim_summary_path = results_dir / "opensim_quality_summary.json"
    if opensim_path.is_file():
        rows = json.loads(opensim_path.read_text(encoding="utf-8"))
        add("G6", len(rows) == 336, "OpenSim denominator must be 336")
        add(
            "G6",
            len(
                {
                    (row.get("subject_id"), row.get("speed"), row.get("trial"))
                    for row in rows
                }
            )
            == 336,
            "all OpenSim trial identities are unique",
        )
        add(
            "G6",
            all(row.get("outcome") in {"pass", "fail", "unassessed"} for row in rows),
            "every OpenSim trial has an outcome",
        )
    else:
        add("G6", False, "opensim_quality.json is missing")
    if opensim_summary_path.is_file():
        summary = json.loads(opensim_summary_path.read_text(encoding="utf-8"))
        opensim_checks = summary.get("checks") or {}
        add(
            "G6",
            summary.get("audit_complete") is True,
            "OpenSim source audit is checksum-bound and complete",
        )
        add(
            "G6",
            opensim_checks.get("all_frozen_trials_source_available") is True,
            "all 336 frozen trials are present in the official source release",
            submission_blocking=False,
        )
        add(
            "G6",
            opensim_checks.get("released_static_calibration_available") is True,
            "released static calibration supports subject-specific scaling",
            submission_blocking=False,
        )
        add(
            "G6",
            opensim_checks.get("runtime_version_match") is True
            and opensim_checks.get("runtime_identity_pinned") is True
            and opensim_checks.get("rra_tool_available") is True
            and opensim_checks.get("moco_inverse_available") is True,
            "runtime matches the frozen OpenSim version, is digest-pinned, and "
            "exposes RRATool and MocoInverse",
        )
        add(
            "G6",
            opensim_checks.get("all_source_available_trials_completely_assessed")
            is True,
            "all source-available trials contain every frozen quality metric",
            submission_blocking=False,
        )
        add(
            "G6",
            opensim_checks.get("no_threshold_failures") is True,
            "no completely or partially assessed trial exceeds a quality threshold",
            submission_blocking=False,
        )
        add(
            "G6",
            summary.get("pass") is True,
            "complete frozen G6 protocol passes",
            submission_blocking=False,
        )
    else:
        add("G6", False, "opensim_quality_summary.json is missing")
    disposition_path = results_dir / "g6_mechanics_disposition.json"
    disposition_policy_path = (
        results_dir.parent.parent / "g6_mechanics_disposition_policy.yaml"
    )
    if disposition_path.is_file() and disposition_policy_path.is_file():
        disposition = json.loads(disposition_path.read_text(encoding="utf-8"))
        repository_root = results_dir.parents[2]
        disposition_checks = validate_mechanics_disposition(
            disposition,
            results_dir,
            repository_root,
            disposition_policy_path,
        )
        for name, condition in disposition_checks.items():
            add(
                "G6-reporting",
                condition,
                "final mechanics disposition: {}".format(name.replace("_", " ")),
            )
    else:
        add(
            "G6-reporting",
            False,
            "g6_mechanics_disposition.json and its policy are required",
        )
    adapter_path = results_dir / "adapter_metrics.json"
    if adapter_path.is_file():
        adapter = json.loads(adapter_path.read_text(encoding="utf-8"))
        add(
            "G7",
            adapter.get("core_dataset_id_branches") == 0,
            "held-out adapter adds no core dataset-ID branches",
        )
        add(
            "G7",
            adapter.get("all_sources_accounted") is True,
            "all held-out sources are accounted",
        )
    else:
        add("G7", False, "adapter_metrics.json is missing")
    physiology_path = results_dir / "physiology_positive_control.json"
    if physiology_path.is_file():
        physiology = json.loads(physiology_path.read_text(encoding="utf-8"))
        eligibility = physiology.get("study_eligibility") or []
        add(
            "G3",
            len(eligibility) == 2
            and all(row.get("eligible") is True for row in eligibility),
            "both frozen physiology-control studies meet their exact subject denominator",
        )
        add(
            "G3",
            physiology.get("reporting_complete") is True,
            "physiology-control provenance and complete denominators are bound",
        )
        add(
            "G3",
            physiology.get("scientific_endpoint_pass") is True
            and physiology.get("positive_control_pass") is True,
            "prespecified cross-study physiological positive control passes",
        )
        add(
            "G3",
            physiology.get("amplitude_pooled_across_studies") is False,
            "incompatible cross-study amplitudes are not pooled",
        )
        add(
            "G3",
            len(physiology.get("source_attestations") or []) == 11
            and all(
                row.get("pass") is True
                for row in physiology.get("source_attestations", [])
            ),
            "all physiology-control source assets match frozen identities",
        )
        gait120_labels = {
            (row.get("muscle"), row.get("source_channel_label"))
            for row in physiology.get("metrics", [])
            if row.get("study_id") == "study_120"
        }
        add(
            "G3",
            gait120_labels
            == {
                ("tibialis anterior", "TibialisAnterior"),
                ("medial gastrocnemius", "GastrocnemuisMedialis"),
            },
            "Gait120 physiology metrics bind source-declared MCOS channel labels",
        )
        add(
            "G3",
            bool(physiology.get("metrics"))
            and all(
                row.get("outcome") in {"assessed", "unassessed"}
                for row in physiology.get("metrics", [])
            ),
            "physiology metrics retain explicit outcomes",
        )
        physiology_repro_path = (
            results_dir / "physiology_positive_control_reproducibility.json"
        )
        if physiology_repro_path.is_file():
            physiology_repro = json.loads(
                physiology_repro_path.read_text(encoding="utf-8")
            )
            add(
                "G3",
                physiology_repro.get("byte_identical") is True
                and physiology_repro.get("corrected_internal_result_sha256")
                == physiology.get("result_sha256")
                and physiology_repro.get("corrected_result_file_sha256")
                == file_sha256(physiology_path),
                "corrected physiology-control result is byte-identical on rerun",
            )
        else:
            add(
                "G3",
                False,
                "physiology_positive_control_reproducibility.json is missing",
            )
    else:
        add("G3", False, "physiology_positive_control.json is missing")

    if source_registry_path is None:
        source_registry_path = results_dir.parent.parent / "source_releases.yaml"
    if source_registry_path.is_file():
        source_registry = (
            yaml.safe_load(source_registry_path.read_text(encoding="utf-8")) or {}
        )
        study_ids = [str(study["id"]) for study in registry["studies"]]
        rights = source_rights_report(source_registry, study_ids)
        source_by_id = source_registry.get("sources") or {}
        rights_aligned = all(
            source_by_id.get(str(study["id"]), {}).get("license")
            == study.get("license")
            and source_by_id.get(str(study["id"]), {}).get("redistribution")
            == study.get("redistribution")
            for study in registry["studies"]
        )
        add(
            "M0",
            rights.get("pass") is True,
            "all eight releases have official repository rights evidence",
        )
        add(
            "M0",
            rights_aligned,
            "validation and source registries agree on licence and redistribution",
        )
    else:
        add("M0", False, "source_releases.yaml is missing")
    release_verification_path = results_dir / "source_release_verification.json"
    if release_verification_path.is_file():
        release_verification = json.loads(
            release_verification_path.read_text(encoding="utf-8")
        )
        add(
            "M0",
            release_verification.get("online_requested") is True,
            "source release evidence was checked against live official APIs",
        )
        add(
            "M0",
            release_verification.get("pass") is True,
            "live official release identities, manifests, and rights match the registry",
        )
    else:
        add("M0", False, "source_release_verification.json is missing")
    summary = Counter("pass" if item["pass"] else "fail" for item in checks)
    blocking_failures = [
        item
        for item in checks
        if item["submission_blocking"] and not item["pass"]
    ]
    return {
        "audit_version": VALIDATION_VERSION,
        "registry_digest": canonical_sha256(registry),
        "checks": checks,
        "passed_checks": summary["pass"],
        "failed_checks": summary["fail"],
        "blocking_failed_checks": len(blocking_failures),
        "all_scientific_endpoints_pass": summary["fail"] == 0,
        "submission_ready": not blocking_failures,
    }
