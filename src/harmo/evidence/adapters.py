"""Adapters that preserve signal semantics when creating AI-ready bundles."""
from __future__ import annotations

import math
from pathlib import Path
from statistics import median
from typing import Any, Dict, List, Optional, Tuple
from urllib.parse import unquote, urlparse
from urllib.request import url2pathname

import pandas as pd

from .io import sha256_file
from .models import (
    AnatomicalEntity,
    ArrayReference,
    ChannelDescriptor,
    HarmoRecordingBundle,
    OntologyTerm,
    RECORDING_SCHEMA_VERSION,
    SemanticMapping,
    SourceAsset,
    TaskDescriptor,
    Timebase,
    TransformationWarning,
)


def bundle_from_wide_dataframe(
    frame: pd.DataFrame,
    *,
    recording_id: str,
    study_id: str,
    subject_id: str,
    session_id: str,
    source_path: str,
    array_uri: str,
    channel_units: Dict[str, str],
    time_column: str = "time",
    sensor_type: str = "surface-emg",
    channel_metadata: Optional[Dict[str, Dict[str, Any]]] = None,
    task_metadata: Optional[Dict[str, Any]] = None,
    array_format: str = "parquet",
    array_dtype: str = "float64",
    regularity_tolerance: float = 0.01,
    explicit_timestamps_uri: Optional[str] = None,
) -> Tuple[HarmoRecordingBundle, List[TransformationWarning]]:
    """Create a bundle only after checking timebase and channel completeness.

    Data values remain external to the JSON contract; ``array_uri`` points to the
    normalized matrix and its checksum is recorded when it is a local file.
    """
    if time_column not in frame.columns:
        raise ValueError("time column is missing: {}".format(time_column))
    raw_channel_names = [name for name in frame.columns if name != time_column]
    channel_names = [str(name) for name in raw_channel_names]
    if not channel_names:
        raise ValueError("at least one signal channel is required")
    if len(channel_names) != len(set(channel_names)):
        raise ValueError("signal channel names must be unique")
    missing_units = sorted(set(channel_names) - set(channel_units))
    extra_units = sorted(set(channel_units) - set(channel_names))
    if missing_units or extra_units:
        raise ValueError(
            "channel/unit mismatch; missing={}, extra={}".format(missing_units, extra_units)
        )

    times = [float(value) for value in frame[time_column].tolist()]
    if len(times) < 2 or not all(math.isfinite(value) for value in times):
        raise ValueError("time column requires at least two finite samples")
    deltas = [right - left for left, right in zip(times, times[1:])]
    if any(delta <= 0 for delta in deltas):
        raise ValueError("time must be strictly increasing")
    period = median(deltas)
    maximum_relative_deviation = max(abs(delta - period) / period for delta in deltas)
    warnings = []  # type: List[TransformationWarning]
    timestamps_uri = None
    if maximum_relative_deviation > regularity_tolerance:
        timestamps_uri = explicit_timestamps_uri or array_uri + "#" + time_column
        warnings.append(TransformationWarning(
            code="irregular-timebase",
            severity="warning",
            message="timestamps are irregular; explicit timestamps must be retained",
            field=time_column,
            evidence="max_relative_deviation={:.8g}".format(maximum_relative_deviation),
        ))

    for raw_name, name in zip(raw_channel_names, channel_names):
        numeric = pd.to_numeric(frame[raw_name], errors="coerce")
        invalid = frame[raw_name].notna() & numeric.isna()
        if bool(invalid.any()):
            raise ValueError("signal channel contains non-numeric values: {}".format(name))
        if bool((~numeric.isna() & ~numeric.map(math.isfinite)).any()):
            raise ValueError("signal channel contains infinite values: {}".format(name))
        missing_count = int(numeric.isna().sum())
        if missing_count:
            warnings.append(TransformationWarning(
                code="missing-samples.{}".format(name),
                severity="warning",
                message="signal channel contains missing samples",
                field=name,
                evidence="missing_count={}".format(missing_count),
                dimension="scientific_validity",
            ))

    metadata = channel_metadata or {}
    channels = []
    for index, name in enumerate(channel_names):
        item = metadata.get(name, {})
        anatomy = item.get("anatomy")
        channels.append(ChannelDescriptor(
            channel_id="ch-{:03d}".format(index + 1),
            name=name,
            unit=channel_units[name],
            sensor_type=item.get("sensor_type", sensor_type),
            muscle=item.get("muscle"),
            side=item.get("side"),
            source_name=item.get("source_name", name),
            anatomy=(
                _anatomical_entity_from_dict(anatomy)
                if anatomy is not None
                else None
            ),
        ))

    source = Path(source_path).resolve()
    if not source.is_file():
        raise ValueError("source file does not exist: {}".format(source))
    normalized = _local_path(array_uri)
    checksum = sha256_file(str(normalized)) if normalized and normalized.is_file() else None
    task = (
        _task_descriptor_from_dict(task_metadata)
        if task_metadata is not None
        else None
    )
    first_class_semantics = bool(
        task is not None or any(channel.anatomy is not None for channel in channels)
    )
    bundle = HarmoRecordingBundle(
        recording_id=recording_id,
        study_id=study_id,
        subject_id=subject_id,
        session_id=session_id,
        samples=ArrayReference(
            uri=array_uri,
            format=array_format,
            dtype=array_dtype,
            shape=[len(frame), len(channels)],
            checksum=checksum,
        ),
        timebase=Timebase(
            sampling_rate_hz=1.0 / period,
            sample_count=len(frame),
            start_seconds=times[0],
            timestamps_uri=timestamps_uri,
        ),
        channels=channels,
        sources=[SourceAsset(
            uri=source.as_uri(),
            sha256=sha256_file(str(source)),
            size_bytes=source.stat().st_size,
        )],
        warnings=warnings,
        task=task,
        schema_version=(
            RECORDING_SCHEMA_VERSION if first_class_semantics else "1.0.0"
        ),
    )
    return bundle, warnings


