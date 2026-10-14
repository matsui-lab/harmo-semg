"""Materialize real, bounded EMG records into R/M/T/C/D products.

This module is deliberately narrower than full-dataset ingestion.  It takes
the six mapping types that passed the repository-level task/anatomy readiness
audit, binds them to verified public source archives, and creates two
source-derived, source-spanning 100-timepoint records per mapping.  The bounded
scope is carried as a release condition and is never presented as a full
recording.
"""
from __future__ import annotations

import json
import os
import re
import shutil
import tempfile
import zipfile
import zlib
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Mapping, Optional, Sequence, Tuple

import pandas as pd
import yaml

from harmo import __version__
from harmo.evidence.adapters import bundle_from_wide_dataframe
from harmo.evidence.anatomy_crosswalk import (
    VerifiedAnatomyCrosswalk,
    verify_anatomy_crosswalk,
)
from harmo.evidence.audit import export_atlas_exchange
from harmo.evidence.io import (
    sha256_contract,
    sha256_file,
    write_json_atomic,
)
from harmo.evidence.models import (
    ArtifactReference,
    EvidenceManifest,
    HarmoRecordingBundle,
    TransformationStep,
    TrustEvidence,
    TrustReport,
    TrustStatus,
)
from harmo.evidence.semantic_claims import SemanticOverlay
from harmo.evidence.policy import (
    Comparability,
    ComparabilityPolicyEngine,
    ComparabilityRequest,
    ReleasePolicy,
)
from harmo.evidence.run import current_repository_state, runtime_environment
from harmo.evidence.validation import RecordingProfile, assess_bundle
from harmo.evidence.workflow import _augment_workflow_trust
from harmo.real_data_validation import (
    _parse_once,
    _resolve_channel_definition,
    load_channel_definitions,
    mapping_regex,
    normalized_records_digest,
)
from harmo.validation_corpus import (
    _download_asset,
    _extract_member,
    _logical_zip_members,
    _mapping_table,
    verify_corpus_asset,
)
from harmo.warehouse.schema import (
    ATLAS_CHANNEL,
    ATLAS_COMPARABILITY,
    ATLAS_RECORD,
    ATLAS_RELEASE,
    ATLAS_TASK,
)


MATERIALIZATION_VERSION = "1.0.0"
SEMANTIC_OVERLAY_AUDIT_VERSION = "1.0.0"
DEFAULT_READY_MAPPING_IDS = (
    "study_28-004",
    "study_28-008",
    "study_33-002",
    "study_33-005",
    "study_33-008",
    "study_33-011",
)
_ALLOWED_ROLE_DIFFERENCES = {"trial_id", "trial_num", "rep", "sub_id", "subject_dir"}


def _normalized_selector_value(value: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", str(value).casefold()).strip("-")


def _overlay_applies_to_bundle(
    overlay: SemanticOverlay,
    bundle: HarmoRecordingBundle,
) -> Tuple[bool, str]:
    """Fail-closed binding of one explicit overlay target to one recording."""
    if overlay.study_id != bundle.study_id:
        return False, "different study"
    selector = overlay.target_scope_selector
    direct = {
        "study_id": bundle.study_id,
        "subject_id": bundle.subject_id,
        "session_id": bundle.session_id,
        "recording_id": bundle.recording_id,
    }
    for key in set(selector).intersection(direct):
        if _normalized_selector_value(selector[key]) != _normalized_selector_value(
            direct[key]
        ):
            return False, "{} differs".format(key)
    sensor_types = {
        _normalized_selector_value(channel.sensor_type)
        for channel in bundle.channels
    }
    if "sensor_type" in selector and (
        _normalized_selector_value(selector["sensor_type"]) not in sensor_types
    ):
        return False, "sensor_type differs"
    if "channel_id" in selector and selector["channel_id"] not in {
        channel.channel_id for channel in bundle.channels
    }:
        return False, "channel_id differs"
    if "representation" in selector:
        if bundle.processing_state is None:
            return False, "recording representation is unavailable"
        if _normalized_selector_value(
            selector["representation"]
        ) != _normalized_selector_value(bundle.processing_state.representation):
            return False, "representation differs"
    known = set(direct) | {"sensor_type", "channel_id", "representation"}
    unknown = sorted(set(selector) - known)
    if unknown:
        return False, "unverifiable target selector: {}".format(
            ", ".join(unknown)
        )
    required = {
        "subject": "subject_id",
        "session": "session_id",
        "trial": "recording_id",
        "channel-group": "sensor_type",
        "channel": "channel_id",
    }.get(overlay.target_scope_level)
    if required and required not in selector:
        return False, "{} target lacks {}".format(
            overlay.target_scope_level, required
        )
    if overlay.target_scope_level == "cohort":
        return False, "recording bundle has no checksum-bound cohort identity"
    return True, "overlay target is explicitly bound to recording"


def _semantic_overlay_inputs(
    bundle: HarmoRecordingBundle,
    overlay_paths: Sequence[Path],
    repository_root: Path,
) -> Tuple[List[ArtifactReference], List[SemanticOverlay]]:
    """Load applicable overlays and reject conflicting same-study bindings."""
    artifacts: List[ArtifactReference] = []
    overlays: List[SemanticOverlay] = []
    property_values: Dict[str, Any] = {}
    for path in overlay_paths:
        overlay = SemanticOverlay.from_dict(
            json.loads(path.read_text(encoding="utf-8"))
        )
        applies, reason = _overlay_applies_to_bundle(overlay, bundle)
        if not applies:
            if reason == "different study":
                continue
            raise ValueError(
                "{} cannot bind to {}: {}".format(
                    overlay.overlay_id, bundle.recording_id, reason
                )
            )
        for entry in overlay.entries:
            if (
                entry.property_path in property_values
                and property_values[entry.property_path] != entry.value
            ):
                raise ValueError(
                    "semantic overlays disagree for {}".format(
                        entry.property_path
                    )
                )
            property_values[entry.property_path] = entry.value
        try:
            uri = str(path.relative_to(repository_root))
        except ValueError:
            uri = path.as_uri()
        artifacts.append(
            ArtifactReference(
                overlay.overlay_id,
                uri,
                sha256_file(str(path)),
                "application/json",
            )
        )
        overlays.append(overlay)
    return artifacts, overlays


def _frozen_source_entry(
    inventory: Sequence[Mapping[str, Any]],
    study_id: str,
    asset_id: str,
    asset: Mapping[str, Any],
) -> Dict[str, Any]:
    """Return a strictly matched prior full-archive verification record."""
    matches = [
        dict(row)
        for row in inventory
        if row.get("study_id") == study_id and row.get("asset_id") == asset_id
    ]
    if len(matches) != 1:
        raise ValueError(
            "source inventory must contain exactly one {}::{} row".format(
                study_id, asset_id
            )
        )
    row = matches[0]
    expected = {
        "pass": True,
        "local_name": asset["local_name"],
        "size_bytes": int(asset["size_bytes"]),
        "checksum_algorithm": asset["checksum_algorithm"],
        "checksum": asset["checksum"],
        "source_record_doi": asset["source_record_doi"],
    }
    mismatches = {
        key: {"inventory": row.get(key), "corpus": value}
        for key, value in expected.items()
        if row.get(key) != value
    }
    archive_sha = str(row.get("sha256", ""))
    if not re.fullmatch(r"[0-9a-f]{64}", archive_sha):
        mismatches["sha256"] = {
            "inventory": archive_sha,
            "corpus": "64 lowercase hexadecimal characters",
        }
    if mismatches:
        raise ValueError(
            "{}::{} frozen source verification does not match the corpus: {}".format(
                study_id, asset_id, mismatches
            )
        )
    return row


def _source_release_terms(
    source_releases: Mapping[str, Any],
    study_id: str,
) -> Dict[str, str]:
    sources = source_releases.get("sources")
    if not isinstance(sources, Mapping) or not isinstance(
        sources.get(study_id), Mapping
    ):
        raise ValueError("source release registry lacks {}".format(study_id))
    row = sources[study_id]
    required = ("license", "license_url", "redistribution", "attribution")
    missing = [key for key in required if not str(row.get(key, "")).strip()]
    if missing:
        raise ValueError(
            "{} source rights metadata lacks {}".format(study_id, missing)
        )
    redistribution = str(row["redistribution"])
    if redistribution not in {"permitted", "permitted-with-attribution"}:
        raise ValueError(
            "{} source release does not permit redistribution".format(study_id)
        )
    return {key: str(row[key]) for key in required}


def _remote_zip_pairs(
    url: str, asset: Mapping[str, Any]
) -> List[Tuple[str, str]]:
    """Read only a remote ZIP index by HTTP byte ranges."""
    try:
        import fsspec
    except ImportError as exc:
        raise RuntimeError(
            "remote member materialization requires harmo[validation]"
        ) from exc
    with fsspec.open(
        url,
        "rb",
        block_size=1024 * 1024,
        cache_type="readahead",
    ).open() as source:
        with zipfile.ZipFile(source) as archive:
            return _logical_zip_members(archive, asset)


def _extract_remote_member(
    url: str,
    raw_member: str,
    target_dir: Path,
) -> Tuple[Path, str]:
    """Range-fetch one ZIP member, requiring ZIP CRC verification."""
    try:
        import fsspec
    except ImportError as exc:
        raise RuntimeError(
            "remote member materialization requires harmo[validation]"
        ) from exc
    target_dir.mkdir(parents=True, exist_ok=True)
    target = target_dir / Path(raw_member).name
    temporary = target.with_name(target.name + ".part")
    with fsspec.open(
        url,
        "rb",
        block_size=1024 * 1024,
        cache_type="readahead",
    ).open() as source:
        with zipfile.ZipFile(source) as archive:
            info = archive.getinfo(raw_member)
            if info.is_dir():
                raise ValueError("selected ZIP member is a directory")
            expected_crc32 = "{:08x}".format(info.CRC)
            if (
                target.is_file()
                and target.stat().st_size == info.file_size
                and _crc32_file(target) == expected_crc32
            ):
                return target, expected_crc32
            with archive.open(info) as member, temporary.open("wb") as output:
                shutil.copyfileobj(member, output, length=1024 * 1024)
    if temporary.stat().st_size != info.file_size:
        temporary.unlink()
        raise ValueError("remote ZIP member size verification failed")
    os.replace(str(temporary), str(target))
    return target, expected_crc32


def _crc32_file(path: Path) -> str:
    checksum = 0
    with path.open("rb") as source:
        for chunk in iter(lambda: source.read(1024 * 1024), b""):
            checksum = zlib.crc32(chunk, checksum)
    return "{:08x}".format(checksum & 0xFFFFFFFF)


def _load_yaml(path: Path) -> Dict[str, Any]:
    value = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    if not isinstance(value, dict):
        raise ValueError("{} must contain an object".format(path))
    return value


def _write_frame_atomic(path: Path, frame: pd.DataFrame) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, temporary = tempfile.mkstemp(prefix=path.name + ".", dir=str(path.parent))
    os.close(fd)
    try:
        frame.to_csv(
            temporary,
            index=False,
            encoding="utf-8",
            float_format="%.17g",
            lineterminator="\n",
        )
        os.replace(temporary, str(path))
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)


