"""Versioned public contracts for scientifically auditable data products.

The contracts use only the Python standard library so a manifest can be
validated before optional Spark, OpenSim, or DuckDB dependencies are loaded.
"""

from __future__ import annotations

import dataclasses
import json
import math
import re
from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum
from typing import Any, Dict, List, Optional

SCHEMA_VERSION = "1.0.0"
TEMPORAL_RECORDING_SCHEMA_VERSION = "1.1.0"
RECORDING_SCHEMA_VERSION = "1.2.0"
COMPARABILITY_SCHEMA_VERSION = "1.1.0"
_SHA256_RE = re.compile(r"^[0-9a-f]{64}$")
_EVIDENCE_REF_RE = re.compile(r"^.+:sha256:[0-9a-f]{64}$")


class TrustStatus(str, Enum):
    PASS = "pass"
    WARN = "warn"
    FAIL = "fail"
    UNASSESSED = "unassessed"


class Comparability(str, Enum):
    COMPARABLE = "comparable"
    CONDITIONAL = "conditionally_comparable"
    NOT_COMPARABLE = "not_comparable"
    UNASSESSED = "unassessed"


class ReleaseStatus(str, Enum):
    ALLOW = "allow"
    CONDITIONAL = "conditional"
    QUARANTINE = "quarantine"
    DENY = "deny"


def _now() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def _jsonable(value: Any) -> Any:
    if dataclasses.is_dataclass(value):
        return {
            f.name: _jsonable(getattr(value, f.name)) for f in dataclasses.fields(value)
        }
    if isinstance(value, Enum):
        return value.value
    if isinstance(value, list):
        return [_jsonable(v) for v in value]
    if isinstance(value, dict):
        return {str(k): _jsonable(v) for k, v in value.items()}
    return value


class JsonContract:
    def to_dict(self) -> Dict[str, Any]:
        return _jsonable(self)

    def to_json(self, indent: int = 2) -> str:
        return (
            json.dumps(
                self.to_dict(), ensure_ascii=False, sort_keys=True, indent=indent
            )
            + "\n"
        )


@dataclass
class SourceAsset(JsonContract):
    uri: str
    sha256: str
    size_bytes: Optional[int] = None
    media_type: Optional[str] = None
    dataset_release: Optional[str] = None

    def __post_init__(self) -> None:
        if not self.uri:
            raise ValueError("source uri is required")
        if not _SHA256_RE.match(self.sha256):
            raise ValueError("sha256 must be 64 lowercase hexadecimal characters")
        if self.size_bytes is not None and self.size_bytes < 0:
            raise ValueError("size_bytes must be non-negative")


@dataclass
class Timebase(JsonContract):
    sampling_rate_hz: Optional[float]
    sample_count: int
    start_seconds: float = 0.0
    timestamps_uri: Optional[str] = None
    clock: str = "relative"

    def __post_init__(self) -> None:
        if self.sampling_rate_hz is not None and (
            not math.isfinite(self.sampling_rate_hz)
            or self.sampling_rate_hz <= 0
        ):
            raise ValueError(
                "sampling_rate_hz must be null or a finite positive value"
            )
        if not math.isfinite(self.start_seconds):
            raise ValueError("start_seconds must be finite")
        if self.sample_count < 1:
            raise ValueError("sample_count must be positive")
        if not self.clock:
            raise ValueError("timebase clock is required")


@dataclass
class ProcessingOperation(JsonContract):
    """One ordered, executable signal-processing operation."""

    operation: str
    parameters: Dict[str, Any] = field(default_factory=dict)
    software: str = "harmo"
    software_version: str = "unspecified"

    def __post_init__(self) -> None:
        if not all((self.operation, self.software, self.software_version)):
            raise ValueError(
                "processing operation, software, and software_version are required"
            )


