"""Scientific and semantic validation that produces explicit trust evidence."""
from __future__ import annotations

import math
from dataclasses import dataclass, field
from typing import Dict, List, Optional, Sequence

from .models import HarmoRecordingBundle, TrustEvidence, TrustReport, TrustStatus


@dataclass
class RecordingProfile:
    """Study-independent expectations; study-specific values belong in config."""

    sampling_rate_min_hz: Optional[float] = None
    sampling_rate_max_hz: Optional[float] = None
    units_by_sensor_type: Dict[str, List[str]] = field(default_factory=dict)
    required_channel_names: List[str] = field(default_factory=list)
    require_array_checksum: bool = True
    expected_sampling_rate_hz: Optional[float] = None
    expected_start_seconds: Optional[float] = None
    time_tolerance_samples: float = 0.5
    expected_sides_by_channel: Dict[str, str] = field(default_factory=dict)
    expected_muscles_by_channel: Dict[str, str] = field(default_factory=dict)
    expected_coordinates_by_channel: Dict[str, Dict[str, float]] = field(
        default_factory=dict
    )


def _evidence(rule: str, ok: bool, message: str, dimension: str = "semantic_fidelity", metric=None, threshold=None) -> TrustEvidence:
    return TrustEvidence(
        dimension=dimension,
        status=TrustStatus.PASS if ok else TrustStatus.FAIL,
        rule_id=rule,
        message=message,
        metric=metric,
        threshold=threshold,
    )


