"""Full-record Study 38 materialization and DuckDB scalability benchmark."""

from __future__ import annotations

import hashlib
import json
import statistics
import time
from collections import defaultdict
from pathlib import Path
from typing import Any, Dict, Mapping

import duckdb
import numpy as np
import pandas as pd
import yaml
from scipy.io import loadmat

from harmo.evidence.adapters import bundle_from_wide_dataframe
from harmo.evidence.io import (
    canonical_json_bytes,
    sha256_contract,
    sha256_file,
    write_json_atomic,
)
from harmo.evidence.models import (
    ArtifactReference,
    EvidenceManifest,
    TransformationStep,
    TrustEvidence,
    TrustStatus,
)
from harmo.evidence.policy import (
    ComparabilityPolicyEngine,
    ComparabilityRequest,
    ReleasePolicy,
)
from harmo.evidence.run import current_repository_state, runtime_environment
from harmo.evidence.validation import RecordingProfile, assess_bundle
from harmo.physiology_control import _asset_attestation


VERSION = "1.0.0"


def _text(value: Any) -> str:
    if isinstance(value, np.ndarray) and value.size == 1:
        value = value.item()
    return str(value).strip()


def load_protocol(path: Path) -> Dict[str, Any]:
    value = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    required = {
        "protocol_id",
        "study_id",
        "subjects",
        "source_assets",
        "selection",
        "channels",
        "query_benchmark",
    }
    if not isinstance(value, dict) or not required.issubset(value):
        raise ValueError("full-record protocol is incomplete")
    if len(value["channels"]) != 8 or len(set(value["channels"].values())) != 8:
        raise ValueError("protocol requires eight unique Study 38 channels")
    return value


def _percentile(values: list[float], q: float) -> float:
    return float(np.percentile(np.asarray(values, dtype=float), q))


def _query_benchmark(
    signal_path: Path,
    record_path: Path,
    repeats: int,
    warmups: int,
) -> Dict[str, Any]:
    queries = {
        "all_row_count": (
            "SELECT count(*) FROM read_parquet(?)",
            [str(signal_path)],
        ),
        "subject_filter": (
            "SELECT count(*) FROM read_parquet(?) WHERE subject_id = '1'",
            [str(signal_path)],
        ),
        "muscle_aggregate": (
            "SELECT subject_id, avg(tibialis_anterior) "
            "FROM read_parquet(?) GROUP BY subject_id ORDER BY subject_id",
            [str(signal_path)],
        ),
        "record_join": (
            "SELECT r.subject_id, count(s.sample_index) "
            "FROM read_parquet(?) s JOIN read_parquet(?) r USING(recording_id) "
            "GROUP BY r.subject_id ORDER BY r.subject_id",
            [str(signal_path), str(record_path)],
        ),
    }
    connection = duckdb.connect()
    results = {}
    try:
        for name, (sql, parameters) in queries.items():
            for _ in range(warmups):
                connection.execute(sql, parameters).fetchall()
            elapsed = []
            canonical_result = None
            for _ in range(repeats):
                started = time.perf_counter()
                rows = connection.execute(sql, parameters).fetchall()
                elapsed.append((time.perf_counter() - started) * 1000.0)
                digest = hashlib.sha256(canonical_json_bytes(rows)).hexdigest()
                if canonical_result is None:
                    canonical_result = digest
                elif digest != canonical_result:
                    raise ValueError("DuckDB query result changed across repeats")
            results[name] = {
                "repeat_count": repeats,
                "median_ms": statistics.median(elapsed),
                "p95_ms": _percentile(elapsed, 95),
                "min_ms": min(elapsed),
                "max_ms": max(elapsed),
                "result_sha256": canonical_result,
            }
    finally:
        connection.close()
    return results


def _append_evidence(report, positive_control_sha256: str):
    report.evidence.extend(
        [
            TrustEvidence(
                dimension="scientific_validity",
                status=TrustStatus.PASS,
                rule_id="study38.activation-timing-positive-control",
                message=(
                    "Study-level tibialis-anterior timing evidence passed for "
                    "the frozen ten-subject cohort; amplitude validity is not inferred"
                ),
                artifact_id=positive_control_sha256,
            ),
            TrustEvidence(
                dimension="computational_reproducibility",
                status=TrustStatus.PASS,
                rule_id="full-record.array-and-code-digests",
                message="source, normalized array, protocol, and code identities are recorded",
            ),
        ]
    )
    return report