def _side(value: object) -> str:
    normalized = str(value or "unknown").strip().lower()
    return normalized if normalized in {"left", "right", "midline"} else "unknown"


def _canonical_muscle(value: object) -> str:
    result = re.sub(
        r"_+",
        "_",
        str(value or "").strip().lower().replace("-", "_").replace(" ", "_"),
    )
    if not result:
        raise ValueError("channel muscle declaration is absent")
    return result


def _semantic_evidence_ref(
    path: Path,
    label: str,
    repository_root: Optional[Path] = None,
) -> str:
    if repository_root is None:
        uri = path.as_uri()
    else:
        relative = path.resolve().relative_to(repository_root.resolve())
        uri = "urn:harmo:repository-file:{}".format(relative.as_posix())
    return "{}#{}:sha256:{}".format(uri, label, sha256_file(str(path)))


def _task_metadata(
    study_id: str,
    mapping: Mapping[str, Any],
    groups: Mapping[str, str],
    config_path: Path,
    hed_path: Path,
    repository_root: Optional[Path] = None,
) -> Dict[str, Any]:
    tag = str(mapping.get("hed_tag", ""))
    if not tag:
        raise ValueError("mapping lacks a task tag")
    hed = json.loads(hed_path.read_text(encoding="utf-8"))
    hed_terms: Mapping[str, Any] = {}
    for dimension in hed.values():
        if isinstance(dimension, Mapping) and isinstance(
            dimension.get("HED"), Mapping
        ):
            hed_terms = dimension["HED"]
            break
    if tag not in hed_terms:
        raise ValueError("task tag {} is absent from {}".format(tag, hed_path))

    facets: Dict[str, str]
    ontology_ids = ["Walk"]
    compatibility: str
    if tag == "levelground":
        facets = {"terrain": "level"}
        for key in ("direction", "speed"):
            if groups.get(key):
                facets[key] = str(groups[key])
        compatibility = "walking/level/" + "/".join(
            "{}={}".format(key, facets[key]) for key in sorted(facets)
        )
    elif tag == "ramp":
        facets = {"terrain": "sloped"}
        for key in ("ramp_grade", "side"):
            if groups.get(key):
                facets[key] = str(groups[key])
        ontology_ids.append("Sloped-terrain")
        compatibility = "walking/ramp/" + "/".join(
            "{}={}".format(key, facets[key]) for key in sorted(facets)
        )
    elif tag == "stair":
        facets = {"terrain": "stairs"}
        for key in ("sum", "direction"):
            if groups.get(key):
                facets[key] = str(groups[key])
        ontology_ids.append("Uneven-terrain")
        compatibility = "walking/stairs/" + "/".join(
            "{}={}".format(key, facets[key]) for key in sorted(facets)
        )
    elif tag == "treadmill":
        facets = {"terrain": "treadmill"}
        ontology_ids.append("On-treadmill")
        compatibility = "walking/treadmill"
    else:
        raise ValueError("unsupported ready task tag: {}".format(tag))
    facets["sampling_scope"] = "source-spanning-100-timepoints"
    mapping_evidence = [
        _semantic_evidence_ref(
            config_path, str(mapping.get("name", tag)), repository_root
        ),
        _semantic_evidence_ref(hed_path, tag, repository_root),
    ]
    return {
        "activity": "walking",
        "source_label": tag,
        "ontology_terms": [
            {
                "ontology": "HED",
                "ontology_version": "8.4.0",
                "term_id": term_id,
                "label": term_id,
            }
            for term_id in ontology_ids
        ],
        "facets": facets,
        "mapping": {
            "method": "curated-study-HED-definition",
            "mapping_version": "harmo-public-semantics/1.1.0",
            "status": "mapped",
            "evidence_refs": mapping_evidence,
            "confidence": 1.0,
        },
        "compatibility_group": compatibility,
        "_hed_value": str(hed_terms[tag]),
        "_study_id": study_id,
    }


def _channel_metadata(
    observed: Sequence[str],
    definitions: Mapping[str, Any],
    mapping: Mapping[str, Any],
    channel_path: Path,
    verified_crosswalk: VerifiedAnatomyCrosswalk,
    repository_root: Optional[Path] = None,
) -> Tuple[Dict[str, Dict[str, Any]], List[Dict[str, str]]]:
    by_source_term = {
        entry.source_term.term_id: entry
        for entry in verified_crosswalk.crosswalk.entries
        if entry.predicate == "exactMatch"
    }
    result: Dict[str, Dict[str, Any]] = {}
    excluded: List[Dict[str, str]] = []
    context = mapping.get("static_metadata") or {}
    for source_label in sorted(observed):
        definition = _resolve_channel_definition(
            definitions.get(source_label), context
        )
        if definition is None:
            excluded.append(
                {
                    "source_label": source_label,
                    "reason": "source channel lacks an unambiguous anatomy definition",
                }
            )
            continue
        raw_muscle = str(definition.get("muscle", ""))
        if "ignore_channel" in raw_muscle.lower():
            excluded.append(
                {
                    "source_label": source_label,
                    "reason": "source definition explicitly declares ignore_channel",
                }
            )
            continue
        try:
            muscle = _canonical_muscle(raw_muscle)
        except ValueError:
            excluded.append(
                {
                    "source_label": source_label,
                    "reason": "source definition has no canonical muscle",
                }
            )
            continue
        entry = by_source_term.get("HARMOMUSCLE:{}".format(muscle))
        if entry is None:
            excluded.append(
                {
                    "source_label": source_label,
                    "reason": "canonical muscle lacks a verified exact crosswalk",
                }
            )
            continue
        laterality = _side(definition.get("side"))
        if laterality == "unknown":
            excluded.append(
                {
                    "source_label": source_label,
                    "reason": "source definition has unresolved laterality",
                }
            )
            continue
        canonical = muscle.replace("_", "-")
        result[source_label] = {
            "muscle": canonical,
            "side": laterality,
            "sensor_type": "surface-emg",
            "source_name": source_label,
            "anatomy": {
                "entity_type": "muscle",
                "canonical_label": canonical,
                "ontology_term": entry.target_term.to_dict(),
                "laterality": laterality,
                "source_label": source_label,
                "mapping": {
                    "method": "curated-study-definition+verified-crosswalk",
                    "mapping_version": "harmo-anatomy/1.0.0",
                    "status": "mapped",
                    "evidence_refs": [
                        _semantic_evidence_ref(
                            channel_path, source_label, repository_root
                        ),
                        verified_crosswalk.evidence_ref,
                    ],
                    "confidence": 1.0,
                },
            },
        }
    return result, excluded


def _pair_matches(
    pairs: Sequence[Tuple[str, str]],
    mapping: Mapping[str, Any],
) -> List[Dict[str, Any]]:
    regex = mapping_regex(mapping)
    matches = []
    for logical, raw in pairs:
        match = regex.match(logical)
        if match:
            matches.append(
                {"logical": logical, "raw": raw, "groups": match.groupdict()}
            )
    if len(matches) < 2:
        raise ValueError(
            "{} requires at least two source members".format(mapping.get("name"))
        )
    first = matches[0]
    semantic_keys = set(first["groups"]) - _ALLOWED_ROLE_DIFFERENCES
    compatible = [
        item
        for item in matches[1:]
        if all(
            item["groups"].get(key) == first["groups"].get(key)
            for key in semantic_keys
        )
    ]
    if not compatible:
        raise ValueError(
            "{} has no second member with the same task facets".format(
                mapping.get("name")
            )
        )
    return [first, compatible[0]]


