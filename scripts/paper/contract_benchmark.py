#!/usr/bin/env python3
"""Generate the deterministic pre-data benchmark table for the methods paper."""
from __future__ import annotations

import argparse
import json
from pathlib import Path

from harmo.evidence.audit import compare_contracts
from harmo.evidence.models import (
    ArrayReference,
    ChannelDescriptor,
    HarmoRecordingBundle,
    SourceAsset,
    Timebase,
    TrustStatus,
)
from harmo.evidence.validation import RecordingProfile, assess_bundle


def _fixture():
    return HarmoRecordingBundle(
        recording_id="study-1/sub-1/ses-1/emg",
        study_id="study-1",
        subject_id="sub-1",
        session_id="ses-1",
        samples=ArrayReference(
            uri="s3://atlas/signal.zarr",
            format="zarr-v3",
            dtype="float32",
            shape=[100, 2],
            checksum="3" * 64,
        ),
        timebase=Timebase(sampling_rate_hz=1000.0, sample_count=100),
        channels=[
            ChannelDescriptor("ch-1", "TA", "V", "surface-emg"),
            ChannelDescriptor("ch-2", "GM", "V", "surface-emg"),
        ],
        sources=[SourceAsset("https://example.test/raw.c3d", "0" * 64, 123)],
    )


def benchmark():
    faults = []
    cases = [
        ("unit", "semantic_fidelity", lambda value: setattr(value.channels[0], "unit", "mV")),
        ("required_channel", "semantic_fidelity", lambda value: setattr(value.channels[0], "name", "unknown")),
        ("source_hash", "traceability", lambda value: setattr(value.sources[0], "sha256", "")),
        ("array_hash", "traceability", lambda value: setattr(value.samples, "checksum", None)),
    ]
    for name, dimension, inject in cases:
        value = _fixture()
        inject(value)
        report = assess_bundle(value, RecordingProfile(
            units_by_sensor_type={"surface-emg": ["V"]},
            required_channel_names=["TA"],
            require_array_checksum=True,
        ))
        faults.append({
            "fault": name,
            "dimension": dimension,
            "detected": report.dimension_status(dimension) == TrustStatus.FAIL,
        })
    reproduce_pass = compare_contracts(
        {"signal": [0.0, 1.0, 2.0]},
        {"signal": [0.0, 1.0 + 1e-10, 2.0]},
    )["matches"]
    reproduce_fail = not compare_contracts(
        {"signal": [0.0, 1.0, 2.0]},
        {"signal": [0.0, 1.1, 2.0]},
    )["matches"]
    return {
        "benchmark_version": "1.0.0",
        "scope": "synthetic contract and fault-injection pre-data benchmark",
        "fault_detection": faults,
        "faults_detected": sum(item["detected"] for item in faults),
        "faults_total": len(faults),
        "bounded_reproduction_accepted": reproduce_pass,
        "material_difference_rejected": reproduce_fail,
    }


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", required=True)
    args = parser.parse_args()
    result = benchmark()
    target = Path(args.output)
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(json.dumps(result, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    if not all(item["detected"] for item in result["fault_detection"]):
        raise SystemExit(1)
    if not result["bounded_reproduction_accepted"] or not result["material_difference_rejected"]:
        raise SystemExit(1)


if __name__ == "__main__":
    main()
