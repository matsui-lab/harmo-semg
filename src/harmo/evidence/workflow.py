"""End-to-end evidence qualification for a wide EMG CSV/TSV recording."""
from __future__ import annotations

import os
import tempfile
from pathlib import Path
from typing import Any, Dict, Optional

import pandas as pd
from jsonschema import Draft202012Validator

from harmo import __version__

from .adapters import bundle_from_wide_dataframe
from .audit import export_atlas_exchange
from .io import read_json, sha256_contract, sha256_file, write_json_atomic
from .models import (
    ArtifactReference,
    EvidenceManifest,
    HarmoRecordingBundle,
    TransformationStep,
    TrustEvidence,
    TrustReport,
    TrustStatus,
)
from .policy import (
    ComparabilityPolicyEngine,
    ComparabilityRequest,
    ReleasePolicy,
)
from .run import current_repository_state, runtime_environment
from .validation import RecordingProfile, assess_bundle


QUALIFICATION_SCHEMA = (
    "https://matsui-lab.github.io/emg-atlas/"
    "schemas/qualification-index-1.0.0.json"
)


def _validate_public_config_schema(config: Dict[str, Any]) -> None:
    from harmo.schema_resources import schema_path
    schema = read_json(str(schema_path("qualification-config-1.1.0.json")))
    errors = sorted(
        Draft202012Validator(schema).iter_errors(config),
        key=lambda error: list(error.absolute_path),
    )
    if errors:
        messages = [
            "{}: {}".format(
                ".".join(str(value) for value in error.absolute_path) or "$",
                error.message,
            )
            for error in errors
        ]
        raise ValueError(
            "qualification config schema validation failed: {}".format(
                "; ".join(messages)
            )
        )


def _required_string(config: Dict[str, Any], key: str) -> str:
    value = config.get(key)
    if not isinstance(value, str) or not value:
        raise ValueError("qualification config requires non-empty {}".format(key))
    return value


def _reject_unknown_keys(
    value: Dict[str, Any], allowed: set, scope: str
) -> None:
    unknown = sorted(set(value) - allowed)
    if unknown:
        raise ValueError(
            "{} contains unknown fields: {}".format(scope, ", ".join(unknown))
        )


def _validate_config_shape(config: Dict[str, Any]) -> None:
    _reject_unknown_keys(
        config,
        {
            "recording_id",
            "study_id",
            "subject_id",
            "session_id",
            "time_column",
            "sensor_type",
            "source_uri",
            "source_dataset_release",
            "regularity_tolerance",
            "channel_units",
            "channel_metadata",
            "task",
            "validation_profile",
            "comparison",
            "release_policy",
        },
        "qualification config",
    )
    for key in ("recording_id", "study_id", "subject_id", "session_id"):
        _required_string(config, key)
    for key in (
        "time_column",
        "sensor_type",
        "source_uri",
        "source_dataset_release",
    ):
        if key in config and (
            not isinstance(config[key], str) or not config[key]
        ):
            raise ValueError("{} must be a non-empty string".format(key))
    tolerance = config.get("regularity_tolerance", 0.01)
    if (
        isinstance(tolerance, bool)
        or not isinstance(tolerance, (int, float))
        or float(tolerance) < 0
    ):
        raise ValueError("regularity_tolerance must be a non-negative number")

    channel_units = config.get("channel_units")
    if (
        not isinstance(channel_units, dict)
        or not channel_units
        or any(
            not isinstance(key, str)
            or not key
            or not isinstance(value, str)
            or not value
            for key, value in channel_units.items()
        )
    ):
        raise ValueError(
            "channel_units must map channel names to non-empty unit strings"
        )
    metadata = config.get("channel_metadata")
    if not isinstance(metadata, dict):
        raise ValueError("channel_metadata must be an object")
    extra_metadata = sorted(set(metadata) - set(channel_units))
    if extra_metadata:
        raise ValueError(
            "channel_metadata names are absent from channel_units: {}".format(
                ", ".join(extra_metadata)
            )
        )
    for channel_name, values in metadata.items():
        if not isinstance(values, dict):
            raise ValueError(
                "channel_metadata.{} must be an object".format(channel_name)
            )
        _reject_unknown_keys(
            values,
            {"muscle", "side", "sensor_type", "source_name", "anatomy"},
            "channel_metadata.{}".format(channel_name),
        )
    task = config.get("task")
    if task is not None and not isinstance(task, dict):
        raise ValueError("task must be an object")

    profile = config.get("validation_profile")
    if not isinstance(profile, dict):
        raise ValueError("qualification config requires validation_profile")
    _reject_unknown_keys(
        profile,
        {
            "sampling_rate_min_hz",
            "sampling_rate_max_hz",
            "units_by_sensor_type",
            "required_channel_names",
            "require_array_checksum",
            "expected_sampling_rate_hz",
            "expected_start_seconds",
            "time_tolerance_samples",
            "expected_sides_by_channel",
            "expected_muscles_by_channel",
            "expected_coordinates_by_channel",
        },
        "validation_profile",
    )
    expected_rate = profile.get("expected_sampling_rate_hz")
    if (
        isinstance(expected_rate, bool)
        or not isinstance(expected_rate, (int, float))
        or float(expected_rate) <= 0
    ):
        raise ValueError(
            "validation_profile.expected_sampling_rate_hz must be positive"
        )

    comparison = config.get("comparison")
    if not isinstance(comparison, dict):
        raise ValueError("qualification config requires comparison")
    _reject_unknown_keys(
        comparison,
        {
            "variable",
            "intended_use",
            "reference_channel_id",
            "recording_channel_id",
        },
        "comparison",
    )
    for key in (
        "variable",
        "intended_use",
        "reference_channel_id",
        "recording_channel_id",
    ):
        _required_string(comparison, key)

    release = config.get("release_policy")
    if release is not None:
        if not isinstance(release, dict):
            raise ValueError("release_policy must be an object")
        _reject_unknown_keys(
            release,
            {
                "policy_id",
                "policy_version",
                "required_dimensions",
                "allow_trust_warnings",
                "allow_conditional_comparability",
            },
            "release_policy",
        )