def _parse_member(
    *,
    path: Path,
    selected_key: str,
    selected: Mapping[str, Any],
    study_id: str,
    mapping_id: str,
    mapping: Mapping[str, Any],
    config: Mapping[str, Any],
    channel_definitions: Mapping[str, Any],
    max_timepoints: int,
) -> Tuple[pd.DataFrame, Dict[str, Any], str]:
    session_template = str(mapping.get("session_id_template", ""))
    groups = selected["groups"]
    session_id = (
        session_template.format(**groups)
        if session_template
        else "ses-{}".format(sha256_file(str(path))[:12])
    )
    row = {
        "study_id": study_id,
        "mapping_id": mapping_id,
        "mapping_name": str(mapping.get("name", mapping_id)),
        "input_path_template": mapping.get("input_path_template"),
        "file_format": mapping.get("file_format"),
        "reader_plugin": mapping.get("reader_plugin"),
        "asset_id": selected_key.split("::", 1)[0],
        "matched_files": 1,
        "selected_member": selected_key,
        "source_archive_member": selected["raw"],
        "logical_source_member": selected["logical"],
        "subject_id": groups.get("sub_id"),
        "session_id": session_id,
        "path_metadata": groups,
        "source_checksum_pass": True,
        "outcome": "discovered",
        "error": None,
    }
    frame, ledger = _parse_once(
        [row],
        config,
        {selected_key: path},
        channel_definitions,
        max_timepoints,
    )
    item = ledger[0]
    if item.get("parse_outcome") != "converted":
        raise ValueError(
            "{} parse failed: {}".format(mapping_id, item.get("error"))
        )
    return frame, item, normalized_records_digest(frame)


def _wide_mapped_frame(
    normalized: pd.DataFrame, metadata: Mapping[str, Any]
) -> pd.DataFrame:
    selected = normalized[normalized["channel"].isin(metadata)].copy()
    if selected.empty:
        raise ValueError("no source channels have verified anatomy")
    duplicates = selected.duplicated(["time", "channel"])
    if bool(duplicates.any()):
        raise ValueError("source sample has duplicate time/channel observations")
    wide = selected.pivot(index="time", columns="channel", values="value")
    wide = wide.sort_index().reset_index()
    wide.columns.name = None
    if wide.isna().any().any():
        raise ValueError("qualified channel subset has missing values")
    return wide


def _recording_profile(bundle: HarmoRecordingBundle) -> RecordingProfile:
    channels = bundle.channels
    return RecordingProfile(
        sampling_rate_min_hz=bundle.timebase.sampling_rate_hz,
        sampling_rate_max_hz=bundle.timebase.sampling_rate_hz,
        units_by_sensor_type={"surface-emg": ["V"]},
        required_channel_names=[channel.name for channel in channels],
        require_array_checksum=True,
        expected_sampling_rate_hz=bundle.timebase.sampling_rate_hz,
        expected_start_seconds=bundle.timebase.start_seconds,
        expected_sides_by_channel={
            channel.channel_id: str(channel.side) for channel in channels
        },
        expected_muscles_by_channel={
            channel.channel_id: str(channel.muscle) for channel in channels
        },
    )


def _resolve_retained_timestamps(
    bundle: HarmoRecordingBundle,
    timestamps_path: Path,
) -> None:
    """Resolve an irregular-grid warning only after explicit retention checks."""
    frame = pd.read_csv(timestamps_path)
    if list(frame.columns) != ["time_seconds"]:
        raise ValueError("explicit timestamps use an unexpected schema")
    values = pd.to_numeric(frame["time_seconds"], errors="coerce")
    if (
        len(values) != bundle.timebase.sample_count
        or bool(values.isna().any())
        or not bool((values.diff().dropna() > 0).all())
        or not bundle.timebase.timestamps_uri
    ):
        raise ValueError("explicit timestamp retention verification failed")
    for warning in bundle.warnings:
        if warning.code == "irregular-timebase":
            warning.severity = "info"
            warning.message = (
                "irregular source-spanning sample grid is explicitly retained"
            )
            warning.evidence = "sha256:{}".format(
                sha256_file(str(timestamps_path))
            )


def _build_manifest(
    *,
    root: Path,
    output: Path,
    archive_name: str,
    archive_url: str,
    archive_sha256: str,
    archive_size: int,
    archive_verification_mode: str,
    source_inventory_path: Path,
    source_releases_path: Path,
    member_path: Path,
    member_uri: str,
    member_crc32: str,
    config_path: Path,
    channel_path: Path,
    hed_path: Path,
    crosswalk_path: Path,
    ontology_path: Path,
    samples_path: Path,
    timestamps_path: Path,
    bundle: HarmoRecordingBundle,
    trust: TrustReport,
    repository_state: Mapping[str, Any],
    excluded_channels: Sequence[Mapping[str, str]],
    max_timepoints: int,
    semantic_overlay_inputs: Sequence[ArtifactReference] = (),
) -> EvidenceManifest:
    inputs = [
        ArtifactReference(
            "source-archive",
            archive_url,
            archive_sha256,
            "application/zip",
        ),
        ArtifactReference(
            "source-member",
            member_uri,
            sha256_file(str(member_path)),
            "application/octet-stream",
        ),
        ArtifactReference(
            "full-archive-verification",
            str(source_inventory_path.relative_to(root)),
            sha256_file(str(source_inventory_path)),
            "application/json",
        ),
        ArtifactReference(
            "source-release-rights",
            str(source_releases_path.relative_to(root)),
            sha256_file(str(source_releases_path)),
            "application/yaml",
        ),
        ArtifactReference(
            "study-config",
            str(config_path.relative_to(root)),
            sha256_file(str(config_path)),
            "application/yaml",
        ),
        ArtifactReference(
            "channel-definitions",
            str(channel_path.relative_to(root)),
            sha256_file(str(channel_path)),
            "application/yaml",
        ),
        ArtifactReference(
            "task-definitions",
            str(hed_path.relative_to(root)),
            sha256_file(str(hed_path)),
            "application/json",
        ),
        ArtifactReference(
            "anatomy-crosswalk",
            str(crosswalk_path.relative_to(root)),
            sha256_file(str(crosswalk_path)),
            "application/yaml",
        ),
        ArtifactReference(
            "ontology-release",
            "urn:sha256:{}".format(sha256_file(str(ontology_path))),
            sha256_file(str(ontology_path)),
            "text/obo",
        ),
    ] + list(semantic_overlay_inputs)
    overlay_ids = [
        artifact.artifact_id for artifact in semantic_overlay_inputs
    ]
    outputs = [
        ArtifactReference(
            "normalized-samples",
            str(samples_path.relative_to(output)),
            sha256_file(str(samples_path)),
            "text/csv",
        ),
        ArtifactReference(
            "normalized-timestamps",
            str(timestamps_path.relative_to(output)),
            sha256_file(str(timestamps_path)),
            "text/csv",
        ),
        ArtifactReference(
            "recording-bundle",
            "recording.bundle.json",
            sha256_contract(bundle),
            "application/json",
            "sha256-canonical-json",
        ),
        ArtifactReference(
            "trust-report",
            "trust.report.json",
            sha256_contract(trust),
            "application/json",
            "sha256-canonical-json",
        ),
    ]
    return EvidenceManifest(
        run_id="atlas-materialize-{}".format(sha256_contract(bundle)[:20]),
        inputs=inputs,
        outputs=outputs,
        steps=[
            TransformationStep(
                "extract",
                "verify archive and extract checksum-bound source member",
                "harmo",
                __version__,
                parameters={
                    "archive_size_bytes": archive_size,
                    "archive_name": archive_name,
                    "archive_verification_mode": archive_verification_mode,
                    "member_crc32": member_crc32,
                },
                inputs=[
                    "source-archive",
                    "full-archive-verification",
                    "source-release-rights",
                    "source-member",
                ],
                outputs=[],
            ),
            TransformationStep(
                "normalize",
                "parse source and retain a deterministic source-spanning sample",
                "harmo",
                __version__,
                parameters={
                    "max_timepoints": max_timepoints,
                    "value_transform": "identity",
                },
                inputs=["source-member", "study-config"],
                outputs=["normalized-samples", "normalized-timestamps"],
            ),
            TransformationStep(
                "describe",
                "map verified task and anatomy semantics",
                "harmo",
                __version__,
                parameters={
                    "excluded_source_channels": list(excluded_channels),
                    "scope": "verified-anatomy-channel-subset",
                    "semantic_overlay_policy": (
                        "provenance-only-no-recording-mutation"
                    ),
                    "semantic_overlay_ids": overlay_ids,
                },
                inputs=[
                    "normalized-samples",
                    "normalized-timestamps",
                    "channel-definitions",
                    "task-definitions",
                    "anatomy-crosswalk",
                    "ontology-release",
                ] + overlay_ids,
                outputs=["recording-bundle"],
            ),
            TransformationStep(
                "assess",
                "evaluate bounded recording trust",
                "harmo",
                __version__,
                inputs=["recording-bundle", "study-config"],
                outputs=["trust-report"],
            ),
        ],
        code_commit=str(repository_state.get("commit", "unknown")),
        config_sha256=sha256_file(str(config_path)),
        code_dirty=repository_state.get("dirty"),
        working_tree_sha256=repository_state.get("working_tree_sha256"),
        environment=runtime_environment(),
    )