@dataclass
class TemporalReference(JsonContract):
    """Scientific meaning of an array's first axis.

    A validated compatibility group is intentionally explicit.  Merely having
    101 samples does not prove that two arrays share the same gait-cycle
    reference.
    """

    axis: str
    unit: str
    start: float
    end: float
    sample_count: int
    landmark_start: str
    landmark_end: str
    side: str
    method: str
    compatibility_group: str
    validation_evidence_refs: List[str]

    def __post_init__(self) -> None:
        if self.axis not in (
            "elapsed-seconds",
            "sample-index",
            "gait-cycle-percent",
        ):
            raise ValueError("unsupported temporal axis: {}".format(self.axis))
        if not all(
            (
                self.unit,
                self.landmark_start,
                self.landmark_end,
                self.method,
                self.compatibility_group,
            )
        ):
            raise ValueError("temporal reference fields are required")
        if self.side not in ("left", "right", "midline"):
            raise ValueError("temporal reference side must be left, right, or midline")
        if (
            not math.isfinite(self.start)
            or not math.isfinite(self.end)
            or self.end <= self.start
        ):
            raise ValueError("temporal reference bounds must be finite and increasing")
        if self.sample_count < 2:
            raise ValueError("temporal reference requires at least two samples")
        if not self.validation_evidence_refs or any(
            not _EVIDENCE_REF_RE.match(value)
            for value in self.validation_evidence_refs
        ):
            raise ValueError(
                "temporal validation evidence must be SHA-256-bound references"
            )
        if self.axis == "gait-cycle-percent" and (
            self.unit != "%gait-cycle"
            or not math.isclose(self.start, 0.0, abs_tol=1e-12)
            or not math.isclose(self.end, 100.0, abs_tol=1e-12)
        ):
            raise ValueError(
                "gait-cycle-percent requires a 0..100 %gait-cycle axis"
            )


@dataclass
class SignalProcessingState(JsonContract):
    """Ordered processing state and its intended-use compatibility boundary."""

    representation: str
    operations: List[ProcessingOperation]
    amplitude_scale: str
    amplitude_comparability: str
    timing_compatibility_group: str
    validation_evidence_refs: List[str]
    source_recording_sha256: str

    def __post_init__(self) -> None:
        if not all(
            (
                self.representation,
                self.amplitude_scale,
                self.timing_compatibility_group,
            )
        ):
            raise ValueError("signal processing state fields are required")
        if not self.operations:
            raise ValueError("signal processing state requires ordered operations")
        if self.amplitude_comparability not in (
            "not-established",
            "within-recording-only",
            "cross-recording-validated",
        ):
            raise ValueError(
                "unsupported amplitude comparability: {}".format(
                    self.amplitude_comparability
                )
            )
        if not self.validation_evidence_refs or any(
            not _EVIDENCE_REF_RE.match(value)
            for value in self.validation_evidence_refs
        ):
            raise ValueError(
                "processing validation evidence must be SHA-256-bound references"
            )
        if not _SHA256_RE.match(self.source_recording_sha256):
            raise ValueError(
                "source_recording_sha256 must be a SHA-256 digest"
            )


@dataclass
class OntologyTerm(JsonContract):
    """A versioned external or project vocabulary term."""

    ontology: str
    ontology_version: str
    term_id: str
    label: str

    def __post_init__(self) -> None:
        if not all(
            (self.ontology, self.ontology_version, self.term_id, self.label)
        ):
            raise ValueError("ontology, version, term_id, and label are required")


@dataclass
class SemanticMapping(JsonContract):
    """Traceable assertion connecting a source label to a canonical concept."""

    method: str
    mapping_version: str
    status: str
    evidence_refs: List[str]
    confidence: Optional[float] = None

    def __post_init__(self) -> None:
        if not self.method or not self.mapping_version:
            raise ValueError("mapping method and version are required")
        if self.status not in ("mapped", "provisional", "unmapped"):
            raise ValueError(
                "mapping status must be mapped, provisional, or unmapped"
            )
        if self.confidence is not None and (
            not math.isfinite(self.confidence)
            or self.confidence < 0.0
            or self.confidence > 1.0
        ):
            raise ValueError("mapping confidence must be between zero and one")
        if self.status == "mapped" and (
            not self.evidence_refs
            or any(
                not _EVIDENCE_REF_RE.match(value)
                for value in self.evidence_refs
            )
        ):
            raise ValueError(
                "mapped semantics require SHA-256-bound evidence references"
            )
        if self.evidence_refs and any(
            not _EVIDENCE_REF_RE.match(value) for value in self.evidence_refs
        ):
            raise ValueError(
                "semantic mapping evidence must be SHA-256-bound references"
            )