def assess_bundle(bundle: HarmoRecordingBundle, profile: RecordingProfile) -> TrustReport:
    evidence = []  # type: List[TrustEvidence]
    fs = bundle.timebase.sampling_rate_hz
    if profile.sampling_rate_min_hz is not None:
        evidence.append(_evidence("timebase.fs.min", fs >= profile.sampling_rate_min_hz, "sampling rate lower bound", metric=fs, threshold=profile.sampling_rate_min_hz))
    if profile.sampling_rate_max_hz is not None:
        evidence.append(_evidence("timebase.fs.max", fs <= profile.sampling_rate_max_hz, "sampling rate upper bound", metric=fs, threshold=profile.sampling_rate_max_hz))
    if profile.expected_sampling_rate_hz is not None:
        expected_fs = profile.expected_sampling_rate_hz
        evidence.append(_evidence(
            "timebase.fs.expected",
            math.isclose(fs, expected_fs, rel_tol=0.0, abs_tol=1e-12),
            "sampling rate must match declared source timebase",
            metric=fs,
            threshold=expected_fs,
        ))
    if profile.expected_start_seconds is not None:
        tolerance_seconds = profile.time_tolerance_samples / fs
        start_error = abs(bundle.timebase.start_seconds - profile.expected_start_seconds)
        evidence.append(_evidence(
            "timebase.start.expected",
            start_error <= tolerance_seconds,
            "start time disagreement must be within {} source samples".format(
                profile.time_tolerance_samples
            ),
            metric=start_error,
            threshold=tolerance_seconds,
        ))

    for channel in bundle.channels:
        allowed = profile.units_by_sensor_type.get(channel.sensor_type)
        if allowed:
            evidence.append(_evidence(
                "channel.unit.{}".format(channel.channel_id), channel.unit in allowed,
                "unit {} must be one of {}".format(channel.unit, allowed),
            ))
        expected_side = profile.expected_sides_by_channel.get(channel.channel_id)
        if expected_side is not None:
            evidence.append(_evidence(
                "channel.side.{}".format(channel.channel_id),
                channel.side == expected_side,
                "side {} must match declared side {}".format(
                    channel.side, expected_side
                ),
            ))
        expected_muscle = profile.expected_muscles_by_channel.get(channel.channel_id)
        if expected_muscle is not None:
            evidence.append(_evidence(
                "channel.muscle.{}".format(channel.channel_id),
                channel.muscle == expected_muscle,
                "muscle {} must match declared muscle {}".format(
                    channel.muscle, expected_muscle
                ),
            ))
        expected_coordinates = profile.expected_coordinates_by_channel.get(
            channel.channel_id
        )
        if expected_coordinates is not None:
            actual_coordinates = channel.coordinates or {}
            coordinate_match = set(actual_coordinates) == set(expected_coordinates)
            if coordinate_match:
                coordinate_match = all(
                    math.isclose(
                        float(actual_coordinates[key]),
                        float(expected_coordinates[key]),
                        rel_tol=0.0,
                        abs_tol=1e-12,
                    )
                    for key in expected_coordinates
                )
            evidence.append(_evidence(
                "channel.coordinates.{}".format(channel.channel_id),
                coordinate_match,
                "coordinates must match the declared coordinate frame",
            ))
        if bundle.schema_version == "1.2.0" and "emg" in (
            channel.sensor_type.lower().replace("-", "")
        ):
            anatomy = channel.anatomy
            evidence.append(_evidence(
                "channel.anatomy.{}".format(channel.channel_id),
                bool(
                    anatomy is not None
                    and anatomy.entity_type == "muscle"
                    and anatomy.mapping.status == "mapped"
                    and anatomy.mapping.evidence_refs
                ),
                "EMG anatomy must be a checksum-supported mapped muscle",
            ))

    if bundle.schema_version == "1.2.0":
        task = bundle.task
        evidence.append(_evidence(
            "recording.task",
            bool(
                task is not None
                and task.mapping.status == "mapped"
                and task.mapping.evidence_refs
                and task.ontology_terms
            ),
            "recording task must have checksum-supported mapped ontology terms",
        ))

    names = {channel.name for channel in bundle.channels}
    for required in profile.required_channel_names:
        evidence.append(_evidence(
            "channel.required.{}".format(required), required in names,
            "required channel {}".format(required),
        ))

    has_checksum = bool(bundle.samples.checksum)
    evidence.append(TrustEvidence(
        dimension="traceability",
        status=(TrustStatus.PASS if has_checksum else (TrustStatus.FAIL if profile.require_array_checksum else TrustStatus.WARN)),
        rule_id="array.checksum", message="normalized array checksum is recorded" if has_checksum else "normalized array checksum is missing",
    ))
    evidence.append(_evidence(
        "source.checksums", all(bool(source.sha256) for source in bundle.sources),
        "all source assets have checksums", dimension="traceability",
    ))
    for warning in bundle.warnings:
        if warning.severity in ("warning", "error"):
            evidence.append(TrustEvidence(
                dimension=warning.dimension,
                status=(TrustStatus.FAIL if warning.severity == "error" else TrustStatus.WARN),
                rule_id="transformation.{}".format(warning.code),
                message=warning.message,
            ))
    return TrustReport(subject_id=bundle.recording_id, evidence=evidence)


def compare_numeric_series(expected: Sequence[float], actual: Sequence[float]) -> Dict[str, float]:
    if len(expected) != len(actual) or not expected:
        raise ValueError("numeric series must have the same non-zero length")
    left = [float(v) for v in expected]
    right = [float(v) for v in actual]
    if not all(math.isfinite(v) for v in left + right):
        raise ValueError("numeric series must contain only finite values")
    errors = [a - b for a, b in zip(left, right)]
    rmse = math.sqrt(sum(v * v for v in errors) / len(errors))
    span = max(left) - min(left)
    mean_left = sum(left) / len(left)
    mean_right = sum(right) / len(right)
    covariance = sum((a - mean_left) * (b - mean_right) for a, b in zip(left, right))
    variance_left = sum((a - mean_left) ** 2 for a in left)
    variance_right = sum((b - mean_right) ** 2 for b in right)
    correlation = covariance / math.sqrt(variance_left * variance_right) if variance_left and variance_right else (1.0 if left == right else 0.0)
    return {
        "sample_count": float(len(left)),
        "rmse": rmse,
        "nrmse": rmse / span if span else (0.0 if rmse == 0 else math.inf),
        "max_absolute_error": max(abs(v) for v in errors),
        "pearson_correlation": correlation,
    }
