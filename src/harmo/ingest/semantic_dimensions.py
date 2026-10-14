"""Materialize first-class recording semantics into Iceberg dimensions.

The RecordingBundle remains the checksum-bound source of truth.  These tables
make task and anatomy semantics queryable without weakening their provenance.
"""
from __future__ import annotations

import hashlib
import json
from typing import Dict, Optional, TYPE_CHECKING

import pandas as pd

from harmo.evidence.io import sha256_contract
from harmo.evidence.models import HarmoRecordingBundle
from harmo.ingest.warehouse import ingest_definition_table
from harmo.warehouse.schema import (
    CHANNEL_ANATOMY,
    DIM_ANATOMY,
    DIM_TASK,
    SESSION_TASK,
)

if TYPE_CHECKING:
    from pyspark.sql import SparkSession


def _canonical_json(value: object) -> str:
    return json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    )


def _semantic_id(prefix: str, value: object) -> str:
    digest = hashlib.sha256(_canonical_json(value).encode("utf-8")).hexdigest()
    return "{}-{}".format(prefix, digest[:24])


def _mapping_columns(mapping) -> Dict[str, object]:
    return {
        "mapping_method": mapping.method,
        "mapping_version": mapping.mapping_version,
        "mapping_status": mapping.status,
        "mapping_confidence": mapping.confidence,
        "mapping_evidence_refs_json": _canonical_json(mapping.evidence_refs),
    }


def build_semantic_dimension_frames(
    bundle: HarmoRecordingBundle,
) -> Dict[str, pd.DataFrame]:
    """Build deterministic dimension and bridge rows from a 1.2 bundle."""
    if bundle.schema_version != "1.2.0":
        raise ValueError(
            "semantic dimensions require recording schema 1.2.0"
        )
    if bundle.task is None:
        raise ValueError("semantic dimensions require a recording task")

    missing_anatomy = [
        channel.channel_id
        for channel in bundle.channels
        if "emg" in channel.sensor_type.lower().replace("-", "")
        and channel.anatomy is None
    ]
    if missing_anatomy:
        raise ValueError(
            "EMG channels lack first-class anatomy: {}".format(
                ", ".join(missing_anatomy)
            )
        )

    recording_sha256 = sha256_contract(bundle)
    task = bundle.task
    task_key = {
        "activity": task.activity,
        "ontology_terms": [
            term.to_dict()
            for term in sorted(
                task.ontology_terms,
                key=lambda value: (
                    value.ontology,
                    value.ontology_version,
                    value.term_id,
                ),
            )
        ],
        "facets": task.facets,
        "compatibility_group": task.compatibility_group,
    }
    task_id = _semantic_id("task", task_key)
    dim_task = pd.DataFrame(
        [
            {
                "task_id": task_id,
                "activity": task.activity,
                "ontology_terms_json": _canonical_json(
                    task_key["ontology_terms"]
                ),
                "facets_json": _canonical_json(task.facets),
                "compatibility_group": task.compatibility_group,
            }
        ]
    )
    session_task = pd.DataFrame(
        [
            {
                "recording_id": bundle.recording_id,
                "study_id": bundle.study_id,
                "subject_id": bundle.subject_id,
                "session_id": bundle.session_id,
                "task_id": task_id,
                "source_label": task.source_label,
                "recording_sha256": recording_sha256,
                **_mapping_columns(task.mapping),
            }
        ]
    )

    dimension_rows = {}
    bridge_rows = []
    for channel in bundle.channels:
        anatomy = channel.anatomy
        if anatomy is None:
            continue
        term = anatomy.ontology_term
        anatomy_key = {
            "entity_type": anatomy.entity_type,
            "ontology": term.ontology,
            "ontology_version": term.ontology_version,
            "term_id": term.term_id,
        }
        anatomy_id = _semantic_id("anatomy", anatomy_key)
        dimension_rows[anatomy_id] = {
            "anatomy_id": anatomy_id,
            "entity_type": anatomy.entity_type,
            "canonical_label": anatomy.canonical_label,
            "ontology": term.ontology,
            "ontology_version": term.ontology_version,
            "term_id": term.term_id,
            "term_label": term.label,
        }
        bridge_rows.append(
            {
                "recording_id": bundle.recording_id,
                "study_id": bundle.study_id,
                "subject_id": bundle.subject_id,
                "session_id": bundle.session_id,
                "channel_id": channel.channel_id,
                "anatomy_id": anatomy_id,
                "laterality": anatomy.laterality,
                "source_label": anatomy.source_label,
                "recording_sha256": recording_sha256,
                **_mapping_columns(anatomy.mapping),
            }
        )

    return {
        DIM_TASK: dim_task,
        SESSION_TASK: session_task,
        DIM_ANATOMY: pd.DataFrame(
            [dimension_rows[key] for key in sorted(dimension_rows)]
        ),
        CHANNEL_ANATOMY: pd.DataFrame(bridge_rows),
    }


def persist_semantic_dimensions(
    bundle: HarmoRecordingBundle,
    spark: Optional["SparkSession"] = None,
) -> Dict[str, pd.DataFrame]:
    """Append semantic frames to Iceberg, creating their tables when absent."""
    frames = build_semantic_dimension_frames(bundle)
    created = spark is None
    if created:
        from harmo.warehouse.connection import get_spark_session

        spark = get_spark_session(app_name="harmo-semantic-dimensions")
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