def _load_source(path: Path) -> pd.DataFrame:
    suffix = path.suffix.lower()
    if suffix == ".csv":
        return pd.read_csv(path)
    if suffix in (".tsv", ".txt"):
        return pd.read_csv(path, sep="\t")
    raise ValueError("qualification source must be CSV or TSV")


def _write_dataframe_atomic(path: Path, frame: pd.DataFrame) -> None:
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


def _recording_profile(
    config: Dict[str, Any],
    bundle: HarmoRecordingBundle,
) -> RecordingProfile:
    raw = config.get("validation_profile")
    if not isinstance(raw, dict):
        raise ValueError("qualification config requires validation_profile")
    if raw.get("expected_sampling_rate_hz") is None:
        raise ValueError(
            "validation_profile.expected_sampling_rate_hz is required"
        )

    units_by_sensor_type = raw.get("units_by_sensor_type")
    if units_by_sensor_type is None:
        units_by_sensor_type = {}
        for channel in bundle.channels:
            units_by_sensor_type.setdefault(channel.sensor_type, [])
            if channel.unit not in units_by_sensor_type[channel.sensor_type]:
                units_by_sensor_type[channel.sensor_type].append(channel.unit)

    expected_sides = raw.get("expected_sides_by_channel")
    if expected_sides is None:
        expected_sides = {
            channel.channel_id: channel.side
            for channel in bundle.channels
            if channel.side is not None
        }
    expected_muscles = raw.get("expected_muscles_by_channel")
    if expected_muscles is None:
        expected_muscles = {
            channel.channel_id: channel.muscle
            for channel in bundle.channels
            if channel.muscle is not None
        }

    return RecordingProfile(
        sampling_rate_min_hz=raw.get("sampling_rate_min_hz"),
        sampling_rate_max_hz=raw.get("sampling_rate_max_hz"),
        units_by_sensor_type={
            str(key): [str(value) for value in values]
            for key, values in units_by_sensor_type.items()
        },
        required_channel_names=[
            str(value)
            for value in raw.get(
                "required_channel_names",
                [channel.name for channel in bundle.channels],
            )
        ],
        require_array_checksum=bool(raw.get("require_array_checksum", True)),
        expected_sampling_rate_hz=float(raw["expected_sampling_rate_hz"]),
        expected_start_seconds=(
            None
            if raw.get("expected_start_seconds") is None
            else float(raw["expected_start_seconds"])
        ),
        time_tolerance_samples=float(raw.get("time_tolerance_samples", 0.5)),
        expected_sides_by_channel={
            str(key): str(value) for key, value in expected_sides.items()
        },
        expected_muscles_by_channel={
            str(key): str(value) for key, value in expected_muscles.items()
        },
        expected_coordinates_by_channel={
            str(key): {
                str(axis): float(value) for axis, value in coordinates.items()
            }
            for key, coordinates in raw.get(
                "expected_coordinates_by_channel", {}
            ).items()
        },
    )