def materialize_once(
    *,
    source_dir: Path,
    output_dir: Path,
    protocol_path: Path,
    positive_control_path: Path,
) -> Dict[str, Any]:
    protocol = load_protocol(protocol_path)
    state = current_repository_state()
    output_dir.mkdir(parents=True, exist_ok=True)
    arrays_dir = output_dir / "arrays"
    contracts_dir = output_dir / "contracts"
    arrays_dir.mkdir(exist_ok=True)
    contracts_dir.mkdir(exist_ok=True)
    positive_sha = sha256_file(str(positive_control_path))
    signal_frames = []
    record_rows = []
    records_by_subject: Dict[str, list[Dict[str, Any]]] = defaultdict(list)
    attestations = []
    started = time.perf_counter()

    for subject_id in protocol["subjects"]:
        source_path = source_dir / "Subject{}.mat".format(subject_id)
        attestation = _asset_attestation(
            source_path, protocol["source_assets"][str(subject_id)]
        )
        attestations.append(
            {
                key: attestation[key]
                for key in ("bytes", "md5", "sha256", "pass")
            }
            | {"subject_id": str(subject_id)}
        )
        source = loadmat(
            source_path, squeeze_me=True, struct_as_record=False
        )["s"]
        labels = [_text(value) for value in np.atleast_1d(source.EmgVarName)]
        if labels != list(protocol["channels"]):
            raise ValueError("Study 38 channel order changed for Subject{}".format(subject_id))
        side = _text(source.EMGSide)
        canonical_side = (
            "right"
            if side.upper().startswith("R")
            else "left"
            if side.upper().startswith("L")
            else "unknown"
        )
        if canonical_side == "unknown":
            raise ValueError("unsupported Study 38 EMGSide: " + side)
        fs = float(source.EMGFreq)
        selected = [
            (index, row)
            for index, row in enumerate(np.atleast_1d(source.Data).ravel())
            if _text(row.Task) == protocol["selection"]["activity"]
            and _text(row.Foot) == side
        ]
        if not selected:
            raise ValueError("no selected records for Subject{}".format(subject_id))
        for ordinal, (source_index, row) in enumerate(selected, start=1):
            values = np.asarray(row.EMG, dtype=float)
            if values.ndim != 2 or values.shape[0] != len(labels):
                raise ValueError("unexpected EMG shape")
            if values.shape[1] < 2 or not np.isfinite(values).all():
                raise ValueError("full record contains invalid samples")
            recording_id = (
                "study_38/sub-{}/walking-same-side/cycle-{:03d}".format(
                    subject_id, ordinal
                )
            )
            canonical = list(protocol["channels"].values())
            frame = pd.DataFrame(values.T, columns=canonical)
            frame.insert(0, "time", np.arange(values.shape[1], dtype=float) / fs)
            array_path = arrays_dir / (
                "sub-{}-cycle-{:03d}.parquet".format(subject_id, ordinal)
            )
            frame.drop(columns=["time"]).to_parquet(array_path, index=False)
            metadata = {
                name: {
                    "muscle": name,
                    "side": canonical_side,
                    "source_name": source_name,
                }
                for source_name, name in protocol["channels"].items()
            }
            bundle, _ = bundle_from_wide_dataframe(
                frame,
                recording_id=recording_id,
                study_id="study_38",
                subject_id=str(subject_id),
                session_id="walking-cycle-{:03d}".format(ordinal),
                source_path=str(source_path),
                array_uri=array_path.as_uri(),
                channel_units={name: "mV" for name in canonical},
                channel_metadata=metadata,
                array_format="parquet",
                array_dtype="float64",
            )
            # The source declares the acquisition rate exactly.  Avoid treating
            # binary floating-point reconstruction of arange/fs as a semantic
            # disagreement (for example 800.000000000017 versus 800 Hz).
            bundle.timebase.sampling_rate_hz = fs
            bundle.samples.uri = (
                "urn:harmo:study38-full-record:{}:samples".format(
                    recording_id
                )
            )
            profile = RecordingProfile(
                expected_sampling_rate_hz=fs,
                expected_start_seconds=0.0,
                units_by_sensor_type={"surface-emg": ["mV"]},
                required_channel_names=canonical,
                expected_sides_by_channel={
                    channel.channel_id: canonical_side for channel in bundle.channels
                },
                expected_muscles_by_channel={
                    channel.channel_id: str(channel.muscle)
                    for channel in bundle.channels
                },
            )
            trust = _append_evidence(
                assess_bundle(bundle, profile), positive_sha
            )
            record_dir = contracts_dir / "sub-{}".format(subject_id) / (
                "cycle-{:03d}".format(ordinal)
            )
            bundle_path = Path(
                write_json_atomic(
                    str(record_dir / "recording.bundle.json"), bundle
                )
            )
            trust_path = Path(
                write_json_atomic(str(record_dir / "trust.report.json"), trust)
            )
            manifest = EvidenceManifest(
                run_id="full-record-{}".format(
                    hashlib.sha256(recording_id.encode()).hexdigest()[:20]
                ),
                inputs=[
                    ArtifactReference(
                        "source-mat",
                        source_path.as_uri(),
                        attestation["sha256"],
                        "application/x-matlab-data",
                    ),
                    ArtifactReference(
                        "timing-validation",
                        positive_control_path.as_uri(),
                        positive_sha,
                        "application/json",
                    ),
                ],
                outputs=[
                    ArtifactReference(
                        "normalized-samples",
                        array_path.as_uri(),
                        sha256_file(str(array_path)),
                        "application/vnd.apache.parquet",
                    ),
                    ArtifactReference(
                        "recording-bundle",
                        bundle_path.as_uri(),
                        sha256_file(str(bundle_path)),
                        "application/json",
                    ),
                    ArtifactReference(
                        "trust-report",
                        trust_path.as_uri(),
                        sha256_file(str(trust_path)),
                        "application/json",
                    ),
                ],
                steps=[
                    TransformationStep(
                        "select",
                        "select every source-ordered same-side Walking cycle",
                        "harmo",
                        VERSION,
                        parameters={
                            "source_record_index": source_index,
                            "no_resampling": True,
                            "no_truncation": True,
                        },
                        inputs=["source-mat", "timing-validation"],
                        outputs=[
                            "normalized-samples",
                            "recording-bundle",
                            "trust-report",
                        ],
                    )
                ],
                code_commit=str(state.get("commit", "unknown")),
                config_sha256=sha256_file(str(protocol_path)),
                code_dirty=bool(state.get("dirty")),
                working_tree_sha256=state.get("working_tree_sha256"),
                environment=runtime_environment(),
            )
            manifest_path = Path(
                write_json_atomic(
                    str(record_dir / "evidence.manifest.json"), manifest
                )
            )
            enriched = frame.copy()
            enriched.insert(0, "sample_index", np.arange(len(frame), dtype=np.int64))
            enriched.insert(0, "recording_id", recording_id)
            enriched.insert(0, "subject_id", str(subject_id))
            signal_frames.append(enriched)
            item = {
                "recording_id": recording_id,
                "subject_id": str(subject_id),
                "cycle_ordinal": ordinal,
                "source_record_index": source_index,
                "sample_count": len(frame),
                "sampling_rate_hz": fs,
                "source_sha256": attestation["sha256"],
                "array_sha256": sha256_file(str(array_path)),
                "recording_sha256": sha256_contract(bundle),
                "manifest_sha256": sha256_contract(manifest),
                "trust_sha256": sha256_contract(trust),
                "bundle": bundle,
                "manifest": manifest,
                "trust": trust,
                "record_dir": record_dir,
            }
            records_by_subject[str(subject_id)].append(item)
            record_rows.append(
                {
                    key: value
                    for key, value in item.items()
                    if key
                    not in {
                        "bundle",
                        "manifest",
                        "trust",
                        "record_dir",
                        "manifest_sha256",
                        "trust_sha256",
                    }
                }
            )

    signals = pd.concat(signal_frames, ignore_index=True)
    records = pd.DataFrame(record_rows)
    signal_path = output_dir / "study38.full.signals.parquet"
    record_path = output_dir / "study38.full.records.parquet"
    signals.to_parquet(signal_path, index=False)
    records.to_parquet(record_path, index=False)
    comparisons = []
    releases = []
    policy = ReleasePolicy(policy_id="study38-full-record-timing")
    engine = ComparabilityPolicyEngine()
    for subject_id, subject_records in sorted(records_by_subject.items()):
        if len(subject_records) < 2:
            raise ValueError("comparability requires two cycles per subject")
        reference = subject_records[0]
        for index, candidate in enumerate(subject_records[1:]):
            assertion = engine.evaluate(
                reference["bundle"],
                candidate["bundle"],
                reference["trust"],
                candidate["trust"],
                ComparabilityRequest(
                    variable="tibialis-anterior.activation-timing",
                    intended_use="activation-timing",
                    left_channel_id="ch-001",
                    right_channel_id="ch-001",
                ),
            )
            comparisons.append(assertion.to_dict())
            targets = [candidate]
            if index == 0:
                targets.append(reference)
            for target in targets:
                decision = policy.decide(
                    target["bundle"],
                    target["manifest"],
                    target["trust"],
                    assertion,
                )
                releases.append(decision.to_dict())
    comparison_path = output_dir / "study38.full.comparability.json"
    release_path = output_dir / "study38.full.releases.json"
    write_json_atomic(str(comparison_path), {"rows": comparisons})
    write_json_atomic(str(release_path), {"rows": releases})
    materialization_seconds = time.perf_counter() - started
    query = _query_benchmark(
        signal_path,
        record_path,
        int(protocol["query_benchmark"]["repeats"]),
        int(protocol["query_benchmark"]["warmups"]),
    )
    table_hashes = {
        path.name: {
            "sha256": sha256_file(str(path)),
            "bytes": path.stat().st_size,
        }
        for path in (
            signal_path,
            record_path,
            comparison_path,
            release_path,
        )
    }
    return {
        "code_commit": state.get("commit"),
        "code_dirty": bool(state.get("dirty")),
        "working_tree_sha256": state.get("working_tree_sha256"),
        "materialization_seconds": materialization_seconds,
        "record_count": len(record_rows),
        "subject_count": len(records_by_subject),
        "timepoint_count": len(signals),
        "signal_value_count": len(signals) * len(protocol["channels"]),
        "channel_count_per_record": len(protocol["channels"]),
        "comparison_count": len(comparisons),
        "release_count": len(releases),
        "conditional_release_count": sum(
            row["status"] == "conditional" for row in releases
        ),
        "source_attestations": attestations,
        "tables": table_hashes,
        "query_benchmark": query,
        "scientific_digest": hashlib.sha256(
            canonical_json_bytes(
                {
                    "records": record_rows,
                    "signal_table_sha256": table_hashes[
                        "study38.full.signals.parquet"
                    ]["sha256"],
                    "record_table_sha256": table_hashes[
                        "study38.full.records.parquet"
                    ]["sha256"],
                    "comparisons": [
                        {
                            key: value
                            for key, value in row.items()
                            if key not in {"evidence_refs"}
                        }
                        for row in comparisons
                    ],
                    "releases": [
                        {
                            key: value
                            for key, value in row.items()
                            if key
                            in {
                                "subject_id",
                                "intended_use",
                                "status",
                                "policy_id",
                                "policy_version",
                                "required_dimensions",
                                "reasons",
                                "conditions",
                            }
                        }
                        for row in releases
                    ],
                }
            )
        ).hexdigest(),
    }