def _build_record(
    *,
    root: Path,
    output_root: Path,
    mapping_id: str,
    role: str,
    selected: Mapping[str, Any],
    normalized: pd.DataFrame,
    parse_ledger: Mapping[str, Any],
    normalized_digest: str,
    member_path: Path,
    mapping: Mapping[str, Any],
    config_path: Path,
    channel_path: Path,
    hed_path: Path,
    definitions: Mapping[str, Any],
    verified_crosswalk: VerifiedAnatomyCrosswalk,
    crosswalk_path: Path,
    ontology_path: Path,
    archive_asset: Mapping[str, Any],
    archive_sha256: str,
    archive_size_bytes: int,
    archive_verification_mode: str,
    source_inventory_path: Path,
    source_releases_path: Path,
    source_release_terms: Mapping[str, str],
    member_crc32: str,
    repository_state: Mapping[str, Any],
    max_timepoints: int,
    semantic_overlay_paths: Sequence[Path] = (),
) -> Dict[str, Any]:
    observed = sorted(str(value) for value in normalized["channel"].unique())
    channel_metadata, excluded = _channel_metadata(
        observed,
        definitions,
        mapping,
        channel_path,
        verified_crosswalk,
        root,
    )
    wide = _wide_mapped_frame(normalized, channel_metadata)
    task_metadata = _task_metadata(
        str(mapping_id.split("-", 1)[0]),
        mapping,
        selected["groups"],
        config_path,
        hed_path,
        root,
    )
    task_metadata.pop("_hed_value")
    task_metadata.pop("_study_id")
    subject_id = str(selected["groups"].get("sub_id") or "unknown")
    session_template = str(mapping.get("session_id_template", ""))
    session_id = (
        session_template.format(**selected["groups"])
        if session_template
        else "ses-{}".format(role)
    )
    member_sha = sha256_file(str(member_path))
    recording_id = "{}/{}/{}/{}/{}".format(
        mapping_id.split("-", 1)[0],
        mapping_id,
        subject_id,
        session_id,
        member_sha[:12],
    )
    record_dir = output_root / "records" / mapping_id / role
    record_dir.mkdir(parents=True, exist_ok=True)
    samples_path = record_dir / "normalized.samples.csv"
    timestamps_path = record_dir / "normalized.timestamps.csv"
    _write_frame_atomic(samples_path, wide.drop(columns=["time"]))
    _write_frame_atomic(
        timestamps_path,
        pd.DataFrame({"time_seconds": wide["time"]}),
    )
    bundle, _ = bundle_from_wide_dataframe(
        wide,
        recording_id=recording_id,
        study_id=mapping_id.split("-", 1)[0],
        subject_id=subject_id,
        session_id=session_id,
        source_path=str(member_path),
        array_uri=samples_path.as_uri(),
        channel_units={name: "V" for name in wide.columns if name != "time"},
        time_column="time",
        sensor_type="surface-emg",
        channel_metadata=channel_metadata,
        task_metadata=task_metadata,
        array_format="csv",
        array_dtype="float64",
        explicit_timestamps_uri=timestamps_path.as_uri(),
    )
    bundle.samples.uri = "urn:harmo:artifact:{}/normalized-samples".format(
        recording_id
    )
    bundle.timebase.timestamps_uri = (
        "urn:harmo:artifact:{}/normalized-timestamps".format(recording_id)
    )
    _resolve_retained_timestamps(bundle, timestamps_path)
    member_uri = "zip+{}#{}".format(
        archive_asset["download_url"], selected["raw"]
    )
    bundle.sources = [
        bundle.sources[0].__class__(
            uri=str(archive_asset["download_url"]),
            sha256=archive_sha256,
            size_bytes=archive_size_bytes,
            media_type="application/zip",
            dataset_release=str(archive_asset["source_record_doi"]),
        ),
        bundle.sources[0].__class__(
            uri=member_uri,
            sha256=member_sha,
            size_bytes=member_path.stat().st_size,
            media_type="application/octet-stream",
            dataset_release=str(archive_asset["source_record_doi"]),
        ),
    ]
    overlay_inputs, bound_overlays = _semantic_overlay_inputs(
        bundle, semantic_overlay_paths, root
    )
    trust = _augment_workflow_trust(
        assess_bundle(bundle, _recording_profile(bundle)),
        bundle,
        dict(repository_state),
    )
    trust.evidence.extend(
        [
            TrustEvidence(
                "semantic_fidelity",
                TrustStatus.PASS,
                "timebase.explicit-timestamps.retained",
                (
                    "the deterministic source-spanning sample retains every "
                    "selected timestamp explicitly"
                ),
                artifact_id="normalized-timestamps",
            ),
            TrustEvidence(
                "traceability",
                TrustStatus.PASS,
                "source.archive.full-checksum",
                (
                    "complete public archive size and checksum are bound to "
                    "the frozen source inventory"
                ),
                artifact_id="source-archive",
            ),
            TrustEvidence(
                "traceability",
                TrustStatus.PASS,
                "source.rights.redistribution",
                (
                    "source release registry declares {} under {}".format(
                        source_release_terms["redistribution"],
                        source_release_terms["license"],
                    )
                ),
                artifact_id="source-release-rights",
            ),
            TrustEvidence(
                "traceability",
                TrustStatus.PASS,
                "source.member.checksum",
                (
                    "source member passed ZIP CRC and has an independently "
                    "recorded SHA-256"
                ),
                artifact_id="source-member",
            ),
            TrustEvidence(
                "scientific_validity",
                TrustStatus.PASS,
                "source.sample.finite",
                "bounded source sample has finite timestamps and signal values",
            ),
        ]
    )
    trust.evidence.extend(
        TrustEvidence(
            "traceability",
            TrustStatus.PASS,
            "literature.semantic-overlay.bound",
            (
                "{} accepted semantic claim(s) are checksum-bound as a "
                "provenance-only overlay; recording values and trust "
                "dimensions were not promoted"
            ).format(len(overlay.entries)),
            artifact_id=overlay.overlay_id,
        )
        for overlay in bound_overlays
    )
    manifest = _build_manifest(
        root=root,
        output=record_dir,
        archive_name=str(archive_asset["local_name"]),
        archive_url=str(archive_asset["download_url"]),
        archive_sha256=archive_sha256,
        archive_size=archive_size_bytes,
        archive_verification_mode=archive_verification_mode,
        source_inventory_path=source_inventory_path,
        source_releases_path=source_releases_path,
        member_path=member_path,
        member_uri=member_uri,
        member_crc32=member_crc32,
        config_path=config_path,
        channel_path=channel_path,
        hed_path=hed_path,
        crosswalk_path=crosswalk_path,
        ontology_path=ontology_path,
        samples_path=samples_path,
        timestamps_path=timestamps_path,
        bundle=bundle,
        trust=trust,
        repository_state=repository_state,
        excluded_channels=excluded,
        max_timepoints=max_timepoints,
        semantic_overlay_inputs=overlay_inputs,
    )
    paths = {
        "recording": write_json_atomic(
            str(record_dir / "recording.bundle.json"), bundle
        ),
        "manifest": write_json_atomic(
            str(record_dir / "evidence.manifest.json"), manifest
        ),
        "trust": write_json_atomic(
            str(record_dir / "trust.report.json"), trust
        ),
    }
    return {
        "role": role,
        "recording_id": recording_id,
        "bundle": bundle,
        "manifest": manifest,
        "trust": trust,
        "paths": paths,
        "record_dir": record_dir,
        "source_member": selected["raw"],
        "source_member_sha256": member_sha,
        "source_member_size_bytes": member_path.stat().st_size,
        "source_member_crc32": member_crc32,
        "normalized_record_sha256": normalized_digest,
        "sample_sha256": sha256_file(str(samples_path)),
        "sample_count": bundle.timebase.sample_count,
        "source_channel_count": len(observed),
        "qualified_channel_count": len(bundle.channels),
        "excluded_source_channels": excluded,
        "parse_summary": {
            key: parse_ledger.get(key)
            for key in (
                "parser",
                "parser_version",
                "source_rows",
                "sample_rows",
                "sample_timepoints",
                "sample_channels",
                "time_provenance",
                "time_axis_semantics",
                "time_unit",
                "sampling_rate_hz",
            )
        },
    }


def _comparison_channel(bundle: HarmoRecordingBundle) -> str:
    preferred = [
        channel.channel_id
        for channel in bundle.channels
        if channel.muscle == "tibialis-anterior"
        and channel.side == "right"
    ]
    if preferred:
        return preferred[0]
    return bundle.channels[0].channel_id


def _relative(path: str, output_root: Path) -> str:
    return str(Path(path).resolve().relative_to(output_root.resolve()))


def _record_public_row(record: Mapping[str, Any], output_root: Path) -> Dict[str, Any]:
    bundle = record["bundle"]
    trust = record["trust"]
    manifest = record["manifest"]
    return {
        "role": record["role"],
        "recording_id": record["recording_id"],
        "recording_sha256": sha256_contract(bundle),
        "manifest_sha256": sha256_contract(manifest),
        "trust_sha256": sha256_contract(trust),
        "recording_path": _relative(record["paths"]["recording"], output_root),
        "manifest_path": _relative(record["paths"]["manifest"], output_root),
        "trust_path": _relative(record["paths"]["trust"], output_root),
        "source_member": record["source_member"],
        "source_member_sha256": record["source_member_sha256"],
        "source_member_size_bytes": record["source_member_size_bytes"],
        "source_member_crc32": record["source_member_crc32"],
        "normalized_record_sha256": record["normalized_record_sha256"],
        "sample_sha256": record["sample_sha256"],
        "sample_count": record["sample_count"],
        "source_channel_count": record["source_channel_count"],
        "qualified_channel_count": record["qualified_channel_count"],
        "excluded_source_channels": record["excluded_source_channels"],
        "trust_dimensions": {
            dimension: trust.dimension_status(dimension).value
            for dimension in (
                "semantic_fidelity",
                "scientific_validity",
                "computational_reproducibility",
                "traceability",
            )
        },
        "parse_summary": record["parse_summary"],
    }


