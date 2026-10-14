"""Repository-wide task and anatomy semantic coverage audit.

The audit keeps three states distinct:

* ``mapped``: a source declaration has a verified canonical target;
* ``excluded``: the repository explicitly declares a channel unusable;
* ``unresolved``: the necessary source declaration is absent or cannot be
  resolved without guessing.

It does not manufacture RecordingBundles for the 78 validation mappings.
Instead, it produces a checksum-bound materialization ledger showing which
configurations are ready and which evidence is still missing.
"""
from __future__ import annotations

import json
import re
from collections import Counter
from pathlib import Path
from typing import Any, Dict, List, Mapping, Optional, Sequence

import pandas as pd
import yaml

from harmo.evidence.anatomy_crosswalk import VerifiedAnatomyCrosswalk
from harmo.evidence.io import sha256_file
from harmo.evidence.run import current_repository_state
from harmo.ingest.warehouse import ingest_definition_table
from harmo.warehouse.schema import (
    ANATOMY_CROSSWALK,
    ONTOLOGY_RELEASE,
    SEMANTIC_COVERAGE_AUDIT,
)


SEMANTIC_COVERAGE_VERSION = "1.0.0"


def _is_emg(sensor_type: object) -> bool:
    normalized = re.sub(r"[^a-z]", "", str(sensor_type).lower())
    return "emg" in normalized or "electromyography" in normalized


def _canonical_muscle(value: object) -> Optional[str]:
    text = str(value or "").strip().lower().replace("-", "_").replace(" ", "_")
    text = re.sub(r"_+", "_", text)
    return text or None


def _load_yaml(path: Path) -> Dict[str, Any]:
    value = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    if not isinstance(value, dict):
        raise ValueError("{} must contain an object".format(path))
    return value


def _mapping_by_id(config: Mapping[str, Any], study_id: str) -> Dict[str, Mapping]:
    return {
        "{}-{:03d}".format(study_id, index): mapping
        for index, mapping in enumerate(config.get("file_mappings") or [], 1)
        if mapping.get("enabled", True)
    }


def _hed_terms(path: Optional[Path]) -> Sequence[str]:
    if path is None or not path.is_file():
        return ()
    value = json.loads(path.read_text(encoding="utf-8"))
    terms: List[str] = []
    for dimension in value.values():
        if not isinstance(dimension, Mapping):
            continue
        hed = dimension.get("HED")
        if isinstance(hed, Mapping):
            terms.extend(str(key) for key in hed)
    return tuple(sorted(set(terms)))


def _template_matches(value: str, terms: Sequence[str]) -> bool:
    pattern = re.escape(value)
    pattern = re.sub(r"\\\{[^{}]+\\\}", r"[^/]+", pattern)
    return any(re.fullmatch(pattern, term) for term in terms)


def _task_status(mapping: Mapping[str, Any], terms: Sequence[str]) -> Dict[str, Any]:
    raw = mapping.get("hed_tag")
    if raw in (None, "", []):
        return {
            "status": "unresolved",
            "declared_tags": [],
            "resolved_tags": [],
            "unresolved_tags": [],
            "reason": "mapping has no HED task reference",
        }
    tags = raw if isinstance(raw, list) else [raw]
    tags = [str(value) for value in tags]
    resolved = [tag for tag in tags if _template_matches(tag, terms)]
    unresolved = [tag for tag in tags if tag not in resolved]
    if resolved and not unresolved:
        status = "declared"
        reason = "all mapping HED references resolve against the study dictionary"
    elif resolved:
        status = "partial"
        reason = "only some mapping HED references resolve"
    else:
        status = "invalid-reference"
        reason = "mapping HED references do not resolve"
    return {
        "status": status,
        "declared_tags": tags,
        "resolved_tags": resolved,
        "unresolved_tags": unresolved,
        "reason": reason,
    }