def _augment_workflow_trust(
    report: TrustReport,
    bundle: HarmoRecordingBundle,
    repository_state: Dict[str, object],
) -> TrustReport:
    evidence = list(report.evidence)
    metadata_complete = all(
        channel.muscle not in (None, "")
        and channel.side not in (None, "", "unknown")
        for channel in bundle.channels
    )
    evidence.append(
        TrustEvidence(
            dimension="semantic_fidelity",
            status=(
                TrustStatus.PASS
                if metadata_complete
                else TrustStatus.UNASSESSED
            ),
            rule_id="channel.anatomy.complete",
            message=(
                "muscle and anatomical side are declared for every channel"
                if metadata_complete
                else "muscle or anatomical side is not declared for every channel"
            ),
        )
    )

    scientific_warnings = [
        warning
        for warning in bundle.warnings
        if warning.dimension == "scientific_validity"
        and warning.severity in ("warning", "error")
    ]
    evidence.append(
        TrustEvidence(
            dimension="scientific_validity",
            status=(
                TrustStatus.PASS
                if not scientific_warnings
                else TrustStatus.WARN
            ),
            rule_id="signal.numeric-completeness",
            message=(
                "all signal values are finite numeric observations"
                if not scientific_warnings
                else "missing signal observations are retained and flagged"
            ),
        )
    )

    commit_known = repository_state.get("commit") != "unknown"
    code_dirty = repository_state.get("dirty")
    working_tree_bound = bool(repository_state.get("working_tree_sha256"))
    if commit_known and code_dirty is False:
        code_status = TrustStatus.PASS
        code_message = "qualification code is bound to a clean commit"
    elif commit_known and code_dirty is True and working_tree_bound:
        code_status = TrustStatus.WARN
        code_message = (
            "unreleased working-tree code is fingerprinted but is not a "
            "substitute for an archived source revision"
        )
    else:
        code_status = TrustStatus.UNASSESSED
        code_message = "code state could not be reproducibly bound"
    evidence.extend(
        [
            TrustEvidence(
                dimension="computational_reproducibility",
                status=code_status,
                rule_id="code.state.bound",
                message=code_message,
            ),
            TrustEvidence(
                dimension="computational_reproducibility",
                status=TrustStatus.PASS,
                rule_id="qualification.config.bound",
                message="qualification configuration and normalized arrays are hashed",
            ),
        ]
    )
    return TrustReport(subject_id=report.subject_id, evidence=evidence)