def _materialize_tables(
    index: Mapping[str, Any],
    output_root: Path,
) -> Tuple[Dict[str, pd.DataFrame], Dict[str, Any]]:
    record_rows = []
    channel_rows = []
    task_rows = []
    comparison_rows = []
    release_rows = []
    for mapping in index["mappings"]:
        comparison = mapping["comparison"]
        comparison_rows.append(
            {
                "mapping_id": mapping["mapping_id"],
                "left_id": comparison["left_id"],
                "right_id": comparison["right_id"],
                "variable": comparison["variable"],
                "intended_use": comparison["intended_use"],
                "status": comparison["status"],
                "rationale": comparison["rationale"],
                "conditions_json": json.dumps(
                    comparison["conditions"], sort_keys=True, separators=(",", ":")
                ),
                "assertion_sha256": mapping["comparison_sha256"],
            }
        )
        releases = {
            item["subject_id"]: item for item in mapping["release_decisions"]
        }
        for record in mapping["records"]:
            bundle_path = output_root / record["recording_path"]
            bundle = HarmoRecordingBundle.from_dict(
                json.loads(bundle_path.read_text(encoding="utf-8"))
            )
            release = releases[bundle.recording_id]
            record_rows.append(
                {
                    "mapping_id": mapping["mapping_id"],
                    "recording_id": bundle.recording_id,
                    "role": record["role"],
                    "study_id": bundle.study_id,
                    "subject_id": bundle.subject_id,
                    "session_id": bundle.session_id,
                    "sample_count": bundle.timebase.sample_count,
                    "sampling_rate_hz": bundle.timebase.sampling_rate_hz,
                    "qualified_channel_count": record["qualified_channel_count"],
                    "source_channel_count": record["source_channel_count"],
                    "recording_sha256": record["recording_sha256"],
                    "manifest_sha256": record["manifest_sha256"],
                    "trust_sha256": record["trust_sha256"],
                    "release_status": release["status"],
                    "source_member": record["source_member"],
                    "source_member_sha256": record["source_member_sha256"],
                }
            )
            task_rows.append(
                {
                    "recording_id": bundle.recording_id,
                    "mapping_id": mapping["mapping_id"],
                    "activity": bundle.task.activity,
                    "source_label": bundle.task.source_label,
                    "compatibility_group": bundle.task.compatibility_group,
                    "facets_json": json.dumps(
                        bundle.task.facets, sort_keys=True, separators=(",", ":")
                    ),
                    "ontology_terms_json": json.dumps(
                        [term.to_dict() for term in bundle.task.ontology_terms],
                        sort_keys=True,
                        separators=(",", ":"),
                    ),
                }
            )
            for channel in bundle.channels:
                anatomy = channel.anatomy
                channel_rows.append(
                    {
                        "recording_id": bundle.recording_id,
                        "mapping_id": mapping["mapping_id"],
                        "channel_id": channel.channel_id,
                        "source_name": channel.source_name,
                        "unit": channel.unit,
                        "sensor_type": channel.sensor_type,
                        "muscle": channel.muscle,
                        "laterality": anatomy.laterality,
                        "ontology": anatomy.ontology_term.ontology,
                        "ontology_version": anatomy.ontology_term.ontology_version,
                        "term_id": anatomy.ontology_term.term_id,
                        "term_label": anatomy.ontology_term.label,
                    }
                )
        for release in mapping["release_decisions"]:
            release_rows.append(
                {
                    "mapping_id": mapping["mapping_id"],
                    "recording_id": release["subject_id"],
                    "decision_id": release["decision_id"],
                    "status": release["status"],
                    "intended_use": release["intended_use"],
                    "conditions_json": json.dumps(
                        release["conditions"], sort_keys=True, separators=(",", ":")
                    ),
                    "recording_sha256": release["recording_sha256"],
                    "manifest_sha256": release["manifest_sha256"],
                    "trust_report_sha256": release["trust_report_sha256"],
                    "comparability_assertion_sha256": release[
                        "comparability_assertion_sha256"
                    ],
                }
            )
    frames = {
        ATLAS_RECORD: pd.DataFrame(record_rows),
        ATLAS_CHANNEL: pd.DataFrame(channel_rows),
        ATLAS_TASK: pd.DataFrame(task_rows),
        ATLAS_COMPARABILITY: pd.DataFrame(comparison_rows),
        ATLAS_RELEASE: pd.DataFrame(release_rows),
    }
    table_dir = output_root / "tables"
    table_dir.mkdir(parents=True, exist_ok=True)
    table_rows = {}
    for table_name, frame in frames.items():
        short = table_name.rsplit(".", 1)[-1]
        parquet_path = table_dir / "{}.parquet".format(short)
        frame.to_parquet(parquet_path, index=False)
        table_rows[table_name] = {
            "path": str(parquet_path.relative_to(output_root)),
            "sha256": sha256_file(str(parquet_path)),
            "rows": len(frame),
        }
    import duckdb

    record_path = str(table_dir / "atlas_record.parquet")
    channel_path = str(table_dir / "atlas_channel.parquet")
    queries = {
        "record_count": "SELECT count(*) AS n FROM read_parquet(?)",
        "mapping_count": "SELECT count(DISTINCT mapping_id) AS n FROM read_parquet(?)",
        "conditional_release_count": (
            "SELECT count(*) AS n FROM read_parquet(?) "
            "WHERE release_status = 'conditional'"
        ),
    }
    connection = duckdb.connect()
    try:
        query_results = {
            name: int(connection.execute(sql, [record_path]).fetchone()[0])
            for name, sql in queries.items()
        }
        query_results["uberon_term_count"] = int(
            connection.execute(
                "SELECT count(DISTINCT term_id) FROM read_parquet(?)",
                [channel_path],
            ).fetchone()[0]
        )
    finally:
        connection.close()
    query_results["pass"] = bool(
        query_results["record_count"] == 12
        and query_results["mapping_count"] == 6
        and query_results["conditional_release_count"] == 12
        and query_results["uberon_term_count"] > 0
    )
    return frames, {"tables": table_rows, "duckdb_queries": query_results}


def persist_atlas_frames(frames: Mapping[str, pd.DataFrame], spark=None) -> None:
    from harmo.ingest.warehouse import ingest_definition_table

    created = spark is None
    if created:
        from harmo.warehouse.connection import get_spark_session

        spark = get_spark_session(app_name="harmo-atlas-materialization")
    try:
        if spark is None:
            raise RuntimeError("Spark session is unavailable")
        spark.sql("CREATE NAMESPACE IF NOT EXISTS local.warehouse")
        for table_name, frame in frames.items():
            ingest_definition_table(spark, frame, table_name)
    finally:
        if created and spark is not None:
            spark.stop()


