from __future__ import annotations

import hashlib
import zipfile
from pathlib import Path

import h5py
import numpy as np
import pytest
import yaml

from harmo.adapters.ultra_mocap import (
    AdapterValidationError,
    run_adapter,
    verify_processed_archive,
)


ROOT = Path(__file__).resolve().parents[1]


def _md5(path: Path) -> str:
    digest = hashlib.md5()
    with path.open("rb") as stream:
        while True:
            chunk = stream.read(1024 * 1024)
            if not chunk:
                break
            digest.update(chunk)
    return digest.hexdigest()


def _fixture(
    tmp_path: Path,
    *,
    extra_label: str = "",
    nonmonotonic: bool = False,
    unsafe_member: bool = False,
    emg_missing_token: str = "",
):
    labels = [
        "time",
        "arm_flex_r",
        "1",
        "EMG1",
        "ACCX1",
        "ACCY1",
        "ACCZ1",
        "GYROX1",
        "GYROY1",
        "GYROZ1",
    ]
    if extra_label:
        labels.append(extra_label)
    hdf5_path = tmp_path / "All_subjects_data.h5"
    string_dtype = h5py.string_dtype(encoding="utf-8")
    with h5py.File(str(hdf5_path), "w") as hdf5:
        for subject in ("subject_1", "subject_2"):
            values = [
                ["0", "1", "0", "0.1", "1", "2", "3", "4", "5", "6"],
                [
                    "0" if nonmonotonic else "0.01",
                    "2",
                    "1",
                    emg_missing_token,
                    "2",
                    "3",
                    "4",
                    "5",
                    "6",
                    "7",
                ],
                ["0.02", "3", "2", "0.3", "3", "4", "5", "6", "7", "8"],
            ]
            if extra_label:
                for row in values:
                    row.append("9")
            dataset = hdf5.create_dataset(
                "{}/AS/F".format(subject),
                data=np.asarray(values, dtype=object),
                dtype=string_dtype,
            )
            dataset.attrs.create(
                "column_names",
                np.asarray(labels, dtype=object),
                dtype=string_dtype,
            )

    archive_path = tmp_path / "processed.zip"
    with zipfile.ZipFile(str(archive_path), "w", zipfile.ZIP_DEFLATED) as archive:
        archive.write(
            str(hdf5_path),
            arcname="ULTra-MoCap-processed/All_subjects_data.h5",
        )
        if unsafe_member:
            archive.writestr("../unsafe.txt", "unsafe")

    protocol = {
        "schema_version": "1.0",
        "protocol_id": "fixture",
        "baseline_commit": "HEAD",
        "dataset": {
            "expected_subjects": ["P01", "P02"],
            "expected_trial_count": 2,
            "expected_tasks": {"Arm Swing": ["Fast"]},
            "article_doi": "fixture-article",
            "repository_doi": "fixture-repository",
            "license": "fixture-license",
        },
        "source_assets": [
            {
                "role": "processed-confirmatory-input",
                "file_id": 1,
                "name": archive_path.name,
                "bytes": archive_path.stat().st_size,
                "md5": _md5(archive_path),
                "materialization": "required",
            },
            {
                "role": "raw-one",
                "file_id": 2,
                "name": "raw-one.zip",
                "bytes": 10,
                "md5": "0" * 32,
                "materialization": "registered-not-required",
            },
            {
                "role": "raw-two",
                "file_id": 3,
                "name": "raw-two.zip",
                "bytes": 20,
                "md5": "1" * 32,
                "materialization": "registered-not-required",
            },
        ],
        "adapter_boundary": {
            "forbidden_core_tokens": ["__heldout_fixture_dataset__"]
        },
        "claim_boundary": "fixture boundary",
    }
    schema = {
        "schema_version": "1.0",
        "dataset_id": "fixture-dataset",
        "hdf5_member_basename": "All_subjects_data.h5",
        "subject_group_pattern": "^subject_([1-2])$",
        "subject_id_template": "P{number:02d}",
        "tasks": {
            "AS": {
                "name": "Arm Swing",
                "conditions": {"F": "Fast"},
            }
        },
        "columns": {
            "source_missing_value_tokens": [""],
            "time": {"exact": "time", "modality": "time"},
            "auxiliary": {
                "exact": ["1"],
                "modality": "source-auxiliary-unresolved",
            },
            "kinematics": {
                "exact": ["arm_flex_r"],
                "modality": "joint-angle",
            },
            "patterns": [
                {"regex": "^(?:IM )?EMG[0-9]+$", "modality": "emg"},
                {"regex": "^ACC[XYZ][0-9]+$", "modality": "imu-accelerometer"},
                {"regex": "^GYRO[XYZ][0-9]+$", "modality": "imu-gyroscope"},
            ],
        },
        "required_trial_modalities": [
            "emg",
            "imu-accelerometer",
            "imu-gyroscope",
            "joint-angle",
        ],
    }
    protocol_path = tmp_path / "protocol.yaml"
    schema_path = tmp_path / "schema.yaml"
    protocol_path.write_text(yaml.safe_dump(protocol), encoding="utf-8")
    schema_path.write_text(yaml.safe_dump(schema), encoding="utf-8")
    return protocol_path, schema_path, archive_path, hdf5_path, protocol