def _study_anatomy(
    channel_path: Path,
    verified_crosswalk: VerifiedAnatomyCrosswalk,
) -> Dict[str, Any]:
    rows = (_load_yaml(channel_path).get("channels") or [])
    emg_rows = [row for row in rows if _is_emg(row.get("sensor_type"))]
    targets = {
        entry.source_term.term_id: entry
        for entry in verified_crosswalk.crosswalk.entries
        if entry.predicate == "exactMatch"
    }
    mapped = []
    excluded = []
    unresolved = []
    for row in emg_rows:
        source_label = str(row.get("column_name", ""))
        raw_muscle = str(row.get("muscle", "")).strip()
        if "ignore_channel" in raw_muscle.lower():
            excluded.append(
                {
                    "source_label": source_label,
                    "source_muscle": raw_muscle,
                    "reason": "explicit ignore_channel declaration",
                }
            )
            continue
        muscle = _canonical_muscle(raw_muscle)
        source_term_id = (
            "HARMOMUSCLE:{}".format(muscle) if muscle is not None else None
        )
        entry = targets.get(source_term_id)
        if entry is None:
            unresolved.append(
                {
                    "source_label": source_label,
                    "source_muscle": raw_muscle or None,
                    "reason": (
                        "canonical muscle is absent"
                        if muscle is None
                        else "canonical muscle lacks a verified exact crosswalk"
                    ),
                }
            )
            continue
        mapped.append(
            {
                "source_label": source_label,
                "source_muscle": raw_muscle,
                "source_term_id": entry.source_term.term_id,
                "target_term_id": entry.target_term.term_id,
                "target_label": entry.target_term.label,
                "laterality": str(row.get("side", "unknown")).lower(),
            }
        )
    if not emg_rows:
        status = "not-applicable"
    elif unresolved and mapped:
        status = "partial"
    elif unresolved:
        status = "unresolved"
    else:
        status = "mapped"
    return {
        "status": status,
        "emg_channel_definitions": len(emg_rows),
        "mapped_channels": mapped,
        "excluded_channels": excluded,
        "unresolved_channels": unresolved,
        "channel_definition_sha256": sha256_file(str(channel_path)),
    }