def materialize_ready_emg_mappings(
    *,
    repository_root: str,
    data_root: str,
    work_root: str,
    output_root: str,
    corpus_path: str,
    coverage_path: str,
    mapping_outcomes_path: str,
    source_inventory_path: str,
    source_releases_path: str,
    crosswalk_path: str,
    ontology_path: str,
    mapping_ids: Sequence[str] = DEFAULT_READY_MAPPING_IDS,
    max_timepoints: int = 100,
    download_missing: bool = False,
    range_fetch_missing: bool = False,
    persist_iceberg: bool = False,
    semantic_overlay_paths: Sequence[str] = (),
) -> Dict[str, Any]:
    """Create bounded real-data R/M/T/C/D products for ready EMG mappings."""
    root = Path(repository_root).resolve()
    data_dir = Path(data_root).resolve()
    work_dir = Path(work_root).resolve()
    output_dir = Path(output_root).resolve()
    corpus_file = Path(corpus_path).resolve()
    coverage_file = Path(coverage_path).resolve()
    outcomes_file = Path(mapping_outcomes_path).resolve()
    source_inventory_file = Path(source_inventory_path).resolve()
    source_releases_file = Path(source_releases_path).resolve()
    crosswalk_file = Path(crosswalk_path).resolve()
    ontology_file = Path(ontology_path).resolve()
    overlay_files = [Path(value).resolve() for value in semantic_overlay_paths]
    for overlay_file in overlay_files:
        if not overlay_file.is_file():
            raise ValueError(
                "semantic overlay is missing: {}".format(overlay_file)
            )
    if max_timepoints != 100:
        raise ValueError(
            "max_timepoints must remain 100 to reproduce the frozen sample digests"
        )
    if download_missing and range_fetch_missing:
        raise ValueError(
            "download_missing and range_fetch_missing are mutually exclusive"
        )
    requested = tuple(mapping_ids)
    if len(requested) != len(set(requested)):
        raise ValueError("mapping IDs must be unique")
    coverage = json.loads(coverage_file.read_text(encoding="utf-8"))
    ready = {
        row["mapping_id"]
        for row in coverage["mapping_rows"]
        if row["emg_semantics_apply"]
        and row["config_ready_for_recording_materialization"]
    }
    if set(requested) != ready:
        raise ValueError(
            "requested mappings must exactly equal audited ready EMG mappings: {}".format(
                sorted(ready)
            )
        )
    outcomes = {
        row["mapping_id"]: row
        for row in json.loads(outcomes_file.read_text(encoding="utf-8"))
    }
    source_inventory = json.loads(
        source_inventory_file.read_text(encoding="utf-8")
    )
    if not isinstance(source_inventory, list):
        raise ValueError("source inventory must contain an array")
    source_releases = _load_yaml(source_releases_file)
    corpus = _load_yaml(corpus_file)
    verified_crosswalk = verify_anatomy_crosswalk(
        str(crosswalk_file), str(ontology_file)
    )
    repository_state = current_repository_state()
    if repository_state.get("commit") in (None, "unknown"):
        raise ValueError("materialization requires a known repository commit")
    data_dir.mkdir(parents=True, exist_ok=True)
    work_dir.mkdir(parents=True, exist_ok=True)
    output_dir.mkdir(parents=True, exist_ok=True)

    source_assets: Dict[Tuple[str, str], Dict[str, Any]] = {}
    contexts: Dict[str, Dict[str, Any]] = {}
    for mapping_id in requested:
        study_id = mapping_id.rsplit("-", 1)[0]
        study = corpus["studies"][study_id]
        config_path = root / str(study["config"])
        config = _load_yaml(config_path)
        mappings = _mapping_table(config, study_id)
        mapping = mappings[mapping_id]
        asset_id = str(outcomes[mapping_id]["asset_id"])
        asset = study["assets"][asset_id]
        frozen_source = _frozen_source_entry(
            source_inventory, study_id, asset_id, asset
        )
        release_terms = _source_release_terms(source_releases, study_id)
        archive_path = data_dir / str(asset["local_name"])
        if archive_path.is_file():
            verification = verify_corpus_asset(archive_path, asset)
            if not verification.get("pass"):
                raise ValueError(
                    "source archive verification failed: {}".format(archive_path)
                )
            if verification["sha256"] != frozen_source["sha256"]:
                raise ValueError(
                    "current archive differs from frozen source inventory"
                )
            verification_mode = "current-complete-archive"
        elif download_missing:
            _download_asset(str(asset["download_url"]), archive_path, asset)
            verification = verify_corpus_asset(archive_path, asset)
            if not verification.get("pass"):
                raise ValueError(
                    "source archive verification failed: {}".format(archive_path)
                )
            if verification["sha256"] != frozen_source["sha256"]:
                raise ValueError(
                    "downloaded archive differs from frozen source inventory"
                )
            verification_mode = "current-complete-archive"
        elif range_fetch_missing:
            archive_path = None
            verification = {
                "pass": True,
                "size_bytes": frozen_source["size_bytes"],
                "sha256": frozen_source["sha256"],
            }
            verification_mode = (
                "frozen-complete-archive+current-member-crc-sha256"
            )
        else:
            raise ValueError("source archive is missing: {}".format(archive_path))
        source_assets[(study_id, asset_id)] = {
            "study_id": study_id,
            "asset_id": asset_id,
            "path": archive_path,
            "asset": asset,
            "verification": verification,
            "verification_mode": verification_mode,
            "frozen_source_inventory_sha256": sha256_file(
                str(source_inventory_file)
            ),
            "source_release_registry_sha256": sha256_file(
                str(source_releases_file)
            ),
            "release_terms": release_terms,
        }
        channel_definition = config["definitions"]["channels"]
        channel_path = config_path.parent / str(channel_definition["path"])
        hed_definition = config["definitions"].get("hed_json") or {}
        hed_path = config_path.parent / str(hed_definition["path"])
        definitions, _ = load_channel_definitions(config_path, config)
        contexts[mapping_id] = {
            "study_id": study_id,
            "config": config,
            "config_path": config_path,
            "mapping": mapping,
            "asset_id": asset_id,
            "asset": asset,
            "archive_path": archive_path,
            "archive_sha256": verification["sha256"],
            "archive_size_bytes": verification["size_bytes"],
            "archive_verification_mode": verification_mode,
            "release_terms": release_terms,
            "channel_path": channel_path,
            "hed_path": hed_path,
            "definitions": definitions,
        }

    mapping_results = []
    for mapping_id in requested:
        context = contexts[mapping_id]
        archive_path = context["archive_path"]
        asset = context["asset"]
        mapping = context["mapping"]
        if archive_path is None:
            pairs = _remote_zip_pairs(str(asset["download_url"]), asset)
        else:
            with zipfile.ZipFile(archive_path) as archive:
                pairs = _logical_zip_members(archive, asset)
        selected_pair = _pair_matches(pairs, mapping)
        extracted = []
        for role, selected in zip(("reference", "candidate"), selected_pair):
            if archive_path is None:
                path, member_crc32 = _extract_remote_member(
                    str(asset["download_url"]),
                    selected["raw"],
                    work_dir / mapping_id / role,
                )
            else:
                with zipfile.ZipFile(archive_path) as archive:
                    expected_crc32 = "{:08x}".format(
                        archive.getinfo(selected["raw"]).CRC
                    )
                path = _extract_member(
                    archive_path,
                    selected["raw"],
                    work_dir / mapping_id / role,
                )
                member_crc32 = _crc32_file(path)
                if member_crc32 != expected_crc32:
                    raise ValueError("local ZIP member CRC verification failed")
            selected_key = "{}::{}".format(context["asset_id"], selected["raw"])
            normalized, ledger, digest = _parse_member(
                path=path,
                selected_key=selected_key,
                selected=selected,
                study_id=context["study_id"],
                mapping_id=mapping_id,
                mapping=mapping,
                config=context["config"],
                channel_definitions=context["definitions"],
                max_timepoints=max_timepoints,
            )
            extracted.append(
                _build_record(
                    root=root,
                    output_root=output_dir,
                    mapping_id=mapping_id,
                    role=role,
                    selected=selected,
                    normalized=normalized,
                    parse_ledger=ledger,
                    normalized_digest=digest,
                    member_path=path,
                    mapping=mapping,
                    config_path=context["config_path"],
                    channel_path=context["channel_path"],
                    hed_path=context["hed_path"],
                    definitions=context["definitions"],
                    verified_crosswalk=verified_crosswalk,
                    crosswalk_path=crosswalk_file,
                    ontology_path=ontology_file,
                    archive_asset=asset,
                    archive_sha256=context["archive_sha256"],
                    archive_size_bytes=context["archive_size_bytes"],
                    archive_verification_mode=context[
                        "archive_verification_mode"
                    ],
                    source_inventory_path=source_inventory_file,
                    source_releases_path=source_releases_file,
                    source_release_terms=context["release_terms"],
                    member_crc32=member_crc32,
                    repository_state=repository_state,
                    max_timepoints=max_timepoints,
                    semantic_overlay_paths=overlay_files,
                )
            )
        reference, candidate = extracted
        expected = outcomes[mapping_id]["normalized_sample_sha256"]
        frozen_first_digest_pass = reference["normalized_record_sha256"] == expected
        if not frozen_first_digest_pass:
            raise ValueError(
                "{} frozen first-member digest mismatch: {} != {}".format(
                    mapping_id,
                    reference["normalized_record_sha256"],
                    expected,
                )
            )
        left_channel = _comparison_channel(reference["bundle"])
        right_channel = _comparison_channel(candidate["bundle"])
        assertion = ComparabilityPolicyEngine().evaluate(
            reference["bundle"],
            candidate["bundle"],
            reference["trust"],
            candidate["trust"],
            ComparabilityRequest(
                variable="tibialis-anterior.activation-timing",
                intended_use="activation-timing",
                left_channel_id=left_channel,
                right_channel_id=right_channel,
            ),
        )
        bounded_condition = (
            "use only as a bounded source-spanning 100-timepoint demonstration; "
            "materialize the full recording before scientific inference"
        )
        if bounded_condition not in assertion.conditions:
            assertion.conditions.append(bounded_condition)
        if assertion.status == Comparability.COMPARABLE:
            assertion.status = Comparability.CONDITIONAL
            assertion.rationale = (
                "semantic and trust evidence agree, but bounded materialization "
                "limits the declared intended use"
            )
        assertion.conditions = sorted(set(assertion.conditions))
        pair_dir = output_dir / "pairs" / mapping_id
        comparison_path = write_json_atomic(
            str(pair_dir / "comparability.assertion.json"), assertion
        )
        policy = ReleasePolicy(
            policy_id="emg-atlas-bounded-materialization",
            policy_version="1.0.0",
            allow_trust_warnings=False,
            allow_conditional_comparability=True,
        )
        releases = []
        exchange_paths = []
        for record in (reference, candidate):
            decision = policy.decide(
                record["bundle"],
                record["manifest"],
                record["trust"],
                assertion,
            )
            decision_path = write_json_atomic(
                str(pair_dir / "{}.release.decision.json".format(record["role"])),
                decision,
            )
            exchange = {}
            if decision.export_permitted:
                exchange = export_atlas_exchange(
                    str(
                        output_dir
                        / "exchange"
                        / mapping_id
                        / record["role"]
                    ),
                    record["bundle"],
                    record["manifest"],
                    record["trust"],
                    assertion,
                    decision,
                )
            releases.append(decision.to_dict())
            exchange_paths.append(
                {
                    "recording_id": record["recording_id"],
                    "decision_path": str(
                        Path(decision_path).relative_to(output_dir)
                    ),
                    "exchange_exported": bool(exchange),
                }
            )
        mapping_results.append(
            {
                "mapping_id": mapping_id,
                "mapping_name": str(mapping.get("name", mapping_id)),
                "study_id": context["study_id"],
                "asset_id": context["asset_id"],
                "archive_sha256": context["archive_sha256"],
                "archive_size_bytes": context["archive_size_bytes"],
                "archive_verification_mode": context[
                    "archive_verification_mode"
                ],
                "frozen_first_normalized_sha256": expected,
                "frozen_first_digest_pass": frozen_first_digest_pass,
                "records": [
                    _record_public_row(record, output_dir)
                    for record in (reference, candidate)
                ],
                "comparison": assertion.to_dict(),
                "comparison_path": str(
                    Path(comparison_path).relative_to(output_dir)
                ),
                "comparison_sha256": sha256_contract(assertion),
                "release_decisions": releases,
                "release_artifacts": exchange_paths,
            }
        )

    source_rows = []
    for key in sorted(source_assets):
        item = source_assets[key]
        verification = item["verification"]
        source_rows.append(
            {
                "study_id": item["study_id"],
                "asset_id": item["asset_id"],
                "local_name": item["asset"]["local_name"],
                "size_bytes": verification["size_bytes"],
                "sha256": verification["sha256"],
                "checksum_algorithm": item["asset"]["checksum_algorithm"],
                "declared_checksum": item["asset"]["checksum"],
                "verification_mode": item["verification_mode"],
                "frozen_source_inventory_sha256": item[
                    "frozen_source_inventory_sha256"
                ],
                "source_release_registry_sha256": item[
                    "source_release_registry_sha256"
                ],
                "license": item["release_terms"]["license"],
                "license_url": item["release_terms"]["license_url"],
                "redistribution": item["release_terms"]["redistribution"],
                "attribution": item["release_terms"]["attribution"],
                "pass": verification["pass"],
            }
        )
    index = {
        "schema_version": MATERIALIZATION_VERSION,
        "implementation_commit": repository_state.get("commit"),
        "code_dirty": repository_state.get("dirty"),
        "scope": (
            "two real source members and 100 deterministic source-spanning "
            "unique timepoints for each of the six configuration-ready EMG "
            "mappings"
        ),
        "max_timepoints_per_record": max_timepoints,
        "source_assets": source_rows,
        "crosswalk": {
            "crosswalk_id": verified_crosswalk.crosswalk.crosswalk_id,
            "sha256": verified_crosswalk.crosswalk_sha256,
            "ontology_asset_sha256": (
                verified_crosswalk.crosswalk.target_release.asset_sha256
            ),
        },
        "inputs": {
            "corpus_sha256": sha256_file(str(corpus_file)),
            "coverage_sha256": sha256_file(str(coverage_file)),
            "mapping_outcomes_sha256": sha256_file(str(outcomes_file)),
            "source_inventory_sha256": sha256_file(
                str(source_inventory_file)
            ),
            "source_releases_sha256": sha256_file(
                str(source_releases_file)
            ),
        },
        "mappings": mapping_results,
        "summary": {
            "mapping_count": len(mapping_results),
            "recording_count": sum(
                len(item["records"]) for item in mapping_results
            ),
            "frozen_first_digest_passed": sum(
                item["frozen_first_digest_pass"] for item in mapping_results
            ),
            "conditional_comparisons": sum(
                item["comparison"]["status"] == "conditionally_comparable"
                for item in mapping_results
            ),
            "conditional_releases": sum(
                release["status"] == "conditional"
                for item in mapping_results
                for release in item["release_decisions"]
            ),
            "exchange_exports": sum(
                artifact["exchange_exported"]
                for item in mapping_results
                for artifact in item["release_artifacts"]
            ),
            "source_channels": sum(
                record["source_channel_count"]
                for item in mapping_results
                for record in item["records"]
            ),
            "qualified_channels": sum(
                record["qualified_channel_count"]
                for item in mapping_results
                for record in item["records"]
            ),
            "excluded_source_channels": sum(
                len(record["excluded_source_channels"])
                for item in mapping_results
                for record in item["records"]
            ),
        },
        "claim_boundary": (
            "These products prove real-source R/M/T/C/D generation, scoped "
            "comparability, conditional exchange, and queryability for bounded "
            "source-spanning 100-timepoint channel subsets. They do not "
            "constitute full trial, participant, dataset, amplitude, clinical, "
            "or AI-utility validation."
        ),
    }
    frames, query = _materialize_tables(index, output_dir)
    index["tables"] = query["tables"]
    index["duckdb_queries"] = query["duckdb_queries"]
    index["summary"]["pass"] = bool(
        len(mapping_results) == 6
        and all(row["pass"] for row in source_rows)
        and index["summary"]["frozen_first_digest_passed"] == 6
        and index["summary"]["conditional_comparisons"] == 6
        and index["summary"]["conditional_releases"] == 12
        and index["summary"]["exchange_exports"] == 12
        and query["duckdb_queries"]["pass"]
    )
    write_json_atomic(
        str(output_dir / "atlas.materialization.index.json"), index
    )
    if persist_iceberg:
        persist_atlas_frames(frames)
    return index