def test_adapter_reads_every_cell_twice_and_retains_nonfinite_counts(tmp_path):
    protocol, schema, archive, hdf5, _ = _fixture(tmp_path)
    result = run_adapter(
        ROOT,
        protocol,
        schema,
        archive,
        hdf5,
        repeats=2,
        chunk_rows=2,
    )
    assert result["metrics"]["pass"] is True
    assert result["metrics"]["source_count"] == 3
    assert result["metrics"]["accounted_source_count"] == 3
    assert result["metrics"]["materialized_source_count"] == 1
    assert result["metrics"]["core_comparison_baseline_commit"] == "HEAD"
    assert result["metrics"]["core_comparison_implementation_commit"] == "HEAD"
    assert result["metrics"]["repeat_manifest_sha256"][0] == (
        result["metrics"]["repeat_manifest_sha256"][1]
    )
    trials = result["trial_manifest"]["trials"]
    assert len(trials) == 2
    assert all(row["nonfinite_value_counts"]["emg"] == 1 for row in trials)
    assert all(
        row["source_missing_value_counts"]["emg"] == 1 for row in trials
    )
    assert all(
        row["source_time"]["sampling_rate_hz"] == pytest.approx(100)
        for row in trials
    )
    assert result["source_manifest"]["assets"][1]["identity_verification"] == (
        "not-performed"
    )


def test_adapter_fails_closed_on_unknown_source_label(tmp_path):
    protocol, schema, archive, hdf5, _ = _fixture(
        tmp_path, extra_label="UNDECLARED"
    )
    with pytest.raises(AdapterValidationError, match="unknown labels"):
        run_adapter(ROOT, protocol, schema, archive, hdf5, repeats=2)


def test_adapter_rejects_nonmonotonic_source_time(tmp_path):
    protocol, schema, archive, hdf5, _ = _fixture(
        tmp_path, nonmonotonic=True
    )
    with pytest.raises(AdapterValidationError, match="not strictly increasing"):
        run_adapter(ROOT, protocol, schema, archive, hdf5, repeats=2)


def test_adapter_rejects_undeclared_missing_value_token(tmp_path):
    protocol, schema, archive, hdf5, _ = _fixture(
        tmp_path, emg_missing_token="NA"
    )
    with pytest.raises(AdapterValidationError, match="non-numeric HDF5 cell"):
        run_adapter(ROOT, protocol, schema, archive, hdf5, repeats=2)


def test_archive_verification_rejects_unsafe_paths(tmp_path):
    protocol_path, _, archive, _, protocol = _fixture(
        tmp_path, unsafe_member=True
    )
    assert protocol_path.exists()
    with pytest.raises(AdapterValidationError, match="unsafe paths"):
        verify_processed_archive(
            archive, protocol, "All_subjects_data.h5"
        )


def test_confirmatory_adapter_requires_two_complete_scans(tmp_path):
    protocol, schema, archive, hdf5, _ = _fixture(tmp_path)
    with pytest.raises(AdapterValidationError, match="at least two scans"):
        run_adapter(ROOT, protocol, schema, archive, hdf5, repeats=1)