def audit_active_mapping_semantics(
    repository_root: str,
    registry_path: str,
    mapping_outcomes_path: str,
    verified_crosswalk: VerifiedAnatomyCrosswalk,
) -> Dict[str, Any]:
    """Audit all frozen active mappings without inferring missing semantics."""
    root = Path(repository_root).resolve()
    registry_file = Path(registry_path).resolve()
    mapping_file = Path(mapping_outcomes_path).resolve()
    registry = _load_yaml(registry_file)
    outcomes = json.loads(mapping_file.read_text(encoding="utf-8"))
    if not isinstance(outcomes, list):
        raise ValueError("mapping outcomes must be an array")
    expected_denominator = int(registry["experiments"]["A"]["denominator"])
    if len(outcomes) != expected_denominator:
        raise ValueError(
            "mapping denominator differs: expected {}, observed {}".format(
                expected_denominator, len(outcomes)
            )
        )

    study_records: Dict[str, Dict[str, Any]] = {}
    source_attestations = []
    for study in registry.get("studies") or []:
        study_id = str(study["id"])
        config_path = root / str(study["config"])
        config = _load_yaml(config_path)
        channels_definition = (config.get("definitions") or {}).get("channels") or {}
        channel_path = config_path.parent / str(channels_definition.get("path", ""))
        if not channel_path.is_file():
            raise ValueError("{} channel definitions are missing".format(study_id))
        hed_definition = (
            (config.get("definitions") or {}).get("hed_json")
            or (config.get("definitions") or {}).get("session_hed")
            or {}
        )
        hed_path = (
            config_path.parent / str(hed_definition.get("path"))
            if hed_definition.get("path")
            else None
        )
        anatomy = _study_anatomy(channel_path, verified_crosswalk)
        study_records[study_id] = {
            "config": config,
            "mappings": _mapping_by_id(config, study_id),
            "hed_terms": _hed_terms(hed_path),
            "anatomy": anatomy,
            "emg_available": bool(study.get("emg_available")),
        }
        for role, path in (
            ("config", config_path),
            ("channels", channel_path),
            ("hed", hed_path),
        ):
            if path is not None and path.is_file():
                source_attestations.append(
                    {
                        "study_id": study_id,
                        "role": role,
                        "path": str(path.relative_to(root)),
                        "sha256": sha256_file(str(path)),
                        "size_bytes": path.stat().st_size,
                    }
                )

    rows = []
    for outcome in sorted(outcomes, key=lambda value: value["mapping_id"]):
        study_id = str(outcome["study_id"])
        mapping_id = str(outcome["mapping_id"])
        study = study_records.get(study_id)
        if study is None or mapping_id not in study["mappings"]:
            raise ValueError("{} is not an active registry mapping".format(mapping_id))
        mapping = study["mappings"][mapping_id]
        sensor_type = str(
            (mapping.get("static_metadata") or {}).get("sensor_type", "")
        )
        direct_emg = _is_emg(sensor_type)
        mixed_emg_container = bool(
            not direct_emg
            and "mat" in sensor_type.lower()
            and study["emg_available"]
            and study["anatomy"]["emg_channel_definitions"] > 0
        )
        anatomy_applies = direct_emg or mixed_emg_container
        anatomy = study["anatomy"]
        if anatomy_applies:
            anatomy_status = anatomy["status"]
            mapped_count = len(anatomy["mapped_channels"])
            excluded_count = len(anatomy["excluded_channels"])
            unresolved_count = len(anatomy["unresolved_channels"])
        else:
            anatomy_status = "not-applicable"
            mapped_count = excluded_count = unresolved_count = 0
        task = _task_status(mapping, study["hed_terms"])
        config_ready = bool(
            task["status"] == "declared"
            and anatomy_status in ("mapped", "not-applicable")
        )
        rows.append(
            {
                "mapping_id": mapping_id,
                "study_id": study_id,
                "mapping_name": str(mapping.get("name", "")),
                "sensor_type": sensor_type,
                "emg_semantics_apply": anatomy_applies,
                "mixed_emg_container": mixed_emg_container,
                "task_status": task["status"],
                "task_declared_tags": task["declared_tags"],
                "task_resolved_tags": task["resolved_tags"],
                "task_unresolved_tags": task["unresolved_tags"],
                "task_reason": task["reason"],
                "anatomy_status": anatomy_status,
                "anatomy_mapped_channels": mapped_count,
                "anatomy_excluded_channels": excluded_count,
                "anatomy_unresolved_channels": unresolved_count,
                "config_ready_for_recording_materialization": config_ready,
                "recording_bundle_status": "not-materialized-by-this-audit",
                "source_outcome": outcome.get("outcome"),
                "source_sha256": outcome.get("source_sha256"),
                "config_sha256": outcome.get("config_sha256"),
            }
        )

    task_counts = Counter(row["task_status"] for row in rows)
    anatomy_counts = Counter(row["anatomy_status"] for row in rows)
    unique_anatomy = {
        study_id: record["anatomy"] for study_id, record in study_records.items()
    }
    channel_mapped = sum(
        len(value["mapped_channels"]) for value in unique_anatomy.values()
    )
    channel_excluded = sum(
        len(value["excluded_channels"]) for value in unique_anatomy.values()
    )
    channel_unresolved = sum(
        len(value["unresolved_channels"]) for value in unique_anatomy.values()
    )
    repository_state = current_repository_state()
    return {
        "schema_version": SEMANTIC_COVERAGE_VERSION,
        "implementation_commit": repository_state.get("commit"),
        "code_dirty": repository_state.get("dirty"),
        "scope": (
            "configuration-level semantic coverage of the frozen 78 active "
            "mapping ledger; not 78 materialized RecordingBundles"
        ),
        "registry": {
            "path": str(registry_file.relative_to(root)),
            "sha256": sha256_file(str(registry_file)),
        },
        "mapping_outcomes": {
            "path": str(mapping_file.relative_to(root)),
            "sha256": sha256_file(str(mapping_file)),
        },
        "crosswalk": {
            "crosswalk_id": verified_crosswalk.crosswalk.crosswalk_id,
            "sha256": verified_crosswalk.crosswalk_sha256,
            "ontology_asset_sha256": (
                verified_crosswalk.crosswalk.target_release.asset_sha256
            ),
            "verified_exact_entries": sum(
                entry.predicate == "exactMatch"
                for entry in verified_crosswalk.crosswalk.entries
            ),
        },
        "source_attestations": sorted(
            source_attestations,
            key=lambda row: (row["study_id"], row["role"], row["path"]),
        ),
        "study_anatomy": unique_anatomy,
        "mapping_rows": rows,
        "summary": {
            "mapping_denominator": len(rows),
            "emg_semantics_applicable_mappings": sum(
                row["emg_semantics_apply"] for row in rows
            ),
            "task_status_counts": dict(sorted(task_counts.items())),
            "anatomy_status_counts": dict(sorted(anatomy_counts.items())),
            "config_ready_for_recording_materialization": sum(
                row["config_ready_for_recording_materialization"] for row in rows
            ),
            "emg_config_ready_for_recording_materialization": sum(
                row["emg_semantics_apply"]
                and row["config_ready_for_recording_materialization"]
                for row in rows
            ),
            "unique_emg_channel_definitions": (
                channel_mapped + channel_excluded + channel_unresolved
            ),
            "mapped_emg_channel_definitions": channel_mapped,
            "explicitly_excluded_emg_channel_definitions": channel_excluded,
            "unresolved_emg_channel_definitions": channel_unresolved,
        },
        "claim_boundary": (
            "Mapped means the repository channel muscle declaration has a "
            "verified exact UBERON crosswalk. Anonymous HD-sEMG electrodes and "
            "mapping-level tasks without resolvable HED references remain "
            "unresolved. The audit does not imply signal validity, anatomical "
            "electrode-placement validity, or completed R/M/T/C/D products."
        ),
    }


