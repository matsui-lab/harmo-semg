#!/usr/bin/env python3
"""Run the R/M/T/C/D workflow on two checksum-verified Study 38 recordings."""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any, Dict, Tuple

import numpy as np
import pandas as pd
from scipy.io import loadmat

ROOT = Path(__file__).resolve().parents[2]
SRC = ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from harmo.evidence.adapters import bundle_from_wide_dataframe  # noqa: E402
from harmo.evidence.io import (  # noqa: E402
    read_json,
    sha256_contract,
    write_json_atomic,
)
from harmo.evidence.run import current_repository_state  # noqa: E402
from harmo.evidence.validation import (  # noqa: E402
    RecordingProfile,
    assess_bundle,
)
from harmo.evidence.workflow import (  # noqa: E402
    _augment_workflow_trust,
    qualify_wide_emg,
)
from harmo.physiology_control import (  # noqa: E402
    _asset_attestation,
    load_physiology_protocol,
)


def _text(value: Any) -> str:
    if isinstance(value, np.ndarray) and value.size == 1:
        value = value.item()
    return str(value).strip()


def _first_walking_cycle(path: Path) -> Tuple[pd.DataFrame, float]:
    source = loadmat(path, squeeze_me=True, struct_as_record=False)["s"]
    sampling_rate = float(source.EMGFreq)
    labels = [_text(value) for value in np.atleast_1d(source.EmgVarName)]
    side = _text(source.EMGSide)
    selected = [
        item
        for item in np.atleast_1d(source.Data).ravel()
        if _text(item.Task) == "Walking" and _text(item.Foot) == side
    ]
    if not selected:
        raise ValueError("{} has no same-side Walking cycle".format(path.name))
    emg = np.asarray(selected[0].EMG, dtype=float)
    label_map = {
        "TA": "Tibialis Anterior",
        "GM": "Gastrocnemius Medialis",
    }
    values = {
        name: emg[labels.index(label), :]
        for name, label in label_map.items()
    }
    frame = pd.DataFrame(
        {
            "time": np.arange(emg.shape[1], dtype=float) / sampling_rate,
            **values,
        }
    )
    if not np.isfinite(frame.to_numpy(dtype=float)).all():
        raise ValueError("{} selected cycle contains non-finite values".format(path))
    return frame, sampling_rate


def _config(
    subject_id: str, sampling_rate: float, source_uri: str
) -> Dict[str, Any]:
    return {
        "recording_id": "study_38/sub-{}/walking-cycle-001/emg".format(
            subject_id
        ),
        "study_id": "study_38",
        "subject_id": "sub-{}".format(subject_id),
        "session_id": "walking-cycle-001",
        "source_uri": source_uri,
        "source_dataset_release": "10.1038/s41597-019-0323-z",
        "time_column": "time",
        "channel_units": {"TA": "source-unit", "GM": "source-unit"},
        "channel_metadata": {
            "TA": {
                "muscle": "tibialis-anterior",
                "side": "right",
            },
            "GM": {
                "muscle": "gastrocnemius-medialis",
                "side": "right",
            },
        },
        "validation_profile": {
            "expected_sampling_rate_hz": sampling_rate,
            "expected_start_seconds": 0.0,
            "units_by_sensor_type": {"surface-emg": ["source-unit"]},
            "required_channel_names": ["TA", "GM"],
        },
        "comparison": {
            "variable": "tibialis-anterior.activation-timing",
            "intended_use": "activation-timing",
            "reference_channel_id": "ch-001",
            "recording_channel_id": "ch-001",
        },
        "release_policy": {
            "policy_id": "emg-atlas-ai-ready-pilot",
            "policy_version": "1.0.0",
            "required_dimensions": [
                "semantic_fidelity",
                "scientific_validity",
                "computational_reproducibility",
                "traceability",
            ],
            "allow_trust_warnings": True,
            "allow_conditional_comparability": True,
        },
    }


def _prepare_reference(
    frame: pd.DataFrame,
    sampling_rate: float,
    raw_asset: Path,
    source_uri: str,
    work_dir: Path,
) -> Tuple[Path, Path]:
    wide_path = work_dir / "subject1.wide.csv"
    samples_path = work_dir / "subject1.samples.csv"
    timestamps_path = work_dir / "subject1.timestamps.csv"
    frame.to_csv(wide_path, index=False, float_format="%.17g")
    frame[["TA", "GM"]].to_csv(
        samples_path, index=False, float_format="%.17g"
    )
    frame[["time"]].rename(columns={"time": "time_seconds"}).to_csv(
        timestamps_path, index=False, float_format="%.17g"
    )
    config = _config("1", sampling_rate, source_uri)
    bundle, _ = bundle_from_wide_dataframe(
        frame,
        recording_id=config["recording_id"],
        study_id=config["study_id"],
        subject_id=config["subject_id"],
        session_id=config["session_id"],
        source_path=str(raw_asset),
        array_uri=samples_path.as_uri(),
        channel_units=config["channel_units"],
        channel_metadata=config["channel_metadata"],
        array_format="csv",
        array_dtype="float64",
        explicit_timestamps_uri=timestamps_path.as_uri(),
    )
    bundle.samples.uri = "urn:harmo:artifact:normalized-samples"
    bundle.timebase.timestamps_uri = (
        "urn:harmo:artifact:normalized-timestamps"
    )
    bundle.sources[0].uri = source_uri
    bundle.sources[0].dataset_release = config["source_dataset_release"]
    profile = RecordingProfile(
        expected_sampling_rate_hz=sampling_rate,
        expected_start_seconds=0.0,
        units_by_sensor_type={"surface-emg": ["source-unit"]},
        required_channel_names=["TA", "GM"],
        expected_sides_by_channel={"ch-001": "right", "ch-002": "right"},
        expected_muscles_by_channel={
            "ch-001": "tibialis-anterior",
            "ch-002": "gastrocnemius-medialis",
        },
    )
    trust = _augment_workflow_trust(
        assess_bundle(bundle, profile),
        bundle,
        current_repository_state(str(ROOT)),
    )
    bundle_path = Path(
        write_json_atomic(str(work_dir / "reference.bundle.json"), bundle)
    )
    trust_path = Path(
        write_json_atomic(str(work_dir / "reference.trust.json"), trust)
    )
    return bundle_path, trust_path