def _semantic_mapping_from_dict(value: Dict[str, Any]) -> SemanticMapping:
    return SemanticMapping(
        method=str(value["method"]),
        mapping_version=str(value["mapping_version"]),
        status=str(value["status"]),
        evidence_refs=[str(item) for item in value.get("evidence_refs", [])],
        confidence=(
            float(value["confidence"])
            if value.get("confidence") is not None
            else None
        ),
    )


def _ontology_term_from_dict(value: Dict[str, Any]) -> OntologyTerm:
    return OntologyTerm(
        ontology=str(value["ontology"]),
        ontology_version=str(value["ontology_version"]),
        term_id=str(value["term_id"]),
        label=str(value["label"]),
    )


def _anatomical_entity_from_dict(
    value: Dict[str, Any],
) -> AnatomicalEntity:
    return AnatomicalEntity(
        entity_type=str(value["entity_type"]),
        canonical_label=str(value["canonical_label"]),
        ontology_term=_ontology_term_from_dict(value["ontology_term"]),
        laterality=str(value["laterality"]),
        source_label=str(value["source_label"]),
        mapping=_semantic_mapping_from_dict(value["mapping"]),
    )


def _task_descriptor_from_dict(value: Dict[str, Any]) -> TaskDescriptor:
    return TaskDescriptor(
        activity=str(value["activity"]),
        source_label=str(value["source_label"]),
        ontology_terms=[
            _ontology_term_from_dict(item)
            for item in value.get("ontology_terms", [])
        ],
        facets={
            str(key): str(item) for key, item in value.get("facets", {}).items()
        },
        mapping=_semantic_mapping_from_dict(value["mapping"]),
        compatibility_group=(
            str(value["compatibility_group"])
            if value.get("compatibility_group") is not None
            else None
        ),
    )


def _local_path(uri: str) -> Optional[Path]:
    parsed = urlparse(uri)
    if parsed.scheme == "file":
        path = url2pathname(unquote(parsed.path))
        if parsed.netloc not in ("", "localhost"):
            path = "//{}{}".format(parsed.netloc, path)
        return Path(path).resolve()
    if not parsed.scheme or (
        len(parsed.scheme) == 1 and len(uri) > 1 and uri[1] == ":"
    ):
        return Path(uri).resolve()
    return None