def build_semantic_audit_frames(
    audit: Mapping[str, Any],
    verified_crosswalk: VerifiedAnatomyCrosswalk,
) -> Dict[str, pd.DataFrame]:
    """Build deterministic Iceberg rows for ontology, crosswalk, and coverage."""
    crosswalk = verified_crosswalk.crosswalk
    release = crosswalk.target_release
    release_frame = pd.DataFrame(
        [
            {
                "ontology": release.ontology,
                "release_tag": release.release_tag,
                "version_iri": release.version_iri,
                "data_version": release.data_version,
                "asset_url": release.asset_url,
                "asset_sha256": release.asset_sha256,
                "asset_size_bytes": release.asset_size_bytes,
                "license": release.license,
                "license_url": release.license_url,
            }
        ]
    )
    crosswalk_frame = pd.DataFrame(
        [
            {
                "crosswalk_id": crosswalk.crosswalk_id,
                "crosswalk_sha256": verified_crosswalk.crosswalk_sha256,
                "source_ontology": entry.source_term.ontology,
                "source_version": entry.source_term.ontology_version,
                "source_term_id": entry.source_term.term_id,
                "source_label": entry.source_term.label,
                "target_ontology": entry.target_term.ontology,
                "target_version": entry.target_term.ontology_version,
                "target_term_id": entry.target_term.term_id,
                "target_label": entry.target_term.label,
                "predicate": entry.predicate,
                "match_basis": entry.match_basis,
                "matched_text": entry.matched_text,
                "ontology_asset_sha256": release.asset_sha256,
            }
            for entry in crosswalk.entries
        ]
    )
    coverage_frame = pd.DataFrame(
        [
            {
                **{
                    key: value
                    for key, value in row.items()
                    if not isinstance(value, (list, dict))
                },
                "task_declared_tags_json": json.dumps(
                    row["task_declared_tags"],
                    ensure_ascii=False,
                    sort_keys=True,
                    separators=(",", ":"),
                ),
                "task_resolved_tags_json": json.dumps(
                    row["task_resolved_tags"],
                    ensure_ascii=False,
                    sort_keys=True,
                    separators=(",", ":"),
                ),
                "task_unresolved_tags_json": json.dumps(
                    row["task_unresolved_tags"],
                    ensure_ascii=False,
                    sort_keys=True,
                    separators=(",", ":"),
                ),
                "registry_sha256": audit["registry"]["sha256"],
                "mapping_outcomes_sha256": audit["mapping_outcomes"]["sha256"],
                "crosswalk_sha256": verified_crosswalk.crosswalk_sha256,
            }
            for row in audit["mapping_rows"]
        ]
    )
    return {
        ONTOLOGY_RELEASE: release_frame,
        ANATOMY_CROSSWALK: crosswalk_frame,
        SEMANTIC_COVERAGE_AUDIT: coverage_frame,
    }


def persist_semantic_audit_frames(
    audit: Mapping[str, Any],
    verified_crosswalk: VerifiedAnatomyCrosswalk,
    spark=None,
) -> Dict[str, pd.DataFrame]:
    frames = build_semantic_audit_frames(audit, verified_crosswalk)
    created = spark is None
    if created:
        from harmo.warehouse.connection import get_spark_session

        spark = get_spark_session(app_name="harmo-semantic-coverage")
    try:
        if spark is None:
            raise RuntimeError("Spark session is unavailable")
        spark.sql("CREATE NAMESPACE IF NOT EXISTS local.warehouse")
        for table_name, frame in frames.items():
            ingest_definition_table(spark, frame, table_name)
    finally:
        if created and spark is not None:
            spark.stop()
    return frames
