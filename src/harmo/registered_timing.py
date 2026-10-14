#!/usr/bin/env python3
"""Resolve Study 38--Gait120 timing comparability for 20 frozen comparisons."""
from __future__ import annotations

import argparse
import hashlib
import json
from collections import defaultdict
from pathlib import Path
from typing import Any, Dict, List, Mapping, Sequence, Tuple

import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parents[2]

from harmo.evidence.io import (  # noqa: E402
    read_json,
    sha256_contract,
    sha256_file,
    write_json_atomic,
)
from harmo.evidence.models import (  # noqa: E402
    ArrayReference,
    ChannelDescriptor,
    HarmoRecordingBundle,
    SourceAsset,
    TemporalReference,
    Timebase,
    TrustEvidence,
    TrustReport,
    TrustStatus,
)
from harmo.evidence.resolution import resolve_activation_timing  # noqa: E402
from harmo.physiology_control import (  # noqa: E402
    extract_study120_rows,
    extract_study38_rows,
    load_physiology_protocol,
)


# This registered demonstration predates first-class task/anatomy descriptors.
# Keep its historical input contract explicit; silently following the current
# default would label incomplete metadata as a newer semantic representation.
FROZEN_RECORDING_SCHEMA_VERSION = "1.1.0"

MUSCLES = {
    "tibialis anterior": {
        "channel_id": "ch-001",
        "name": "TA",
        "canonical": "tibialis-anterior",
    },
    "medial gastrocnemius": {
        "channel_id": "ch-002",
        "name": "GM",
        "canonical": "gastrocnemius-medialis",
    },
}


def _canonical_digest(value: Any) -> str:
    return hashlib.sha256(
        json.dumps(
            value,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        ).encode("utf-8")
    ).hexdigest()


def _source_uri(study_id: str, row: Mapping[str, Any], protocol: Mapping[str, Any]) -> str:
    if study_id == "study_38":
        asset = protocol["source_assets"]["study_38"][row["source_asset"]]
        return "https://ndownloader.figshare.com/files/{}".format(asset["file_id"])
    return (
        "https://figshare.com/articles/dataset/"
        "Comprehensive_Human_Locomotion_and_Electromyography_Dataset_Gait120/"
        "27677016/1"
    )


def _compatibility_group(
    protocol: Mapping[str, Any], physiology_result: Mapping[str, Any]
) -> str:
    return "g3-cross-study-gait-timing:{}:{}".format(
        protocol["protocol_sha256"][:16],
        physiology_result["result_sha256"][:16],
    )


def _validation_refs(
    protocol_path: Path, physiology_result_path: Path
) -> List[str]:
    return [
        "urn:harmo:protocol:g3-cross-study-gait-physiology:sha256:{}".format(
            sha256_file(str(protocol_path))
        ),
        "urn:harmo:result:g3-cross-study-gait-physiology:sha256:{}".format(
            sha256_file(str(physiology_result_path))
        ),
    ]


def _group_subject_rows(
    rows: Sequence[Mapping[str, Any]]
) -> Dict[str, Dict[str, Mapping[str, Any]]]:
    grouped: Dict[str, Dict[str, Mapping[str, Any]]] = defaultdict(dict)
    for row in rows:
        grouped[str(row["subject_id"])][str(row["muscle"])] = row
    return dict(grouped)