@dataclass
class AnatomicalEntity(JsonContract):
    """Canonical anatomy plus the evidence used to map the source label."""

    entity_type: str
    canonical_label: str
    ontology_term: OntologyTerm
    laterality: str
    source_label: str
    mapping: SemanticMapping

    def __post_init__(self) -> None:
        if self.entity_type not in (
            "muscle",
            "muscle-group",
            "body-region",
            "anatomical-landmark",
            "bone",
            "joint",
            "model-body",
        ):
            raise ValueError(
                "unsupported anatomical entity type: {}".format(self.entity_type)
            )
        if not self.canonical_label or not self.source_label:
            raise ValueError(
                "canonical and source anatomical labels are required"
            )
        if self.laterality not in (
            "left",
            "right",
            "bilateral",
            "midline",
            "not-applicable",
            "unknown",
        ):
            raise ValueError("unsupported anatomical laterality")


@dataclass
class TaskDescriptor(JsonContract):
    """Recording-level task semantics, independent of file naming."""

    activity: str
    source_label: str
    ontology_terms: List[OntologyTerm]
    facets: Dict[str, str]
    mapping: SemanticMapping
    compatibility_group: Optional[str] = None

    def __post_init__(self) -> None:
        if not self.activity or not self.source_label:
            raise ValueError("task activity and source label are required")
        if not self.ontology_terms:
            raise ValueError("task requires at least one ontology term")
        if any(
            not isinstance(key, str)
            or not key
            or not isinstance(value, str)
            or not value
            for key, value in self.facets.items()
        ):
            raise ValueError("task facets must map non-empty strings to strings")
        if self.compatibility_group is not None and not self.compatibility_group:
            raise ValueError("task compatibility_group cannot be empty")


@dataclass
class ChannelDescriptor(JsonContract):
    channel_id: str
    name: str
    unit: str
    sensor_type: str
    muscle: Optional[str] = None
    side: Optional[str] = None
    coordinates: Optional[Dict[str, float]] = None
    source_name: Optional[str] = None
    anatomy: Optional[AnatomicalEntity] = None

    def __post_init__(self) -> None:
        if not all((self.channel_id, self.name, self.unit, self.sensor_type)):
            raise ValueError("channel_id, name, unit, and sensor_type are required")
        if self.side not in (None, "left", "right", "midline", "unknown"):
            raise ValueError("side must be left, right, midline, unknown, or null")
        if self.anatomy is not None:
            if (
                self.side not in (None, "unknown")
                and self.anatomy.laterality not in ("unknown", "not-applicable")
                and self.side != self.anatomy.laterality
            ):
                raise ValueError(
                    "legacy channel side conflicts with anatomical laterality"
                )
            if self.muscle and self.anatomy.entity_type == "muscle":
                legacy = re.sub(r"[-_ ]+", "", self.muscle).lower()
                canonical = re.sub(
                    r"[-_ ]+", "", self.anatomy.canonical_label
                ).lower()
                if legacy != canonical:
                    raise ValueError(
                        "legacy muscle conflicts with canonical anatomy"
                    )


@dataclass
class EventDescriptor(JsonContract):
    event_id: str
    onset_seconds: float
    label: str
    duration_seconds: float = 0.0
    source_label: Optional[str] = None
    hed_tags: List[str] = field(default_factory=list)

    def __post_init__(self) -> None:
        if not self.event_id or not self.label:
            raise ValueError("event_id and label are required")
        if (
            not math.isfinite(self.onset_seconds)
            or not math.isfinite(self.duration_seconds)
            or self.onset_seconds < 0
            or self.duration_seconds < 0
        ):
            raise ValueError("event onset and duration must be finite and non-negative")


@dataclass
class ArrayReference(JsonContract):
    uri: str
    format: str
    dtype: str
    shape: List[int]
    checksum: Optional[str] = None

    def __post_init__(self) -> None:
        if not self.uri or not self.format or not self.dtype:
            raise ValueError("array uri, format, and dtype are required")
        if len(self.shape) != 2 or any(v < 1 for v in self.shape):
            raise ValueError("array shape must be [sample_count, channel_count]")
        if self.checksum is not None and not _SHA256_RE.match(self.checksum):
            raise ValueError("array checksum must be a SHA-256 digest")


@dataclass
class TransformationWarning(JsonContract):
    code: str
    severity: str
    message: str
    field: Optional[str] = None
    evidence: Optional[str] = None
    dimension: str = "semantic_fidelity"

    def __post_init__(self) -> None:
        if self.severity not in ("info", "warning", "error"):
            raise ValueError("warning severity must be info, warning, or error")
        if self.dimension not in {
            "semantic_fidelity",
            "scientific_validity",
            "computational_reproducibility",
            "traceability",
        }:
            raise ValueError("unknown warning dimension: {}".format(self.dimension))