def run_benchmark(
    *,
    source_dir: str,
    work_root: str,
    protocol_path: str,
    positive_control_path: str,
) -> Dict[str, Any]:
    protocol_file = Path(protocol_path).resolve()
    protocol = load_protocol(protocol_file)
    runs = []
    for index in range(int(protocol["reproducibility_runs"])):
        runs.append(
            materialize_once(
                source_dir=Path(source_dir).resolve(),
                output_dir=Path(work_root).resolve() / "run-{}".format(index + 1),
                protocol_path=protocol_file,
                positive_control_path=Path(positive_control_path).resolve(),
            )
        )
    scientific_identical = len({row["scientific_digest"] for row in runs}) == 1
    table_names = [
        "study38.full.signals.parquet",
        "study38.full.records.parquet",
    ]
    table_identical = all(
        len({row["tables"][name]["sha256"] for row in runs}) == 1
        for name in table_names
    )
    summary = {
        key: runs[0][key]
        for key in (
            "subject_count",
            "record_count",
            "timepoint_count",
            "signal_value_count",
            "channel_count_per_record",
            "comparison_count",
            "release_count",
            "conditional_release_count",
        )
    }
    summary.update(
        {
            "reproducibility_runs": len(runs),
            "clean_code_runs": sum(not row["code_dirty"] for row in runs),
            "scientific_digest_identical": scientific_identical,
            "signal_and_record_tables_identical": table_identical,
            "pass": bool(
                summary["subject_count"] == len(protocol["subjects"])
                and summary["record_count"]
                == int(protocol["expected_record_count"])
                and summary["timepoint_count"]
                == int(protocol["expected_timepoint_count"])
                and summary["conditional_release_count"]
                == summary["record_count"]
                and all(not row["code_dirty"] for row in runs)
                and len({row["code_commit"] for row in runs}) == 1
                and scientific_identical
                and table_identical
            ),
        }
    )
    return {
        "schema_version": VERSION,
        "protocol_id": protocol["protocol_id"],
        "protocol_sha256": sha256_file(str(protocol_file)),
        "implementation": "harmo.full_record_benchmark",
        "summary": summary,
        "runs": runs,
        "claim_boundary": protocol["claim_boundary"],
    }