def _load_materialization_index(root: Path) -> Dict[str, Any]:
    path = root / "atlas.materialization.index.json"
    if not path.is_file():
        raise ValueError("materialization index is missing: {}".format(path))
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict) or not isinstance(
        value.get("mappings"), list
    ):
        raise ValueError("invalid materialization index: {}".format(path))
    return value


def _mapping_by_id(index: Mapping[str, Any]) -> Dict[str, Mapping[str, Any]]:
    rows = {
        str(row["mapping_id"]): row for row in index.get("mappings", [])
    }
    if len(rows) != len(index.get("mappings", [])):
        raise ValueError("materialization mapping IDs must be unique")
    return rows


def _artifact_map(manifest: Mapping[str, Any]) -> Dict[str, str]:
    rows = {
        str(row["artifact_id"]): str(row["sha256"])
        for row in manifest.get("inputs", [])
    }
    if len(rows) != len(manifest.get("inputs", [])):
        raise ValueError("manifest input artifact IDs must be unique")
    return rows


def _semantic_policy_projection(value: Mapping[str, Any]) -> Dict[str, Any]:
    """Discard only generated IDs, timestamps, and evidence digests."""
    return {
        key: value.get(key)
        for key in (
            "status",
            "intended_use",
            "variable",
            "rationale",
            "conditions",
            "policy_id",
            "policy_version",
            "reasons",
            "required_dimensions",
            "recording_sha256",
            "subject_id",
            "rule_version",
            "left_id",
            "right_id",
            "left_channel_id",
            "right_channel_id",
        )
        if key in value
    }