@dataclass
class HarmoRecordingBundle(JsonContract):
    recording_id: str
    study_id: str
    subject_id: str
    session_id: str
    samples: ArrayReference
    timebase: Timebase
    channels: List[ChannelDescriptor]
    sources: List[SourceAsset]
    events: List[EventDescriptor] = field(default_factory=list)
    warnings: List[TransformationWarning] = field(default_factory=list)
    temporal_reference: Optional[TemporalReference] = None
    processing_state: Optional[SignalProcessingState] = None
    task: Optional[TaskDescriptor] = None
    schema_version: str = SCHEMA_VERSION

    def __post_init__(self) -> None:
        if not all(
            (self.recording_id, self.study_id, self.subject_id, self.session_id)
        ):
            raise ValueError("recording, study, subject, and session IDs are required")
        ids = [c.channel_id for c in self.channels]
        if not ids or len(ids) != len(set(ids)):
            raise ValueError("channel IDs must be present and unique")
        if self.samples.shape != [self.timebase.sample_count, len(self.channels)]:
            raise ValueError("array shape must match timebase and channel count")
        if not self.sources:
            raise ValueError("at least one source asset is required")
        event_ids = [event.event_id for event in self.events]
        if len(event_ids) != len(set(event_ids)):
            raise ValueError("event IDs must be unique")
        if self.schema_version not in (
            SCHEMA_VERSION,
            TEMPORAL_RECORDING_SCHEMA_VERSION,
            RECORDING_SCHEMA_VERSION,
        ):
            raise ValueError(
                "unsupported recording schema version: {}".format(self.schema_version)
            )
        if self.schema_version == SCHEMA_VERSION and (
            self.temporal_reference is not None
            or self.processing_state is not None
        ):
            raise ValueError(
                "temporal_reference and processing_state require recording schema 1.1.0"
            )
        if self.schema_version in (
            SCHEMA_VERSION,
            TEMPORAL_RECORDING_SCHEMA_VERSION,
        ) and (
            self.task is not None
            or any(channel.anatomy is not None for channel in self.channels)
        ):
            raise ValueError(
                "task and channel anatomy require recording schema 1.2.0"
            )
        if self.timebase.sampling_rate_hz is None:
            if (
                self.temporal_reference is None
                or self.temporal_reference.axis != "gait-cycle-percent"
            ):
                raise ValueError(
                    "null sampling_rate_hz requires a declared normalized temporal reference"
                )
        if (
            self.temporal_reference is not None
            and self.temporal_reference.sample_count != self.timebase.sample_count
        ):
            raise ValueError(
                "temporal reference sample_count must match the recording timebase"
            )

    def to_dict(self) -> Dict[str, Any]:
        payload = super().to_dict()
        if self.schema_version == SCHEMA_VERSION:
            payload.pop("temporal_reference", None)
            payload.pop("processing_state", None)
        if self.schema_version in (
            SCHEMA_VERSION,
            TEMPORAL_RECORDING_SCHEMA_VERSION,
        ):
            payload.pop("task", None)
            for channel in payload["channels"]:
                channel.pop("anatomy", None)
        return payload

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "HarmoRecordingBundle":
        temporal = data.get("temporal_reference")
        processing = data.get("processing_state")
        task = data.get("task")
        channels = []
        for value in data["channels"]:
            channel = dict(value)
            anatomy = channel.get("anatomy")
            if anatomy is not None:
                anatomy = dict(anatomy)
                anatomy["ontology_term"] = OntologyTerm(
                    **anatomy["ontology_term"]
                )
                anatomy["mapping"] = SemanticMapping(**anatomy["mapping"])
                channel["anatomy"] = AnatomicalEntity(**anatomy)
            channels.append(ChannelDescriptor(**channel))
        return cls(
            recording_id=data["recording_id"],
            study_id=str(data["study_id"]),
            subject_id=str(data["subject_id"]),
            session_id=str(data["session_id"]),
            samples=ArrayReference(**data["samples"]),
            timebase=Timebase(**data["timebase"]),
            channels=channels,
            sources=[SourceAsset(**v) for v in data["sources"]],
            events=[EventDescriptor(**v) for v in data.get("events", [])],
            warnings=[TransformationWarning(**v) for v in data.get("warnings", [])],
            temporal_reference=(
                TemporalReference(**temporal) if temporal is not None else None
            ),
            processing_state=(
                SignalProcessingState(
                    representation=processing["representation"],
                    operations=[
                        ProcessingOperation(**value)
                        for value in processing.get("operations", [])
                    ],
                    amplitude_scale=processing["amplitude_scale"],
                    amplitude_comparability=processing[
                        "amplitude_comparability"
                    ],
                    timing_compatibility_group=processing[
                        "timing_compatibility_group"
                    ],
                    validation_evidence_refs=[
                        str(value)
                        for value in processing.get(
                            "validation_evidence_refs", []
                        )
                    ],
                    source_recording_sha256=processing[
                        "source_recording_sha256"
                    ],
                )
                if processing is not None
                else None
            ),
            task=(
                TaskDescriptor(
                    activity=task["activity"],
                    source_label=task["source_label"],
                    ontology_terms=[
                        OntologyTerm(**value)
                        for value in task.get("ontology_terms", [])
                    ],
                    facets={
                        str(key): str(value)
                        for key, value in task.get("facets", {}).items()
                    },
                    mapping=SemanticMapping(**task["mapping"]),
                    compatibility_group=task.get("compatibility_group"),
                )
                if task is not None
                else None
            ),
            schema_version=data.get("schema_version", SCHEMA_VERSION),
        )


