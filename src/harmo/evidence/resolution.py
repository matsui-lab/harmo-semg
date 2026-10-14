"""Execute and attest intended-use comparability resolution.

The resolver is deliberately narrow: it creates a validated common gait-cycle
phase representation for activation-timing analysis.  It never promotes
amplitude comparability, and it never overwrites source recording contracts.
"""
from __future__ import annotations

import os
import tempfile
from pathlib import Path
from typing import Any, Dict, List, Mapping, Optional, Sequence, Tuple

import numpy as np
import pandas as pd
import scipy
from jsonschema import Draft202012Validator
from scipy import signal

from harmo import __version__

from .audit import export_atlas_exchange
from .io import (
    read_json,
    sha256_contract,
    sha256_file,
    write_json_atomic,
)
from .models import (
    ArrayReference,
    ArtifactReference,
    ChannelDescriptor,
    EvidenceManifest,
    HarmoRecordingBundle,
    ProcessingOperation,
    SignalProcessingState,
    SourceAsset,
    TemporalReference,
    Timebase,
    TransformationStep,
    TrustEvidence,
    TrustReport,
    TrustStatus,
    RECORDING_SCHEMA_VERSION,
    TEMPORAL_RECORDING_SCHEMA_VERSION,
)
from .policy import (
    ComparabilityPolicyEngine,
    ComparabilityRequest,
    ReleasePolicy,
)
from .run import current_repository_state, runtime_environment


RESOLUTION_SCHEMA = (
    "https://matsui-lab.github.io/emg-atlas/schemas/"
    "comparability-resolution-index-1.0.0.json"
)


def _schema_path(name: str) -> Path:
    from harmo.schema_resources import schema_path
    return schema_path(name)


def _validate_config(config: Mapping[str, Any]) -> None:
    schema = read_json(str(_schema_path("comparability-resolution-config-1.0.0.json")))
    errors = sorted(
        Draft202012Validator(schema).iter_errors(config),
        key=lambda value: list(value.absolute_path),
    )
    if errors:
        details = []
        for error in errors:
            location = ".".join(str(value) for value in error.absolute_path) or "$"
            details.append("{}: {}".format(location, error.message))
        raise ValueError(
            "comparability resolution config schema validation failed: {}".format(
                "; ".join(details)
            )
        )


def _write_dataframe_atomic(path: Path, frame: pd.DataFrame) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    descriptor, temporary = tempfile.mkstemp(
        prefix=path.name + ".", dir=str(path.parent)
    )
    os.close(descriptor)
    try:
        frame.to_csv(temporary, index=False, float_format="%.17g")
        os.replace(temporary, path)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)


def _load_samples(
    path: Path, bundle: HarmoRecordingBundle
) -> Tuple[pd.DataFrame, np.ndarray]:
    if bundle.samples.format.lower() not in ("csv", "text/csv"):
        raise ValueError("comparability resolution currently requires CSV samples")
    if bundle.samples.checksum is not None:
        observed = sha256_file(str(path))
        if observed != bundle.samples.checksum:
            raise ValueError(
                "sample checksum does not match {}: {} != {}".format(
                    bundle.recording_id, observed, bundle.samples.checksum
                )
            )
    frame = pd.read_csv(path)
    expected = [channel.name for channel in bundle.channels]
    if list(frame.columns) != expected:
        raise ValueError(
            "sample columns do not match recording channel order: {} != {}".format(
                list(frame.columns), expected
            )
        )
    if frame.shape != (
        bundle.timebase.sample_count,
        len(bundle.channels),
    ):
        raise ValueError(
            "sample array shape does not match recording contract: {} != {}".format(
                list(frame.shape), bundle.samples.shape
            )
        )
    values = frame.to_numpy(dtype=float)
    if not np.isfinite(values).all():
        raise ValueError("comparability resolution rejects non-finite samples")
    return frame, values