def _write_source_contract(
    study_id: str,
    subject_id: str,
    rows: Mapping[str, Mapping[str, Any]],
    protocol: Mapping[str, Any],
    protocol_path: Path,
    physiology_result_path: Path,
    physiology_result: Mapping[str, Any],
    output: Path,
) -> Tuple[Path, Path, Path]:
    if set(rows) != set(MUSCLES):
        raise ValueError(
            "{} {} does not contain both frozen muscles".format(
                study_id, subject_id
            )
        )
    frame = pd.DataFrame(
        {
            MUSCLES[muscle]["name"]: np.asarray(
                rows[muscle]["values"], dtype=float
            )
            for muscle in MUSCLES
        }
    )
    if frame.shape != (101, 2) or not np.isfinite(
        frame.to_numpy(dtype=float)
    ).all():
        raise ValueError("subject curve must be finite 101x2")
    output.mkdir(parents=True, exist_ok=True)
    samples = output / "source.subject-curve.csv"
    frame.to_csv(samples, index=False, float_format="%.17g")
    first = rows["tibialis anterior"]
    raw_digest = str(first["source_asset_sha256"])
    source_asset = str(first["source_asset"])
    if study_id == "study_38":
        expected = protocol["source_assets"]["study_38"][source_asset]
        raw_size = int(expected["bytes"])
        dataset_release = "10.1038/s41597-019-0323-z"
    else:
        expected = protocol["source_assets"]["study_120"][
            "Gait120_011_to_020.zip"
        ]
        raw_size = int(expected["bytes"])
        dataset_release = "10.1038/s41597-025-05391-0"
    recording_id = "{}/sub-{}/subject-level-gait-envelope/emg".format(
        study_id, subject_id
    )
    bundle = HarmoRecordingBundle(
        recording_id=recording_id,
        study_id=study_id,
        subject_id="sub-{}".format(subject_id),
        session_id="subject-level-gait-envelope",
        samples=ArrayReference(
            uri=samples.as_uri(),
            format="csv",
            dtype="float64",
            shape=[101, 2],
            checksum=sha256_file(str(samples)),
        ),
        timebase=Timebase(
            sampling_rate_hz=None,
            sample_count=101,
            start_seconds=0.0,
            clock="gait-cycle-phase",
        ),
        channels=[
            ChannelDescriptor(
                details["channel_id"],
                details["name"],
                "within-cycle-max",
                "surface-emg",
                muscle=details["canonical"],
                side="right",
                source_name=muscle,
            )
            for muscle, details in MUSCLES.items()
        ],
        sources=[
            SourceAsset(
                uri="urn:harmo:g3-subject-curve:{}:{}".format(
                    study_id, subject_id
                ),
                sha256=sha256_file(str(samples)),
                size_bytes=samples.stat().st_size,
                media_type="text/csv",
                dataset_release=protocol["protocol_id"],
            ),
            SourceAsset(
                uri=_source_uri(study_id, first, protocol),
                sha256=raw_digest,
                size_bytes=raw_size,
                media_type=(
                    "application/zip"
                    if study_id == "study_120"
                    else "application/x-matlab-data"
                ),
                dataset_release=dataset_release,
            ),
        ],
        temporal_reference=TemporalReference(
            axis="gait-cycle-percent",
            unit="%gait-cycle",
            start=0.0,
            end=100.0,
            sample_count=101,
            landmark_start="source-defined-ipsilateral-gait-cycle-start",
            landmark_end=(
                "source-defined-next-ipsilateral-gait-cycle-start"
            ),
            side="right",
            method=(
                "Study38 source Foot=EMGSide Walking cycle"
                if study_id == "study_38"
                else "Gait120 source-accepted right LevelWalking step"
            ),
            compatibility_group=_compatibility_group(
                protocol, physiology_result
            ),
            validation_evidence_refs=_validation_refs(
                protocol_path, physiology_result_path
            ),
        ),
        processing_state=None,
        schema_version=FROZEN_RECORDING_SCHEMA_VERSION,
    )
    if first.get('execution_provenance'):
        from harmo.planned_timing_rows import RELEASE
        provenance=Path(first['execution_provenance'])
        bundle.sources.append(SourceAsset(uri=provenance.as_uri(),sha256=sha256_file(str(provenance)),
                                          media_type='application/json',dataset_release=RELEASE))
    evidence = [
        TrustEvidence(
            "semantic_fidelity",
            TrustStatus.PASS,
            "g3.source-declared-label",
            "TA and medial gastrocnemius are bound to source-declared labels",
        ),
        TrustEvidence(
            "scientific_validity",
            TrustStatus.PASS,
            "g3.frozen-positive-control",
            "subject curve follows the frozen cross-study timing protocol",
        ),
        TrustEvidence(
            "computational_reproducibility",
            TrustStatus.PASS,
            "g3.deterministic-subject-curve",
            "subject curve and raw source carry SHA-256 identities",
        ),
        TrustEvidence(
            "traceability",
            TrustStatus.PASS,
            "g3.raw-source-hash",
            "subject curve retains the checksum-verified public source identity",
        ),
    ]
    trust = TrustReport(recording_id, evidence)
    bundle_path = Path(
        write_json_atomic(str(output / "source.bundle.json"), bundle)
    )
    trust_path = Path(
        write_json_atomic(str(output / "source.trust.json"), trust)
    )
    return bundle_path, trust_path, samples