def _build_manifest(
    *,
    source: Path,
    source_asset: Path,
    source_uri: str,
    config_path: Path,
    samples_path: Path,
    timestamps_path: Path,
    reference_bundle_path: Path,
    reference_trust_path: Path,
    bundle: HarmoRecordingBundle,
    trust: TrustReport,
    comparison: Any,
    repository_state: Dict[str, object],
) -> EvidenceManifest:
    inputs = [
        ArtifactReference(
            "wide-source",
            (
                source_uri
                if source_asset == source
                else "wide-source{}".format(source.suffix.lower())
            ),
            sha256_file(str(source)),
            "text/csv"
            if source.suffix.lower() == ".csv"
            else "text/tab-separated-values",
        ),
        ArtifactReference(
            "qualification-config",
            "qualification.json",
            sha256_file(str(config_path)),
            "application/json",
        ),
        ArtifactReference(
            "reference-bundle",
            "reference.bundle.json",
            sha256_contract(
                HarmoRecordingBundle.from_dict(
                    read_json(str(reference_bundle_path))
                )
            ),
            "application/json",
            "sha256-canonical-json",
        ),
        ArtifactReference(
            "reference-trust",
            "reference.trust.json",
            sha256_contract(
                TrustReport.from_dict(read_json(str(reference_trust_path)))
            ),
            "application/json",
            "sha256-canonical-json",
        ),
    ]
    if source_asset != source:
        inputs.append(
            ArtifactReference(
                "source-asset",
                source_uri,
                sha256_file(str(source_asset)),
                "application/octet-stream",
            )
        )
    outputs = [
        ArtifactReference(
            "normalized-samples",
            "normalized.samples.csv",
            sha256_file(str(samples_path)),
            "text/csv",
        ),
        ArtifactReference(
            "normalized-timestamps",
            "normalized.timestamps.csv",
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
        ArtifactReference(
            "comparability-assertion",
            "comparability.assertion.json",
            sha256_contract(comparison),
            "application/json",
            "sha256-canonical-json",
        ),
    ]
    return EvidenceManifest(
        run_id="qualify-{}".format(sha256_contract(bundle)[:20]),
        inputs=inputs,
        outputs=outputs,
        steps=[
            TransformationStep(
                step_id="normalize",
                name="preserve wide EMG values and explicit timestamps",
                software="harmo",
                software_version=__version__,
                parameters={"value_transform": "none", "time_unit": "s"},
                inputs=(
                    ["wide-source", "source-asset", "qualification-config"]
                    if source_asset != source
                    else ["wide-source", "qualification-config"]
                ),
                outputs=["normalized-samples", "normalized-timestamps"],
            ),
            TransformationStep(
                step_id="describe",
                name="construct recording bundle",
                software="harmo",
                software_version=__version__,
                inputs=[
                    "wide-source",
                    "qualification-config",
                    "normalized-samples",
                    "normalized-timestamps",
                ],
                outputs=["recording-bundle"],
            ),
            TransformationStep(
                step_id="assess",
                name="evaluate trust dimensions",
                software="harmo",
                software_version=__version__,
                inputs=["recording-bundle", "qualification-config"],
                outputs=["trust-report"],
            ),
            TransformationStep(
                step_id="compare",
                name="evaluate intended-use comparability",
                software="harmo",
                software_version=__version__,
                inputs=[
                    "recording-bundle",
                    "trust-report",
                    "reference-bundle",
                    "reference-trust",
                ],
                outputs=["comparability-assertion"],
            ),
        ],
        code_commit=str(repository_state.get("commit", "unknown")),
        config_sha256=sha256_file(str(config_path)),
        code_dirty=repository_state.get("dirty"),  # type: ignore[arg-type]
        working_tree_sha256=repository_state.get(  # type: ignore[arg-type]
            "working_tree_sha256"
        ),
        environment=runtime_environment(),
    )


def qualify_wide_emg(
    source_path: str,
    config_path: str,
    reference_bundle_path: str,
    reference_trust_path: str,
    output_dir: str,
    source_asset_path: Optional[str] = None,
) -> Dict[str, Any]:
    """Create R/M/T/C/D contracts and export only an allowed release."""
    source = Path(source_path).resolve()
    source_asset = (
        Path(source_asset_path).resolve() if source_asset_path else source
    )
    config_file = Path(config_path).resolve()
    reference_bundle_file = Path(reference_bundle_path).resolve()
    reference_trust_file = Path(reference_trust_path).resolve()
    for path in (
        source,
        source_asset,
        config_file,
        reference_bundle_file,
        reference_trust_file,
    ):
        if not path.is_file():
            raise ValueError("qualification input does not exist: {}".format(path))

    config = read_json(str(config_file))
    _validate_public_config_schema(config)
    _validate_config_shape(config)
    frame = _load_source(source)
    time_column = config.get("time_column", "time")
    if time_column not in frame:
        raise ValueError("time column is missing: {}".format(time_column))
    channel_units = config["channel_units"]
    channel_metadata = config.get("channel_metadata", {})
    comparison_config = config["comparison"]

    repository_state = current_repository_state()
    output = Path(output_dir).resolve()
    output.mkdir(parents=True, exist_ok=True)
    samples_path = output / "normalized.samples.csv"
    timestamps_path = output / "normalized.timestamps.csv"
    signal_columns = [name for name in frame.columns if name != time_column]
    _write_dataframe_atomic(samples_path, frame[signal_columns])
    _write_dataframe_atomic(
        timestamps_path,
        pd.DataFrame({"time_seconds": frame[time_column]}),
    )

    bundle, _ = bundle_from_wide_dataframe(
        frame,
        recording_id=_required_string(config, "recording_id"),
        study_id=_required_string(config, "study_id"),
        subject_id=_required_string(config, "subject_id"),
        session_id=_required_string(config, "session_id"),
        source_path=str(source_asset),
        array_uri=samples_path.as_uri(),
        channel_units={
            str(key): str(value) for key, value in channel_units.items()
        },
        time_column=time_column,
        sensor_type=str(config.get("sensor_type", "surface-emg")),
        channel_metadata={
            str(key): {str(k): v for k, v in value.items()}
            for key, value in channel_metadata.items()
        },
        task_metadata=config.get("task"),
        array_format="csv",
        array_dtype="float64",
        regularity_tolerance=float(config.get("regularity_tolerance", 0.01)),
        explicit_timestamps_uri=timestamps_path.as_uri(),
    )
    bundle.samples.uri = "urn:harmo:artifact:normalized-samples"
    bundle.timebase.timestamps_uri = (
        "urn:harmo:artifact:normalized-timestamps"
    )
    source_uri = config.get("source_uri", bundle.sources[0].uri)
    bundle.sources[0].uri = source_uri
    bundle.sources[0].dataset_release = config.get(
        "source_dataset_release"
    )

    profile = _recording_profile(config, bundle)
    trust = _augment_workflow_trust(
        assess_bundle(bundle, profile), bundle, repository_state
    )
    reference_bundle = HarmoRecordingBundle.from_dict(
        read_json(str(reference_bundle_file))
    )
    reference_trust = TrustReport.from_dict(
        read_json(str(reference_trust_file))
    )
    request = ComparabilityRequest(
        variable=_required_string(comparison_config, "variable"),
        intended_use=_required_string(comparison_config, "intended_use"),
        left_channel_id=_required_string(
            comparison_config, "reference_channel_id"
        ),
        right_channel_id=_required_string(
            comparison_config, "recording_channel_id"
        ),
    )
    comparison = ComparabilityPolicyEngine().evaluate(
        reference_bundle,
        bundle,
        reference_trust,
        trust,
        request,
    )
    manifest = _build_manifest(
        source=source,
        source_asset=source_asset,
        source_uri=source_uri,
        config_path=config_file,
        samples_path=samples_path,
        timestamps_path=timestamps_path,
        reference_bundle_path=reference_bundle_file,
        reference_trust_path=reference_trust_file,
        bundle=bundle,
        trust=trust,
        comparison=comparison,
        repository_state=repository_state,
    )
    release_policy = ReleasePolicy.from_dict(config.get("release_policy"))
    decision = release_policy.decide(bundle, manifest, trust, comparison)

    files = {
        "recording": write_json_atomic(
            str(output / "recording.bundle.json"), bundle
        ),
        "manifest": write_json_atomic(
            str(output / "evidence.manifest.json"), manifest
        ),
        "trust": write_json_atomic(str(output / "trust.report.json"), trust),
        "comparability": write_json_atomic(
            str(output / "comparability.assertion.json"), comparison
        ),
        "release_decision": write_json_atomic(
            str(output / "release.decision.json"), decision
        ),
    }
    qualification_index = {
        "schema": QUALIFICATION_SCHEMA,
        "recording_id": bundle.recording_id,
        "intended_use": comparison.intended_use,
        "release_status": decision.status.value,
        "exchange_exported": decision.export_permitted,
        "files": {key: Path(value).name for key, value in files.items()},
        "digests": {
            key: sha256_contract(read_json(value))
            for key, value in files.items()
        },
    }
    files["qualification_index"] = write_json_atomic(
        str(output / "qualification.index.json"), qualification_index
    )
    exchange_files = {}  # type: Dict[str, str]
    if decision.export_permitted:
        exchange_files = export_atlas_exchange(
            str(output / "exchange"),
            bundle,
            manifest,
            trust,
            comparison,
            decision,
        )
    return {
        "recording_id": bundle.recording_id,
        "release_status": decision.status.value,
        "exchange_exported": bool(exchange_files),
        "files": files,
        "exchange_files": exchange_files,
    }