def audit_semantic_overlay_materialization(
    *,
    baseline_root: str,
    candidate_root: str,
    semantic_overlay_paths: Sequence[str],
    dirty_control_root: Optional[str] = None,
) -> Dict[str, Any]:
    """Prove that literature overlays add provenance without changing R/C/D.

    The candidate must be a clean, passing materialization. An optional dirty
    control demonstrates that the existing reproducibility gate still
    quarantines release when the same computation runs from a dirty tree.
    """
    baseline_dir = Path(baseline_root).resolve()
    candidate_dir = Path(candidate_root).resolve()
    baseline = _load_materialization_index(baseline_dir)
    candidate = _load_materialization_index(candidate_dir)
    overlay_paths = [Path(value).resolve() for value in semantic_overlay_paths]
    overlays: Dict[str, Tuple[SemanticOverlay, Path, str]] = {}
    for path in overlay_paths:
        if not path.is_file():
            raise ValueError("semantic overlay is missing: {}".format(path))
        overlay = SemanticOverlay.from_dict(
            json.loads(path.read_text(encoding="utf-8"))
        )
        if overlay.study_id in overlays:
            raise ValueError(
                "only one audit overlay is allowed per study: {}".format(
                    overlay.study_id
                )
            )
        overlays[overlay.study_id] = (overlay, path, sha256_file(str(path)))
    if not overlays:
        raise ValueError("at least one semantic overlay is required")

    baseline_mappings = _mapping_by_id(baseline)
    candidate_mappings = _mapping_by_id(candidate)
    if set(baseline_mappings) != set(candidate_mappings):
        raise ValueError("baseline and candidate mapping denominators differ")

    record_rows: List[Dict[str, Any]] = []
    mapping_rows: List[Dict[str, Any]] = []
    release_rows: List[Dict[str, Any]] = []
    for mapping_id in sorted(baseline_mappings):
        study_id = mapping_id.rsplit("-", 1)[0]
        if study_id not in overlays:
            raise ValueError(
                "mapping has no registered study overlay: {}".format(mapping_id)
            )
        overlay, _, overlay_sha256 = overlays[study_id]
        baseline_mapping = baseline_mappings[mapping_id]
        candidate_mapping = candidate_mappings[mapping_id]
        comparison_invariant = _semantic_policy_projection(
            baseline_mapping["comparison"]
        ) == _semantic_policy_projection(candidate_mapping["comparison"])
        mapping_rows.append(
            {
                "mapping_id": mapping_id,
                "study_id": study_id,
                "overlay_id": overlay.overlay_id,
                "comparison_policy_invariant": comparison_invariant,
                "baseline_comparison_status": baseline_mapping["comparison"][
                    "status"
                ],
                "candidate_comparison_status": candidate_mapping["comparison"][
                    "status"
                ],
            }
        )
        baseline_records = {
            str(row["role"]): row for row in baseline_mapping["records"]
        }
        candidate_records = {
            str(row["role"]): row for row in candidate_mapping["records"]
        }
        if set(baseline_records) != set(candidate_records):
            raise ValueError(
                "record roles differ for mapping {}".format(mapping_id)
            )
        for role in sorted(baseline_records):
            baseline_record = baseline_records[role]
            candidate_record = candidate_records[role]
            baseline_bundle = json.loads(
                (
                    baseline_dir / baseline_record["recording_path"]
                ).read_text(encoding="utf-8")
            )
            candidate_bundle = json.loads(
                (
                    candidate_dir / candidate_record["recording_path"]
                ).read_text(encoding="utf-8")
            )
            baseline_manifest = json.loads(
                (
                    baseline_dir / baseline_record["manifest_path"]
                ).read_text(encoding="utf-8")
            )
            candidate_manifest = json.loads(
                (
                    candidate_dir / candidate_record["manifest_path"]
                ).read_text(encoding="utf-8")
            )
            baseline_trust = TrustReport.from_dict(
                json.loads(
                    (
                        baseline_dir / baseline_record["trust_path"]
                    ).read_text(encoding="utf-8")
                )
            )
            candidate_trust = TrustReport.from_dict(
                json.loads(
                    (
                        candidate_dir / candidate_record["trust_path"]
                    ).read_text(encoding="utf-8")
                )
            )
            baseline_artifacts = _artifact_map(baseline_manifest)
            candidate_artifacts = _artifact_map(candidate_manifest)
            candidate_overlay_sha = candidate_artifacts.pop(
                overlay.overlay_id, None
            )
            baseline_overlay_absent = (
                overlay.overlay_id not in baseline_artifacts
            )
            trust_dimensions_invariant = all(
                baseline_trust.dimension_status(dimension)
                == candidate_trust.dimension_status(dimension)
                for dimension in (
                    "semantic_fidelity",
                    "scientific_validity",
                    "computational_reproducibility",
                    "traceability",
                )
            )
            overlay_evidence = [
                item
                for item in candidate_trust.evidence
                if item.rule_id == "literature.semantic-overlay.bound"
                and item.artifact_id == overlay.overlay_id
                and item.dimension == "traceability"
                and item.status == TrustStatus.PASS
            ]
            record_rows.append(
                {
                    "mapping_id": mapping_id,
                    "role": role,
                    "recording_id": candidate_record["recording_id"],
                    "overlay_id": overlay.overlay_id,
                    "recording_bundle_invariant": (
                        baseline_bundle == candidate_bundle
                        and baseline_record["recording_sha256"]
                        == candidate_record["recording_sha256"]
                    ),
                    "normalized_record_invariant": (
                        baseline_record["normalized_record_sha256"]
                        == candidate_record["normalized_record_sha256"]
                    ),
                    "sample_digest_invariant": (
                        baseline_record["sample_sha256"]
                        == candidate_record["sample_sha256"]
                    ),
                    "non_overlay_manifest_inputs_invariant": (
                        baseline_artifacts == candidate_artifacts
                    ),
                    "baseline_overlay_absent": baseline_overlay_absent,
                    "candidate_overlay_sha256": candidate_overlay_sha,
                    "expected_overlay_sha256": overlay_sha256,
                    "overlay_checksum_bound": (
                        candidate_overlay_sha == overlay_sha256
                    ),
                    "overlay_traceability_evidence_count": len(
                        overlay_evidence
                    ),
                    "trust_dimensions_invariant": trust_dimensions_invariant,
                    "candidate_trust_dimensions": {
                        dimension: candidate_trust.dimension_status(
                            dimension
                        ).value
                        for dimension in (
                            "semantic_fidelity",
                            "scientific_validity",
                            "computational_reproducibility",
                            "traceability",
                        )
                    },
                }
            )

        baseline_releases = {
            str(row["subject_id"]): row
            for row in baseline_mapping["release_decisions"]
        }
        candidate_releases = {
            str(row["subject_id"]): row
            for row in candidate_mapping["release_decisions"]
        }
        if set(baseline_releases) != set(candidate_releases):
            raise ValueError(
                "release denominators differ for mapping {}".format(mapping_id)
            )
        exported = {
            str(row["recording_id"]): bool(row["exchange_exported"])
            for row in candidate_mapping["release_artifacts"]
        }
        for recording_id in sorted(baseline_releases):
            baseline_release = baseline_releases[recording_id]
            candidate_release = candidate_releases[recording_id]
            release_rows.append(
                {
                    "mapping_id": mapping_id,
                    "recording_id": recording_id,
                    "release_policy_invariant": (
                        _semantic_policy_projection(baseline_release)
                        == _semantic_policy_projection(candidate_release)
                    ),
                    "baseline_status": baseline_release["status"],
                    "candidate_status": candidate_release["status"],
                    "candidate_exchange_exported": exported.get(
                        recording_id, False
                    ),
                }
            )

    dirty_control = None
    if dirty_control_root is not None:
        dirty_dir = Path(dirty_control_root).resolve()
        dirty_index = _load_materialization_index(dirty_dir)
        dirty_control = {
            "index_sha256": sha256_file(
                str(dirty_dir / "atlas.materialization.index.json")
            ),
            "code_dirty": bool(dirty_index.get("code_dirty")),
            "conditional_release_count": int(
                dirty_index["summary"]["conditional_releases"]
            ),
            "exchange_export_count": int(
                dirty_index["summary"]["exchange_exports"]
            ),
            "fail_closed": bool(
                dirty_index.get("code_dirty")
                and dirty_index["summary"]["conditional_releases"] == 0
                and dirty_index["summary"]["exchange_exports"] == 0
            ),
        }

    all_record_checks = all(
        row["recording_bundle_invariant"]
        and row["normalized_record_invariant"]
        and row["sample_digest_invariant"]
        and row["non_overlay_manifest_inputs_invariant"]
        and row["baseline_overlay_absent"]
        and row["overlay_checksum_bound"]
        and row["overlay_traceability_evidence_count"] == 1
        and row["trust_dimensions_invariant"]
        for row in record_rows
    )
    all_mapping_checks = all(
        row["comparison_policy_invariant"]
        and row["baseline_comparison_status"]
        == row["candidate_comparison_status"]
        == "conditionally_comparable"
        for row in mapping_rows
    )
    all_release_checks = all(
        row["release_policy_invariant"]
        and row["baseline_status"]
        == row["candidate_status"]
        == "conditional"
        and row["candidate_exchange_exported"]
        for row in release_rows
    )
    summary = {
        "mapping_count": len(mapping_rows),
        "recording_count": len(record_rows),
        "release_count": len(release_rows),
        "recording_bundle_invariant_count": sum(
            row["recording_bundle_invariant"] for row in record_rows
        ),
        "overlay_checksum_bound_count": sum(
            row["overlay_checksum_bound"] for row in record_rows
        ),
        "trust_dimension_invariant_count": sum(
            row["trust_dimensions_invariant"] for row in record_rows
        ),
        "comparison_policy_invariant_count": sum(
            row["comparison_policy_invariant"] for row in mapping_rows
        ),
        "release_policy_invariant_count": sum(
            row["release_policy_invariant"] for row in release_rows
        ),
        "candidate_conditional_release_count": sum(
            row["candidate_status"] == "conditional" for row in release_rows
        ),
        "candidate_exchange_export_count": sum(
            row["candidate_exchange_exported"] for row in release_rows
        ),
        "dirty_control_fail_closed": bool(
            dirty_control is not None and dirty_control["fail_closed"]
        ),
    }
    passed = bool(
        candidate.get("code_dirty") is False
        and candidate.get("summary", {}).get("pass") is True
        and all_record_checks
        and all_mapping_checks
        and all_release_checks
        and (dirty_control is None or dirty_control["fail_closed"])
    )
    return {
        "schema_version": SEMANTIC_OVERLAY_AUDIT_VERSION,
        "created_at": datetime.now(timezone.utc).isoformat().replace(
            "+00:00", "Z"
        ),
        "baseline_index_sha256": sha256_file(
            str(baseline_dir / "atlas.materialization.index.json")
        ),
        "candidate_index_sha256": sha256_file(
            str(candidate_dir / "atlas.materialization.index.json")
        ),
        "candidate_implementation_commit": candidate.get(
            "implementation_commit"
        ),
        "candidate_code_dirty": bool(candidate.get("code_dirty")),
        "audit_implementation_sha256": sha256_file(__file__),
        "overlays": [
            {
                "study_id": study_id,
                "overlay_id": value[0].overlay_id,
                "sha256": value[2],
            }
            for study_id, value in sorted(overlays.items())
        ],
        "record_rows": record_rows,
        "mapping_rows": mapping_rows,
        "release_rows": release_rows,
        "dirty_control": dirty_control,
        "summary": summary,
        "pass": passed,
        "claim_boundary": (
            "This audit proves that the declared overlays were checksum-bound "
            "as provenance-only manifest inputs for the bounded 12-record "
            "materialization without changing recording bundles, trust "
            "dimensions, comparison policy, or release policy. It does not "
            "promote literature statements to subject/trial measurements or "
            "validate full recordings, amplitude pooling, or AI utility."
        ),
    }