def _resolution_config(
    muscle: str,
    protocol_path: Path,
    physiology_result_path: Path,
    protocol: Mapping[str, Any],
) -> Dict[str, Any]:
    physiology_result = read_json(str(physiology_result_path))
    if physiology_result.get("positive_control_pass") is not True:
        raise ValueError("frozen physiology positive control must pass")
    compatibility_group = _compatibility_group(protocol, physiology_result)
    processing = {
        "representation": "normalized-envelope",
        "mean_center": False,
        "bandpass_hz": None,
        "butterworth_order": 4,
        "zero_phase": True,
        "rectification": "none",
        "hampel_window_seconds": None,
        "hampel_sigma": 3.0,
        "rms_window_seconds": None,
        "amplitude_normalization": "cycle-max",
    }
    channel = MUSCLES[muscle]
    return {
        "resolution_id": "study38-gait120-{}".format(
            channel["canonical"]
        ),
        "compatibility_group": compatibility_group,
        "intended_use": "activation-timing",
        "variable": "{}.activation-timing".format(channel["canonical"]),
        "common_grid": {
            "points": 101,
            "landmark_start": "source-defined-ipsilateral-gait-cycle-start",
            "landmark_end": "source-defined-next-ipsilateral-gait-cycle-start",
        },
        "validation_evidence": [
            {
                "uri": "urn:harmo:protocol:g3-cross-study-gait-physiology",
                "path": str(protocol_path),
                "sha256": sha256_file(str(protocol_path)),
                "media_type": "application/yaml",
            },
            {
                "uri": "urn:harmo:result:g3-cross-study-gait-physiology",
                "path": str(physiology_result_path),
                "sha256": sha256_file(str(physiology_result_path)),
                "media_type": "application/json",
            },
        ],
        "left": {
            "comparison_channel_id": channel["channel_id"],
            "side": "right",
            "event_method": "Study38 source Foot=EMGSide Walking cycle",
            "processing": processing,
        },
        "right": {
            "comparison_channel_id": channel["channel_id"],
            "side": "right",
            "event_method": "Gait120 source-accepted right LevelWalking step",
            "processing": processing,
        },
        "release_policy": {
            "policy_id": "emg-atlas-cross-study-timing",
            "policy_version": "1.0.0",
            "allow_trust_warnings": False,
            "allow_conditional_comparability": False,
        },
    }