@dataclass
class ArtifactReference(JsonContract):
    artifact_id: str
    uri: str
    sha256: Optional[str] = None
    media_type: Optional[str] = None
    digest_kind: str = "sha256-file"

    def __post_init__(self) -> None:
        if not self.artifact_id or not self.uri:
            raise ValueError("artifact_id and uri are required")
        if self.sha256 is not None and not _SHA256_RE.match(self.sha256):
            raise ValueError("artifact sha256 must be a SHA-256 digest")
        if self.digest_kind not in ("sha256-file", "sha256-canonical-json"):
            raise ValueError(
                "unknown artifact digest_kind: {}".format(self.digest_kind)
            )


@dataclass
class TransformationStep(JsonContract):
    step_id: str
    name: str
    software: str
    software_version: str
    parameters: Dict[str, Any] = field(default_factory=dict)
    inputs: List[str] = field(default_factory=list)
    outputs: List[str] = field(default_factory=list)
    started_at: Optional[str] = None
    completed_at: Optional[str] = None


@dataclass
class EvidenceManifest(JsonContract):
    run_id: str
    inputs: List[ArtifactReference]
    outputs: List[ArtifactReference]
    steps: List[TransformationStep]
    code_commit: str
    config_sha256: str
    container_digest: Optional[str] = None
    code_dirty: Optional[bool] = None
    working_tree_sha256: Optional[str] = None
    environment: Dict[str, str] = field(default_factory=dict)
    created_at: str = field(default_factory=_now)
    schema_version: str = SCHEMA_VERSION

    def __post_init__(self) -> None:
        if not self.run_id or not self.code_commit:
            raise ValueError("run_id and code_commit are required")
        if not _SHA256_RE.match(self.config_sha256):
            raise ValueError("config_sha256 must be a SHA-256 digest")
        if self.working_tree_sha256 is not None and not _SHA256_RE.match(
            self.working_tree_sha256
        ):
            raise ValueError("working_tree_sha256 must be a SHA-256 digest")
        artifact_ids = [a.artifact_id for a in self.inputs + self.outputs]
        if len(artifact_ids) != len(set(artifact_ids)):
            raise ValueError("artifact IDs must be unique within a manifest")
        step_ids = [step.step_id for step in self.steps]
        if any(not step_id for step_id in step_ids) or len(step_ids) != len(
            set(step_ids)
        ):
            raise ValueError("transformation step IDs must be present and unique")
        known_artifacts = set(artifact_ids)
        referenced_artifacts = {
            artifact_id
            for step in self.steps
            for artifact_id in step.inputs + step.outputs
        }
        unknown_artifacts = sorted(referenced_artifacts - known_artifacts)
        if unknown_artifacts:
            raise ValueError(
                "transformation steps reference unknown artifacts: {}".format(
                    unknown_artifacts
                )
            )
        produced = [artifact_id for step in self.steps for artifact_id in step.outputs]
        if len(produced) != len(set(produced)):
            raise ValueError(
                "an artifact may be produced by at most one transformation step"
            )
        unproduced_outputs = sorted(
            {artifact.artifact_id for artifact in self.outputs} - set(produced)
        )
        if unproduced_outputs:
            raise ValueError(
                "manifest outputs lack a producing transformation step: {}".format(
                    unproduced_outputs
                )
            )
        if self.schema_version != SCHEMA_VERSION:
            raise ValueError(
                "unsupported manifest schema version: {}".format(self.schema_version)
            )

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "EvidenceManifest":
        return cls(
            run_id=data["run_id"],
            inputs=[ArtifactReference(**v) for v in data.get("inputs", [])],
            outputs=[ArtifactReference(**v) for v in data.get("outputs", [])],
            steps=[TransformationStep(**v) for v in data.get("steps", [])],
            code_commit=data["code_commit"],
            config_sha256=data["config_sha256"],
            container_digest=data.get("container_digest"),
            code_dirty=data.get("code_dirty"),
            working_tree_sha256=data.get("working_tree_sha256"),
            environment={
                str(k): str(v) for k, v in data.get("environment", {}).items()
            },
            created_at=data.get("created_at", _now()),
            schema_version=data.get("schema_version", SCHEMA_VERSION),
        )