def run(
    study38_dir: Path,
    work_dir: Path,
    output_path: Path,
    protocol_path: Path,
) -> Dict[str, Any]:
    protocol = load_physiology_protocol(protocol_path)
    work_dir.mkdir(parents=True, exist_ok=True)
    attestations = []
    frames = {}
    rates = {}
    for subject_id in ("1", "2"):
        name = "Subject{}.mat".format(subject_id)
        raw_asset = study38_dir / name
        attestation = _asset_attestation(
            raw_asset, protocol["source_assets"]["study_38"][name]
        )
        retained_attestation = {
            key: attestation[key]
            for key in (
                "bytes",
                "expected_bytes",
                "md5",
                "expected_md5",
                "sha256",
                "pass",
            )
        }
        retained_attestation.update(
            {"subject_id": subject_id, "source_asset": name}
        )
        attestations.append(retained_attestation)
        frames[subject_id], rates[subject_id] = _first_walking_cycle(raw_asset)

    source_uris = {
        subject_id: "https://ndownloader.figshare.com/files/{}".format(
            protocol["source_assets"]["study_38"][
                "Subject{}.mat".format(subject_id)
            ]["file_id"]
        )
        for subject_id in ("1", "2")
    }
    reference_bundle, reference_trust = _prepare_reference(
        frames["1"],
        rates["1"],
        study38_dir / "Subject1.mat",
        source_uris["1"],
        work_dir,
    )
    current_wide = work_dir / "subject2.wide.csv"
    frames["2"].to_csv(current_wide, index=False, float_format="%.17g")
    config_path = Path(
        write_json_atomic(
            str(work_dir / "subject2.qualification.json"),
            _config("2", rates["2"], source_uris["2"]),
        )
    )
    qualification = qualify_wide_emg(
        str(current_wide),
        str(config_path),
        str(reference_bundle),
        str(reference_trust),
        str(work_dir / "subject2-qualified"),
        source_asset_path=str(study38_dir / "Subject2.mat"),
    )
    core_files = {
        key: value
        for key, value in qualification["files"].items()
        if key
        in {
            "recording",
            "manifest",
            "trust",
            "comparability",
            "release_decision",
        }
    }
    manifest_payload = read_json(core_files["manifest"])
    result = {
        "protocol_id": "evidence-qualification-real-data-pilot",
        "protocol_source": str(protocol_path.relative_to(ROOT)),
        "protocol_sha256": protocol["protocol_sha256"],
        "public_dataset": {
            "study_id": "study_38",
            "doi": protocol["required_studies"]["study_38"]["doi"],
            "subjects": ["1", "2"],
            "selected_recording": (
                "first source-ordered Walking stride matching source EMGSide"
            ),
            "channels": ["Tibialis Anterior", "Gastrocnemius Medialis"],
        },
        "source_attestations": attestations,
        "reference": {
            "recording_sha256": sha256_contract(read_json(str(reference_bundle))),
            "trust_sha256": sha256_contract(read_json(str(reference_trust))),
            "sampling_rate_hz": rates["1"],
        },
        "qualified_recording": {
            "recording_id": qualification["recording_id"],
            "sampling_rate_hz": rates["2"],
            "code_commit": manifest_payload["code_commit"],
            "code_dirty": manifest_payload["code_dirty"],
            "working_tree_sha256": manifest_payload["working_tree_sha256"],
            "release_status": qualification["release_status"],
            "exchange_exported": qualification["exchange_exported"],
            "contract_sha256": {
                key: sha256_contract(read_json(value))
                for key, value in core_files.items()
            },
            "comparability": read_json(core_files["comparability"]),
            "release_decision": read_json(core_files["release_decision"]),
        },
        "claim_boundary": (
            "This pilot verifies end-to-end R/M/T/C/D production and "
            "fail-closed conditional export on two checksum-verified public "
            "recordings. It does not materialize all 78 mappings or establish "
            "cross-study amplitude comparability."
        ),
    }
    write_json_atomic(str(output_path), result)
    return result


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--study38-dir", required=True)
    parser.add_argument("--work-dir", required=True)
    parser.add_argument(
        "--protocol",
        default=str(ROOT / "paper/physiology_positive_control_protocol.yaml"),
    )
    parser.add_argument(
        "--output",
        default=str(
            ROOT
            / "paper/results/validation/evidence_qualification_pilot.json"
        ),
    )
    args = parser.parse_args()
    result = run(
        Path(args.study38_dir).resolve(),
        Path(args.work_dir).resolve(),
        Path(args.output).resolve(),
        Path(args.protocol).resolve(),
    )
    print(json.dumps(result, ensure_ascii=False, sort_keys=True, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