def _run_once(
    repetition: int,
    work_dir: Path,
    left_contracts: Mapping[str, Tuple[Path, Path, Path]],
    right_contracts: Mapping[str, Tuple[Path, Path, Path]],
    protocol_path: Path,
    physiology_result_path: Path,
    protocol: Mapping[str, Any],
) -> List[Dict[str, Any]]:
    left_subjects = [str(value) for value in protocol["required_studies"]["study_38"]["subjects"]]
    right_subjects = [str(value) for value in protocol["required_studies"]["study_120"]["subjects"]]
    if len(left_subjects) != 10 or len(right_subjects) != 10:
        raise ValueError("demonstration requires exactly 10 subjects per study")
    records = []
    for pair_index, (left_subject, right_subject) in enumerate(
        zip(left_subjects, right_subjects), start=1
    ):
        for muscle in MUSCLES:
            pair_dir = (
                work_dir
                / "repeat-{}".format(repetition)
                / "pair-{:02d}".format(pair_index)
                / MUSCLES[muscle]["canonical"]
            )
            pair_dir.mkdir(parents=True, exist_ok=True)
            config = _resolution_config(
                muscle, protocol_path, physiology_result_path, protocol
            )
            config_path = Path(
                write_json_atomic(str(pair_dir / "resolution.json"), config)
            )
            left_bundle, left_trust, left_samples = left_contracts[left_subject]
            right_bundle, right_trust, right_samples = right_contracts[right_subject]
            result = resolve_activation_timing(
                str(left_bundle),
                str(left_trust),
                str(left_samples),
                str(right_bundle),
                str(right_trust),
                str(right_samples),
                str(config_path),
                str(pair_dir / "resolved"),
            )
            after = read_json(result["files"]["comparison_after"])
            left_resolved = read_json(result["files"]["left_bundle"])
            right_resolved = read_json(result["files"]["right_bundle"])
            manifest = read_json(result["files"]["manifest"])
            records.append(
                {
                    "pair_index": pair_index,
                    "study_38_subject": left_subject,
                    "study_120_subject": right_subject,
                    "muscle": muscle,
                    "source_status": result["source_status"],
                    "resolved_status": result["resolved_status"],
                    "conditions_before": result["conditions_before"],
                    "conditions_after": result["conditions_after"],
                    "left_release_status": result["left_release_status"],
                    "right_release_status": result["right_release_status"],
                    "amplitude_comparability": result[
                        "amplitude_comparability"
                    ],
                    "left_recording_sha256": sha256_contract(left_resolved),
                    "right_recording_sha256": sha256_contract(right_resolved),
                    "comparison_status": after["status"],
                    "comparison_conditions": after["conditions"],
                    "code_commit": manifest["code_commit"],
                    "code_dirty": manifest["code_dirty"],
                    "temporal_grid_and_landmarks_equal": all(
                        left_resolved["temporal_reference"][key]
                        == right_resolved["temporal_reference"][key]
                        for key in (
                            "axis",
                            "unit",
                            "start",
                            "end",
                            "sample_count",
                            "landmark_start",
                            "landmark_end",
                            "side",
                            "compatibility_group",
                        )
                    ),
                    "processing_compatibility_group_equal": (
                        left_resolved["processing_state"][
                            "timing_compatibility_group"
                        ]
                        == right_resolved["processing_state"][
                            "timing_compatibility_group"
                        ]
                    ),
                }
            )
    return records