@dataclass
class TrustEvidence(JsonContract):
    dimension: str
    status: TrustStatus
    rule_id: str
    message: str
    metric: Optional[float] = None
    threshold: Optional[float] = None
    artifact_id: Optional[str] = None

    def __post_init__(self) -> None:
        allowed = {
            "semantic_fidelity",
            "scientific_validity",
            "computational_reproducibility",
            "traceability",
        }
        if self.dimension not in allowed:
            raise ValueError("unknown trust dimension: {}".format(self.dimension))
        self.status = TrustStatus(self.status)
        if self.metric is not None and not math.isfinite(self.metric):
            raise ValueError("trust metric must be finite")
        if self.threshold is not None and not math.isfinite(self.threshold):
            raise ValueError("trust threshold must be finite")


@dataclass
class TrustReport(JsonContract):
    subject_id: str
    evidence: List[TrustEvidence]
    generated_at: str = field(default_factory=_now)
    schema_version: str = SCHEMA_VERSION

    def __post_init__(self) -> None:
        if not self.subject_id:
            raise ValueError("trust report subject_id is required")
        if self.schema_version != SCHEMA_VERSION:
            raise ValueError(
                "unsupported trust schema version: {}".format(self.schema_version)
            )
        evidence_ids = [
            (item.dimension, item.rule_id, item.artifact_id) for item in self.evidence
        ]
        if len(evidence_ids) != len(set(evidence_ids)):
            raise ValueError(
                "trust evidence rule IDs must be unique per artifact and dimension"
            )

    def dimension_status(self, dimension: str) -> TrustStatus:
        states = [e.status for e in self.evidence if e.dimension == dimension]
        if not states:
            return TrustStatus.UNASSESSED
        for state in (TrustStatus.FAIL, TrustStatus.WARN, TrustStatus.UNASSESSED):
            if state in states:
                return state
        return TrustStatus.PASS

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "TrustReport":
        return cls(
            subject_id=data["subject_id"],
            evidence=[TrustEvidence(**v) for v in data.get("evidence", [])],
            generated_at=data.get("generated_at", _now()),
            schema_version=data.get("schema_version", SCHEMA_VERSION),
        )