def _hampel(values: np.ndarray, window: int, sigma: float) -> np.ndarray:
    result = np.asarray(values, dtype=float).copy()
    source = result.copy()
    half = max(1, int(window) // 2)
    for index in range(source.size):
        lower = max(0, index - half)
        upper = min(source.size, index + half + 1)
        local = source[lower:upper]
        median = float(np.median(local))
        mad = float(np.median(np.abs(local - median)))
        scale = 1.4826 * mad
        if scale > 0 and abs(source[index] - median) > sigma * scale:
            result[index] = median
    return result


def _process_values(
    values: np.ndarray,
    sampling_rate_hz: Optional[float],
    settings: Mapping[str, Any],
    grid_points: int,
) -> Tuple[np.ndarray, List[ProcessingOperation], str]:
    result = np.asarray(values, dtype=float).copy()
    if result.ndim != 2 or result.shape[0] < 2:
        raise ValueError("processing requires a sample-by-channel array")
    operations: List[ProcessingOperation] = []

    if bool(settings.get("mean_center", False)):
        result = result - np.mean(result, axis=0, keepdims=True)
        operations.append(
            ProcessingOperation(
                "mean-center",
                {"scope": "within-cycle-per-channel"},
                "harmo",
                __version__,
            )
        )

    bandpass = settings.get("bandpass_hz")
    if bandpass is not None:
        if sampling_rate_hz is None:
            raise ValueError("band-pass filtering requires a physical sampling rate")
        low, high = [float(value) for value in bandpass]
        order = int(settings.get("butterworth_order", 4))
        if not (0 < low < high < sampling_rate_hz / 2):
            raise ValueError("band-pass frequencies must lie below Nyquist")
        sos = signal.butter(
            order,
            [low, high],
            btype="bandpass",
            fs=sampling_rate_hz,
            output="sos",
        )
        if bool(settings.get("zero_phase", True)):
            result = signal.sosfiltfilt(sos, result, axis=0)
            method = "sosfiltfilt"
        else:
            result = signal.sosfilt(sos, result, axis=0)
            method = "sosfilt"
        operations.append(
            ProcessingOperation(
                "butterworth-bandpass",
                {
                    "frequencies_hz": [low, high],
                    "order": order,
                    "method": method,
                },
                "scipy",
                str(scipy.__version__),
            )
        )

    rectification = str(settings.get("rectification", "none"))
    if rectification == "absolute":
        result = np.abs(result)
        operations.append(
            ProcessingOperation("absolute-rectification", {}, "harmo", __version__)
        )
    elif rectification != "none":
        raise ValueError("unsupported rectification: {}".format(rectification))

    hampel_seconds = settings.get("hampel_window_seconds")
    if hampel_seconds is not None:
        if sampling_rate_hz is None:
            raise ValueError("Hampel filtering requires a physical sampling rate")
        window = max(3, int(round(float(hampel_seconds) * sampling_rate_hz)))
        if window % 2 == 0:
            window += 1
        sigma = float(settings.get("hampel_sigma", 3.0))
        for column in range(result.shape[1]):
            result[:, column] = _hampel(result[:, column], window, sigma)
        operations.append(
            ProcessingOperation(
                "hampel-replacement",
                {
                    "window_seconds": float(hampel_seconds),
                    "window_samples": window,
                    "sigma": sigma,
                },
                "harmo",
                __version__,
            )
        )

    rms_seconds = settings.get("rms_window_seconds")
    if rms_seconds is not None:
        if sampling_rate_hz is None:
            raise ValueError("RMS envelope requires a physical sampling rate")
        window = max(1, int(round(float(rms_seconds) * sampling_rate_hz)))
        kernel = np.ones(window, dtype=float) / float(window)
        result = np.column_stack(
            [
                np.sqrt(np.convolve(result[:, column] ** 2, kernel, mode="same"))
                for column in range(result.shape[1])
            ]
        )
        operations.append(
            ProcessingOperation(
                "rms-envelope",
                {
                    "window_seconds": float(rms_seconds),
                    "window_samples": window,
                    "boundary": "zero-padded-same",
                },
                "numpy",
                str(np.__version__),
            )
        )

    old_grid = np.linspace(0.0, 100.0, result.shape[0])
    new_grid = np.linspace(0.0, 100.0, grid_points)
    result = np.column_stack(
        [
            np.interp(new_grid, old_grid, result[:, column])
            for column in range(result.shape[1])
        ]
    )
    operations.append(
        ProcessingOperation(
            "linear-phase-resample",
            {
                "source_points": int(values.shape[0]),
                "target_points": int(grid_points),
                "source_bounds_percent": [0.0, 100.0],
                "target_bounds_percent": [0.0, 100.0],
                "extrapolation": False,
            },
            "numpy",
            str(np.__version__),
        )
    )

    normalization = str(settings.get("amplitude_normalization", "none"))
    if normalization == "cycle-max":
        maxima = np.max(np.abs(result), axis=0)
        if np.any(maxima <= 0) or not np.isfinite(maxima).all():
            raise ValueError("cycle-max normalization requires nonzero finite channels")
        result = result / maxima
        amplitude_scale = "within-cycle-maximum"
        operations.append(
            ProcessingOperation(
                "cycle-max-normalization",
                {"scope": "within-cycle-per-channel"},
                "harmo",
                __version__,
            )
        )
    elif normalization == "none":
        amplitude_scale = "source-scale"
    else:
        raise ValueError(
            "unsupported amplitude normalization: {}".format(normalization)
        )

    if not operations:
        raise ValueError("resolution must perform at least one declared operation")
    if not np.isfinite(result).all():
        raise ValueError("processing produced non-finite samples")
    return result, operations, amplitude_scale


def _copy_channels(
    channels: Sequence[ChannelDescriptor], normalized: bool
) -> List[ChannelDescriptor]:
    return [
        ChannelDescriptor(
            channel_id=value.channel_id,
            name=value.name,
            unit="within-cycle-max" if normalized else value.unit,
            sensor_type=value.sensor_type,
            muscle=value.muscle,
            side=value.side,
            coordinates=value.coordinates,
            source_name=value.source_name,
            anatomy=value.anatomy,
        )
        for value in channels
    ]


def _derived_trust(
    source: TrustReport,
    recording_id: str,
    config_digest: str,
    validation_refs: Sequence[str],
    code_dirty: Optional[bool],
) -> TrustReport:
    copied = [
        TrustEvidence(
            dimension=item.dimension,
            status=item.status,
            rule_id="source.{}".format(item.rule_id),
            message="source recording evidence retained: {}".format(item.message),
            metric=item.metric,
            threshold=item.threshold,
            artifact_id=item.artifact_id,
        )
        for item in source.evidence
    ]
    copied.extend(
        [
            TrustEvidence(
                "semantic_fidelity",
                TrustStatus.PASS,
                "resolution.temporal-reference",
                "gait-cycle landmarks, side, axis, and grid are explicit",
                artifact_id="resolution-config",
            ),
            TrustEvidence(
                "scientific_validity",
                TrustStatus.PASS,
                "resolution.timing-validation",
                "activation-timing transformation is bound to validation evidence: {}".format(
                    ", ".join(validation_refs)
                ),
                artifact_id="validation-evidence",
            ),
            TrustEvidence(
                "traceability",
                TrustStatus.PASS,
                "resolution.config-hash",
                "resolution parameters are checksum-bound",
                artifact_id="resolution-config:{}".format(config_digest),
            ),
            TrustEvidence(
                "computational_reproducibility",
                (
                    TrustStatus.WARN
                    if code_dirty
                    else TrustStatus.PASS
                ),
                "resolution.repository-state",
                (
                    "resolver executed with uncommitted repository changes"
                    if code_dirty
                    else "resolver executed from a clean repository state"
                ),
            ),
        ]
    )
    return TrustReport(subject_id=recording_id, evidence=copied)


def _validation_artifacts(
    config: Mapping[str, Any], config_dir: Path
) -> Tuple[List[ArtifactReference], List[str]]:
    artifacts = []
    references = []
    for index, item in enumerate(config["validation_evidence"]):
        path = Path(str(item["path"]))
        if not path.is_absolute():
            path = (config_dir / path).resolve()
        if not path.is_file():
            raise ValueError("validation evidence does not exist: {}".format(path))
        digest = sha256_file(str(path))
        if digest != item["sha256"]:
            raise ValueError(
                "validation evidence checksum mismatch: {} != {}".format(
                    digest, item["sha256"]
                )
            )
        artifact_id = "validation-evidence-{:02d}".format(index + 1)
        artifacts.append(
            ArtifactReference(
                artifact_id,
                str(item["uri"]),
                digest,
                str(item.get("media_type", "application/octet-stream")),
            )
        )
        references.append("{}:sha256:{}".format(item["uri"], digest))
    return artifacts, references


def _derive_one(
    role: str,
    source_bundle: HarmoRecordingBundle,
    source_trust: TrustReport,
    source_values: np.ndarray,
    settings: Mapping[str, Any],
    config: Mapping[str, Any],
    config_digest: str,
    validation_refs: Sequence[str],
    output: Path,
    code_dirty: Optional[bool],
) -> Tuple[HarmoRecordingBundle, TrustReport, Path, Path]:
    grid = config["common_grid"]
    points = int(grid["points"])
    processed, operations, amplitude_scale = _process_values(
        source_values,
        source_bundle.timebase.sampling_rate_hz,
        settings["processing"],
        points,
    )
    samples_path = output / role / "resolved.samples.csv"
    timestamps_path = output / role / "resolved.phase.csv"
    _write_dataframe_atomic(
        samples_path,
        pd.DataFrame(
            processed, columns=[channel.name for channel in source_bundle.channels]
        ),
    )
    _write_dataframe_atomic(
        timestamps_path,
        pd.DataFrame(
            {"gait_cycle_percent": np.linspace(0.0, 100.0, points)}
        ),
    )
    recording_id = "{}::resolved:{}".format(
        source_bundle.recording_id, config["resolution_id"]
    )
    compatibility_group = str(config["compatibility_group"])
    normalized = settings["processing"].get("amplitude_normalization") == "cycle-max"
    temporal = TemporalReference(
        axis="gait-cycle-percent",
        unit="%gait-cycle",
        start=0.0,
        end=100.0,
        sample_count=points,
        landmark_start=str(grid["landmark_start"]),
        landmark_end=str(grid["landmark_end"]),
        side=str(settings["side"]),
        method=str(settings["event_method"]),
        compatibility_group=compatibility_group,
        validation_evidence_refs=list(validation_refs),
    )
    processing = SignalProcessingState(
        representation=str(settings["processing"]["representation"]),
        operations=operations,
        amplitude_scale=amplitude_scale,
        amplitude_comparability="not-established",
        timing_compatibility_group=compatibility_group,
        validation_evidence_refs=list(validation_refs),
        source_recording_sha256=sha256_contract(source_bundle),
    )
    bundle = HarmoRecordingBundle(
        recording_id=recording_id,
        study_id=source_bundle.study_id,
        subject_id=source_bundle.subject_id,
        session_id="{}::resolved:{}".format(
            source_bundle.session_id, config["resolution_id"]
        ),
        samples=ArrayReference(
            uri="urn:harmo:artifact:{}-resolved-samples".format(role),
            format="csv",
            dtype="float64",
            shape=[points, len(source_bundle.channels)],
            checksum=sha256_file(str(samples_path)),
        ),
        timebase=Timebase(
            sampling_rate_hz=None,
            sample_count=points,
            start_seconds=0.0,
            timestamps_uri="urn:harmo:artifact:{}-resolved-phase".format(role),
            clock="gait-cycle-phase",
        ),
        channels=_copy_channels(source_bundle.channels, normalized),
        sources=[
            SourceAsset(**value.to_dict()) for value in source_bundle.sources
        ],
        warnings=list(source_bundle.warnings),
        temporal_reference=temporal,
        processing_state=processing,
        task=source_bundle.task,
        schema_version=(
            RECORDING_SCHEMA_VERSION
            if source_bundle.schema_version == RECORDING_SCHEMA_VERSION
            else TEMPORAL_RECORDING_SCHEMA_VERSION
        ),
    )
    trust = _derived_trust(
        source_trust,
        recording_id,
        config_digest,
        validation_refs,
        code_dirty,
    )
    return bundle, trust, samples_path, timestamps_path


def _build_manifest(
    config_path: Path,
    config_digest: str,
    validation_artifacts: Sequence[ArtifactReference],
    repository_state: Mapping[str, Any],
    roles: Mapping[str, Mapping[str, Any]],
    comparison_digest: str,
) -> EvidenceManifest:
    inputs = [
        ArtifactReference(
            "resolution-config",
            config_path.as_uri(),
            config_digest,
            "application/json",
        ),
        *validation_artifacts,
    ]
    outputs = []
    steps = []
    for role, value in roles.items():
        source_bundle = value["source_bundle"]
        source_trust = value["source_trust"]
        source_samples = value["source_samples"]
        bundle = value["bundle"]
        trust = value["trust"]
        samples_path = value["samples_path"]
        timestamps_path = value["timestamps_path"]
        inputs.extend(
            [
                ArtifactReference(
                    "{}-source-bundle".format(role),
                    "urn:harmo:input:{}-source-bundle".format(role),
                    sha256_contract(source_bundle),
                    "application/json",
                    "sha256-canonical-json",
                ),
                ArtifactReference(
                    "{}-source-trust".format(role),
                    "urn:harmo:input:{}-source-trust".format(role),
                    sha256_contract(source_trust),
                    "application/json",
                    "sha256-canonical-json",
                ),
                ArtifactReference(
                    "{}-source-samples".format(role),
                    source_samples.as_uri(),
                    sha256_file(str(source_samples)),
                    "text/csv",
                ),
            ]
        )
        outputs.extend(
            [
                ArtifactReference(
                    "{}-resolved-samples".format(role),
                    str(bundle.samples.uri),
                    sha256_file(str(samples_path)),
                    "text/csv",
                ),
                ArtifactReference(
                    "{}-resolved-phase".format(role),
                    str(bundle.timebase.timestamps_uri),
                    sha256_file(str(timestamps_path)),
                    "text/csv",
                ),
                ArtifactReference(
                    "{}-resolved-bundle".format(role),
                    "urn:harmo:output:{}-resolved-bundle".format(role),
                    sha256_contract(bundle),
                    "application/json",
                    "sha256-canonical-json",
                ),
                ArtifactReference(
                    "{}-resolved-trust".format(role),
                    "urn:harmo:output:{}-resolved-trust".format(role),
                    sha256_contract(trust),
                    "application/json",
                    "sha256-canonical-json",
                ),
            ]
        )
        steps.append(
            TransformationStep(
                step_id="resolve-{}".format(role),
                name="create validated common gait-cycle representation",
                software="harmo",
                software_version=__version__,
                parameters={"role": role},
                inputs=[
                    "{}-source-bundle".format(role),
                    "{}-source-trust".format(role),
                    "{}-source-samples".format(role),
                    "resolution-config",
                    *[
                        artifact.artifact_id
                        for artifact in validation_artifacts
                    ],
                ],
                outputs=[
                    "{}-resolved-samples".format(role),
                    "{}-resolved-phase".format(role),
                    "{}-resolved-bundle".format(role),
                    "{}-resolved-trust".format(role),
                ],
            )
        )
    outputs.append(
        ArtifactReference(
            "resolved-comparison",
            "urn:harmo:output:resolved-comparison",
            comparison_digest,
            "application/json",
            "sha256-canonical-json",
        )
    )
    steps.append(
        TransformationStep(
            step_id="reassess-comparability",
            name="re-evaluate intended-use comparability after resolution",
            software="harmo",
            software_version=__version__,
            inputs=[
                "left-resolved-bundle",
                "left-resolved-trust",
                "right-resolved-bundle",
                "right-resolved-trust",
            ],
            outputs=["resolved-comparison"],
        )
    )
    return EvidenceManifest(
        run_id="comparability-resolution-{}".format(config_digest[:20]),
        inputs=inputs,
        outputs=outputs,
        steps=steps,
        code_commit=str(repository_state.get("commit", "unknown")),
        config_sha256=config_digest,
        code_dirty=repository_state.get("dirty"),  # type: ignore[arg-type]
        working_tree_sha256=repository_state.get(  # type: ignore[arg-type]
            "working_tree_sha256"
        ),
        environment=runtime_environment(),
    )


def resolve_activation_timing(
    left_bundle_path: str,
    left_trust_path: str,
    left_samples_path: str,
    right_bundle_path: str,
    right_trust_path: str,
    right_samples_path: str,
    config_path: str,
    output_dir: str,
) -> Dict[str, Any]:
    """Resolve a pair's timing conditions and re-evaluate comparability."""
    paths = [
        Path(value).resolve()
        for value in (
            left_bundle_path,
            left_trust_path,
            left_samples_path,
            right_bundle_path,
            right_trust_path,
            right_samples_path,
            config_path,
        )
    ]
    if any(not value.is_file() for value in paths):
        missing = [str(value) for value in paths if not value.is_file()]
        raise ValueError("resolution inputs do not exist: {}".format(missing))
    (
        left_bundle_file,
        left_trust_file,
        left_samples_file,
        right_bundle_file,
        right_trust_file,
        right_samples_file,
        config_file,
    ) = paths
    config = read_json(str(config_file))
    _validate_config(config)
    if config["intended_use"] != "activation-timing":
        raise ValueError(
            "resolver only supports activation-timing; amplitude remains out of scope"
        )

    left_bundle = HarmoRecordingBundle.from_dict(read_json(str(left_bundle_file)))
    right_bundle = HarmoRecordingBundle.from_dict(read_json(str(right_bundle_file)))
    left_trust = TrustReport.from_dict(read_json(str(left_trust_file)))
    right_trust = TrustReport.from_dict(read_json(str(right_trust_file)))
    if left_bundle.recording_id != left_trust.subject_id:
        raise ValueError("left trust report does not match source recording")
    if right_bundle.recording_id != right_trust.subject_id:
        raise ValueError("right trust report does not match source recording")
    _, left_values = _load_samples(left_samples_file, left_bundle)
    _, right_values = _load_samples(right_samples_file, right_bundle)

    config_digest = sha256_file(str(config_file))
    validation_artifacts, validation_refs = _validation_artifacts(
        config, config_file.parent
    )
    request = ComparabilityRequest(
        variable=str(config["variable"]),
        intended_use=str(config["intended_use"]),
        left_channel_id=str(config["left"]["comparison_channel_id"]),
        right_channel_id=str(config["right"]["comparison_channel_id"]),
    )
    engine = ComparabilityPolicyEngine()
    before = engine.evaluate(
        left_bundle, right_bundle, left_trust, right_trust, request
    )
    repository_state = current_repository_state()
    output = Path(output_dir).resolve()
    output.mkdir(parents=True, exist_ok=True)
    left_resolved, left_resolved_trust, left_samples, left_phase = _derive_one(
        "left",
        left_bundle,
        left_trust,
        left_values,
        config["left"],
        config,
        config_digest,
        validation_refs,
        output,
        repository_state.get("dirty"),  # type: ignore[arg-type]
    )
    right_resolved, right_resolved_trust, right_samples, right_phase = _derive_one(
        "right",
        right_bundle,
        right_trust,
        right_values,
        config["right"],
        config,
        config_digest,
        validation_refs,
        output,
        repository_state.get("dirty"),  # type: ignore[arg-type]
    )
    after = engine.evaluate(
        left_resolved,
        right_resolved,
        left_resolved_trust,
        right_resolved_trust,
        request,
    )
    roles = {
        "left": {
            "source_bundle": left_bundle,
            "source_trust": left_trust,
            "source_samples": left_samples_file,
            "bundle": left_resolved,
            "trust": left_resolved_trust,
            "samples_path": left_samples,
            "timestamps_path": left_phase,
        },
        "right": {
            "source_bundle": right_bundle,
            "source_trust": right_trust,
            "source_samples": right_samples_file,
            "bundle": right_resolved,
            "trust": right_resolved_trust,
            "samples_path": right_samples,
            "timestamps_path": right_phase,
        },
    }
    manifest = _build_manifest(
        config_file,
        config_digest,
        validation_artifacts,
        repository_state,
        roles,
        sha256_contract(after),
    )
    policy = ReleasePolicy.from_dict(config.get("release_policy"))
    left_decision = policy.decide(
        left_resolved, manifest, left_resolved_trust, after
    )
    right_decision = policy.decide(
        right_resolved, manifest, right_resolved_trust, after
    )

    files = {
        "left_bundle": write_json_atomic(
            str(output / "left" / "recording.bundle.json"), left_resolved
        ),
        "left_trust": write_json_atomic(
            str(output / "left" / "trust.report.json"), left_resolved_trust
        ),
        "left_release_decision": write_json_atomic(
            str(output / "left" / "release.decision.json"), left_decision
        ),
        "right_bundle": write_json_atomic(
            str(output / "right" / "recording.bundle.json"), right_resolved
        ),
        "right_trust": write_json_atomic(
            str(output / "right" / "trust.report.json"), right_resolved_trust
        ),
        "right_release_decision": write_json_atomic(
            str(output / "right" / "release.decision.json"), right_decision
        ),
        "comparison_before": write_json_atomic(
            str(output / "comparability.before.json"), before
        ),
        "comparison_after": write_json_atomic(
            str(output / "comparability.after.json"), after
        ),
        "manifest": write_json_atomic(
            str(output / "evidence.manifest.json"), manifest
        ),
    }
    exchange_files: Dict[str, Dict[str, str]] = {}
    if left_decision.export_permitted:
        exchange_files["left"] = export_atlas_exchange(
            str(output / "left" / "exchange"),
            left_resolved,
            manifest,
            left_resolved_trust,
            after,
            left_decision,
        )
    if right_decision.export_permitted:
        exchange_files["right"] = export_atlas_exchange(
            str(output / "right" / "exchange"),
            right_resolved,
            manifest,
            right_resolved_trust,
            after,
            right_decision,
        )
    index = {
        "schema": RESOLUTION_SCHEMA,
        "resolution_id": config["resolution_id"],
        "intended_use": config["intended_use"],
        "source_status": before.status.value,
        "resolved_status": after.status.value,
        "conditions_before": before.conditions,
        "conditions_after": after.conditions,
        "amplitude_comparability": "not-established",
        "left_release_status": left_decision.status.value,
        "right_release_status": right_decision.status.value,
        "exchange_exported": sorted(exchange_files),
        "files": {
            key: str(Path(value).relative_to(output))
            for key, value in files.items()
        },
        "digests": {
            key: sha256_contract(read_json(value))
            for key, value in files.items()
            if value.endswith(".json")
        },
    }
    files["index"] = write_json_atomic(
        str(output / "comparability.resolution.index.json"), index
    )
    return {
        "resolution_id": config["resolution_id"],
        "source_status": before.status.value,
        "resolved_status": after.status.value,
        "conditions_before": before.conditions,
        "conditions_after": after.conditions,
        "left_release_status": left_decision.status.value,
        "right_release_status": right_decision.status.value,
        "amplitude_comparability": "not-established",
        "files": files,
        "exchange_files": exchange_files,
    }