def run(
    study38_dir: Path,
    gait120_archive: Path,
    work_dir: Path,
    protocol_path: Path,
    physiology_result_path: Path,
    output_path: Path,
    planner_replay: Path = None,
) -> Dict[str, Any]:
    protocol = load_physiology_protocol(protocol_path)
    physiology_result = read_json(str(physiology_result_path))
    if (
        physiology_result.get("protocol_sha256")
        != protocol["protocol_sha256"]
        or physiology_result.get("positive_control_pass") is not True
    ):
        raise ValueError(
            "physiology validation result is not a passing result for this protocol"
        )
    study38_rows, study38_attestations = extract_study38_rows(
        study38_dir, protocol
    )
    study120_rows, study120_attestations = extract_study120_rows(
        gait120_archive, protocol
    )
    if planner_replay is not None:
        from harmo.planned_timing_rows import verified_rows
        study38_rows=verified_rows(planner_replay,study38_rows,work_dir/'planner-lineage')
    grouped38 = _group_subject_rows(study38_rows)
    grouped120 = _group_subject_rows(study120_rows)
    contract_root = work_dir / "source-contracts"
    left_contracts = {
        subject: _write_source_contract(
            "study_38",
            subject,
            rows,
            protocol,
            protocol_path,
            physiology_result_path,
            physiology_result,
            contract_root / "study_38" / subject,
        )
        for subject, rows in grouped38.items()
    }
    right_contracts = {
        subject: _write_source_contract(
            "study_120",
            subject,
            rows,
            protocol,
            protocol_path,
            physiology_result_path,
            physiology_result,
            contract_root / "study_120" / subject,
        )
        for subject, rows in grouped120.items()
    }
    runs = [
        _run_once(
            repetition,
            work_dir,
            left_contracts,
            right_contracts,
            protocol_path,
            physiology_result_path,
            protocol,
        )
        for repetition in (1, 2)
    ]
    first, second = runs
    identity_fields = (
        "pair_index",
        "study_38_subject",
        "study_120_subject",
        "muscle",
        "source_status",
        "resolved_status",
        "conditions_before",
        "conditions_after",
        "amplitude_comparability",
        "left_recording_sha256",
        "right_recording_sha256",
        "comparison_status",
        "comparison_conditions",
        "code_commit",
        "code_dirty",
        "temporal_grid_and_landmarks_equal",
        "processing_compatibility_group_equal",
    )
    reproducible = [
        {key: row[key] for key in identity_fields}
        for row in first
    ] == [
        {key: row[key] for key in identity_fields}
        for row in second
    ]
    all_resolved = all(
        row["source_status"] == "conditionally_comparable"
        and row["resolved_status"] == "comparable"
        and row["conditions_before"]
        and not row["conditions_after"]
        and row["amplitude_comparability"] == "not-established"
        and row["temporal_grid_and_landmarks_equal"]
        and row["processing_compatibility_group_equal"]
        for row in first
    )
    source_attestations = []
    for value in study38_attestations + study120_attestations:
        source_attestations.append(
            {
                key: value[key]
                for key in (
                    "study_id",
                    "bytes",
                    "expected_bytes",
                    "md5",
                    "expected_md5",
                    "sha256",
                    "pass",
                )
            }
        )
    result = {
        "schema_version": "1.0.0",
        "protocol_id": "g3-comparability-resolution-demonstration",
        "source_protocol_id": protocol["protocol_id"],
        "source_protocol_sha256": protocol["protocol_sha256"],
        "physiology_result_sha256": sha256_file(
            str(physiology_result_path)
        ),
        "physiology_endpoint_pass": physiology_result[
            "positive_control_pass"
        ],
        "source_attestations": source_attestations,
        "studies": {
            "study_38": {"subjects": len(left_contracts)},
            "study_120": {"subjects": len(right_contracts)},
        },
        "muscles": list(MUSCLES),
        "comparison_count": len(first),
        "common_grid_points": 101,
        "input_representation": (
            "frozen subject-level 101-point gait timing curves from the "
            "checksum-verified physiology positive-control workflow"
        ),
        "code_commit": sorted({row["code_commit"] for row in first}),
        "all_code_clean": all(row["code_dirty"] is False for row in first),
        "all_source_pairs_conditional": all(
            row["source_status"] == "conditionally_comparable"
            for row in first
        ),
        "all_timing_pairs_resolved_comparable": all_resolved,
        "all_amplitude_comparability_not_established": all(
            row["amplitude_comparability"] == "not-established"
            for row in first
        ),
        "independent_work_directories": 2,
        "recording_identities_and_decisions_reproducible": reproducible,
        "comparisons": first,
        "comparison_records_sha256": _canonical_digest(first),
        "claim_boundary": (
            "The executable resolver closes declared temporal and processing "
            "conditions for activation timing on validated subject-level "
            "phase curves in 20 prespecified Study 38--Gait120 subject/muscle "
            "comparisons. It does not establish EMG amplitude comparability, "
            "pooled-effect validity, clinical validity, or OpenSim mechanics."
        ),
    }
    result["result_sha256"] = _canonical_digest(result)
    write_json_atomic(str(output_path), result)
    return result


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--study38-dir", required=True)
    parser.add_argument("--gait120-archive", required=True)
    parser.add_argument("--work-dir", required=True)
    parser.add_argument(
        "--protocol",
        default=str(ROOT / "paper/physiology_positive_control_protocol.yaml"),
    )
    parser.add_argument(
        "--physiology-result",
        default=str(
            ROOT
            / "paper/results/validation/physiology_positive_control.json"
        ),
    )
    parser.add_argument(
        "--output",
        default=str(
            ROOT
            / "paper/results/validation/comparability_resolution_study.json"
        ),
    )
    args = parser.parse_args()
    result = run(
        Path(args.study38_dir).resolve(),
        Path(args.gait120_archive).resolve(),
        Path(args.work_dir).resolve(),
        Path(args.protocol).resolve(),
        Path(args.physiology_result).resolve(),
        Path(args.output).resolve(),
    )
    print(json.dumps(result, ensure_ascii=False, sort_keys=True, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