@dataclass
class ComparabilityAssertion(JsonContract):
    left_id: str
    right_id: str
    variable: str
    status: Comparability
    rationale: str
    conditions: List[str] = field(default_factory=list)
    intended_use: str = "unspecified"
    left_channel_id: Optional[str] = None
    right_channel_id: Optional[str] = None
    evidence_refs: List[str] = field(default_factory=list)
    rule_version: str = SCHEMA_VERSION
    schema_version: str = COMPARABILITY_SCHEMA_VERSION

    def __post_init__(self) -> None:
        self.status = Comparability(self.status)
        if self.left_id == self.right_id:
            raise ValueError("comparability requires two different resources")
        if not all(
            (
                self.left_id,
                self.right_id,
                self.variable,
                self.rationale,
                self.intended_use,
            )
        ):
            raise ValueError("comparability IDs, variable, and rationale are required")
        if (self.left_channel_id is None) != (self.right_channel_id is None):
            raise ValueError(
                "comparability channel IDs must either both be present or both be null"
            )
        if any(not value for value in self.conditions + self.evidence_refs):
            raise ValueError("comparability conditions and evidence refs cannot be empty")
        if not self.rule_version:
            raise ValueError("comparability rule_version is required")
        if self.schema_version not in (SCHEMA_VERSION, COMPARABILITY_SCHEMA_VERSION):
            raise ValueError(
                "unsupported comparability schema version: {}".format(
                    self.schema_version
                )
            )

    def to_dict(self) -> Dict[str, Any]:
        payload = super().to_dict()
        if self.schema_version == SCHEMA_VERSION:
            for key in (
                "intended_use",
                "left_channel_id",
                "right_channel_id",
                "evidence_refs",
            ):
                payload.pop(key, None)
        return payload

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "ComparabilityAssertion":
        return cls(
            left_id=data["left_id"],
            right_id=data["right_id"],
            variable=data["variable"],
            status=Comparability(data["status"]),
            rationale=data["rationale"],
            conditions=[str(value) for value in data.get("conditions", [])],
            intended_use=data.get("intended_use", "unspecified"),
            left_channel_id=data.get("left_channel_id"),
            right_channel_id=data.get("right_channel_id"),
            evidence_refs=[
                str(value) for value in data.get("evidence_refs", [])
            ],
            rule_version=data.get("rule_version", SCHEMA_VERSION),
            schema_version=data.get("schema_version", SCHEMA_VERSION),
        )


@dataclass
class ReleaseDecision(JsonContract):
    decision_id: str
    subject_id: str
    intended_use: str
    status: ReleaseStatus
    policy_id: str
    policy_version: str
    recording_sha256: str
    manifest_sha256: str
    trust_report_sha256: str
    comparability_assertion_sha256: str
    required_dimensions: List[str]
    reasons: List[str]
    conditions: List[str] = field(default_factory=list)
    decided_at: str = field(default_factory=_now)
    schema_version: str = SCHEMA_VERSION

    def __post_init__(self) -> None:
        self.status = ReleaseStatus(self.status)
        if not all(
            (
                self.decision_id,
                self.subject_id,
                self.intended_use,
                self.policy_id,
                self.policy_version,
            )
        ):
            raise ValueError(
                "release decision IDs, intended use, and policy are required"
            )
        for name, digest in (
            ("recording_sha256", self.recording_sha256),
            ("manifest_sha256", self.manifest_sha256),
            ("trust_report_sha256", self.trust_report_sha256),
            (
                "comparability_assertion_sha256",
                self.comparability_assertion_sha256,
            ),
        ):
            if not _SHA256_RE.match(digest):
                raise ValueError("{} must be a SHA-256 digest".format(name))
        allowed_dimensions = {
            "semantic_fidelity",
            "scientific_validity",
            "computational_reproducibility",
            "traceability",
        }
        if (
            not self.required_dimensions
            or len(self.required_dimensions) != len(set(self.required_dimensions))
            or any(value not in allowed_dimensions for value in self.required_dimensions)
        ):
            raise ValueError(
                "required_dimensions must contain unique known trust dimensions"
            )
        if not self.reasons or any(not value for value in self.reasons):
            raise ValueError("release decision requires non-empty reasons")
        if any(not value for value in self.conditions):
            raise ValueError("release decision conditions cannot be empty")
        if self.schema_version != SCHEMA_VERSION:
            raise ValueError(
                "unsupported release decision schema version: {}".format(
                    self.schema_version
                )
            )

    @property
    def export_permitted(self) -> bool:
        return self.status in (ReleaseStatus.ALLOW, ReleaseStatus.CONDITIONAL)

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "ReleaseDecision":
        return cls(
            decision_id=data["decision_id"],
            subject_id=data["subject_id"],
            intended_use=data["intended_use"],
            status=ReleaseStatus(data["status"]),
            policy_id=data["policy_id"],
            policy_version=data["policy_version"],
            recording_sha256=data["recording_sha256"],
            manifest_sha256=data["manifest_sha256"],
            trust_report_sha256=data["trust_report_sha256"],
            comparability_assertion_sha256=data[
                "comparability_assertion_sha256"
            ],
            required_dimensions=[
                str(value) for value in data["required_dimensions"]
            ],
            reasons=[str(value) for value in data["reasons"]],
            conditions=[str(value) for value in data.get("conditions", [])],
            decided_at=data.get("decided_at", _now()),
            schema_version=data.get("schema_version", SCHEMA_VERSION),
        )


@dataclass
class OpenSimArtifact(JsonContract):
    artifact_id: str
    recording_id: str
    model: ArtifactReference
    outputs: List[ArtifactReference]
    ik_rms_error_m: Optional[float] = None
    ik_max_error_m: Optional[float] = None
    residual_force_fraction_peak: Optional[float] = None
    residual_force_fraction_rms: Optional[float] = None
    residual_moment_fraction_peak: Optional[float] = None
    residual_moment_fraction_rms: Optional[float] = None
    rra_angle_change_rms_deg: Optional[float] = None
    manifest_run_id: Optional[str] = None
    schema_version: str = SCHEMA_VERSION

    def __post_init__(self) -> None:
        if not self.artifact_id or not self.recording_id:
            raise ValueError("OpenSim artifact and recording IDs are required")
        references = [self.model] + self.outputs
        reference_ids = [reference.artifact_id for reference in references]
        if len(reference_ids) != len(set(reference_ids)):
            raise ValueError("OpenSim reference artifact IDs must be unique")
        if any(reference.sha256 is None for reference in references):
            raise ValueError(
                "OpenSim model and output references require SHA-256 digests"
            )
        if self.manifest_run_id is not None and (
            not isinstance(self.manifest_run_id, str) or not self.manifest_run_id
        ):
            raise ValueError("OpenSim manifest_run_id must be a non-empty string")
        metrics = (
            self.ik_rms_error_m,
            self.ik_max_error_m,
            self.residual_force_fraction_peak,
            self.residual_force_fraction_rms,
            self.residual_moment_fraction_peak,
            self.residual_moment_fraction_rms,
            self.rra_angle_change_rms_deg,
        )
        if any(
            value is not None and (not math.isfinite(value) or value < 0)
            for value in metrics
        ):
            raise ValueError("OpenSim quality metrics must be finite and non-negative")
        if self.schema_version != SCHEMA_VERSION:
            raise ValueError(
                "unsupported OpenSim schema version: {}".format(self.schema_version)
            )

    def trust_evidence(self) -> List[TrustEvidence]:
        checks = [
            ("opensim.ik_rms", self.ik_rms_error_m, 0.02),
            ("opensim.ik_max", self.ik_max_error_m, 0.04),
            ("opensim.residual_force_peak", self.residual_force_fraction_peak, 0.05),
            ("opensim.residual_force_rms", self.residual_force_fraction_rms, 0.05),
            ("opensim.residual_moment_peak", self.residual_moment_fraction_peak, 0.01),
            ("opensim.residual_moment_rms", self.residual_moment_fraction_rms, 0.01),
            ("opensim.rra_angle_change", self.rra_angle_change_rms_deg, 5.0),
        ]
        result = []
        for rule, value, limit in checks:
            status = (
                TrustStatus.UNASSESSED
                if value is None
                else (TrustStatus.PASS if value <= limit else TrustStatus.FAIL)
            )
            result.append(
                TrustEvidence(
                    dimension="scientific_validity",
                    status=status,
                    rule_id=rule,
                    message="not assessed"
                    if value is None
                    else "{} <= {}".format(value, limit),
                    metric=value,
                    threshold=limit,
                    artifact_id=self.artifact_id,
                )
            )
        result.append(
            TrustEvidence(
                dimension="traceability",
                status=TrustStatus.PASS,
                rule_id="opensim.reference_hashes",
                message="all model and output references have SHA-256 digests",
                artifact_id=self.artifact_id,
            )
        )
        result.append(
            TrustEvidence(
                dimension="computational_reproducibility",
                status=(
                    TrustStatus.PASS if self.manifest_run_id else TrustStatus.UNASSESSED
                ),
                rule_id="opensim.manifest_run",
                message=(
                    "OpenSim artifact links to run manifest {}".format(
                        self.manifest_run_id
                    )
                    if self.manifest_run_id
                    else "OpenSim artifact does not identify its run manifest"
                ),
                artifact_id=self.artifact_id,
            )
        )
        return result
