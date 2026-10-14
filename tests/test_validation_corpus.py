from __future__ import annotations

import hashlib
import io
import json
import zipfile
from copy import deepcopy
from pathlib import Path

import pytest
import yaml
import numpy as np
import pandas as pd
from scipy.io import savemat

from harmo.real_data_validation import mapping_semantic_issues, parse_mapping_sample
from harmo.validation_corpus import (
    create_corpus_reproducibility_manifest,
    download_validation_corpus_assets,
    load_validation_corpus,
    validate_validation_corpus,
    verify_corpus_asset,
)
from harmo.validation_study import load_registry


ROOT = Path(__file__).resolve().parents[1]


def test_committed_corpus_is_checksum_bound_and_covers_exactly_78_mappings():
    registry = load_registry(ROOT / "paper/validation_registry.yaml")
    corpus = load_validation_corpus(
        ROOT / "paper/validation_corpus.yaml", ROOT, registry
    )
    assets = [
        asset
        for study in corpus["studies"].values()
        for asset in study["assets"].values()
    ]
    mappings = [
        mapping_id for asset in assets for mapping_id in asset["expected_mappings"]
    ]
    assert len(corpus["studies"]) == 8
    assert len(assets) == 12
    assert len(mappings) == len(set(mappings)) == 78
    assert all(asset["download_url"].startswith("https://") for asset in assets)
    assert all(asset["checksum_algorithm"] in {"md5", "sha256"} for asset in assets)


def test_corpus_validation_fails_when_a_mapping_or_checksum_is_changed():
    registry = load_registry(ROOT / "paper/validation_registry.yaml")
    value = yaml.safe_load(
        (ROOT / "paper/validation_corpus.yaml").read_text(encoding="utf-8")
    )
    missing = deepcopy(value)
    missing["studies"]["study_43"]["assets"]["s01"]["expected_mappings"].pop()
    errors = validate_validation_corpus(missing, ROOT, registry)
    assert any("coverage differs" in error for error in errors)
    assert any("mapping denominator" in error for error in errors)

    invalid_checksum = deepcopy(value)
    invalid_checksum["studies"]["study_38"]["assets"]["subject1"]["checksum"] = (
        "not-a-checksum"
    )
    errors = validate_validation_corpus(invalid_checksum, ROOT, registry)
    assert any("checksum is invalid" in error for error in errors)


def test_generic_asset_verification_handles_zip_and_direct_file(tmp_path):
    archive_path = tmp_path / "source.zip"
    with zipfile.ZipFile(archive_path, "w") as archive:
        archive.writestr("subject/data.txt", "time\tvalue\n0\t1\n")
    payload = archive_path.read_bytes()
    asset = {
        "size_bytes": len(payload),
        "checksum_algorithm": "md5",
        "checksum": hashlib.md5(payload).hexdigest(),  # nosec B324: source checksum
        "container": "zip",
    }
    verified = verify_corpus_asset(archive_path, asset)
    assert verified["pass"] is True
    assert verified["member_count"] == 1

    direct_path = tmp_path / "source.mat"
    direct_path.write_bytes(b"public-data")
    payload = direct_path.read_bytes()
    direct = {
        "size_bytes": len(payload),
        "checksum_algorithm": "sha256",
        "checksum": hashlib.sha256(payload).hexdigest(),
        "container": "file",
    }
    assert verify_corpus_asset(direct_path, direct)["pass"] is True
    direct["checksum"] = "0" * 64
    assert verify_corpus_asset(direct_path, direct)["pass"] is False


def test_corpus_downloader_verifies_before_atomic_publish(tmp_path):
    payload = b"checksum-bound-public-data"
    asset = {
        "local_name": "source.bin",
        "download_url": "https://example.test/source.bin",
        "size_bytes": len(payload),
        "checksum_algorithm": "sha256",
        "checksum": hashlib.sha256(payload).hexdigest(),
        "container": "file",
    }
    corpus = {"studies": {"study": {"assets": {"asset": asset}}}}

    class Response(io.BytesIO):
        status = 200

        def __enter__(self):
            return self

        def __exit__(self, *_args):
            self.close()

    result = download_validation_corpus_assets(
        corpus, tmp_path, opener=lambda _request, timeout: Response(payload)
    )
    assert result["pass"] is True
    assert result["verified_assets"] == 1
    assert (tmp_path / "source.bin").read_bytes() == payload
    assert not (tmp_path / "source.bin.part").exists()


def test_corpus_reproducibility_manifest_is_portable_and_checksum_complete(
    tmp_path, monkeypatch
):
    monkeypatch.setattr(
        "harmo.validation_corpus._runtime_versions",
        lambda: {
            "harmo": "0.3.0",
            "numpy": "1.26.4",
            "pandas": "2.3.3",
            "scipy": "1.15.3",
            "PyYAML": "6.0.3",
            "duckdb": "1.5.4",
            "ezc3d": "1.7.0",
            "pyarrow": "24.0.0",
        },
    )
    results = tmp_path / "results"
    results.mkdir()
    normalized_digest = "a" * 64
    summary = {
        "scope": "frozen-78-mapping-corpus",
        "trust_ready": True,
        "pass": True,
        "normalized_record_sha256": [normalized_digest, normalized_digest],
        "environment": {"code_commit": "b" * 40, "code_dirty": "false"},
        "source_outputs": {"json": "/tmp/source.json"},
        "mapping_outputs": {"json": "/tmp/mapping.json"},
    }
    sources = [
        {
            "study_id": "study-{:02d}".format(index),
            "asset_id": "asset",
            "sha256": "{:064x}".format(index),
            "size_bytes": index,
            "pass": True,
        }
        for index in range(1, 13)
    ]
    mappings = [
        {
            "mapping_id": "mapping-{:03d}".format(index),
            "source_sha256": "{:064x}".format(index),
            "config_sha256": "c" * 64,
            "normalized_sample_sha256": normalized_digest,
            "repeat_normalized_sample_sha256": normalized_digest,
            "trust_state": "traceable-format-ready",
            "semantic_issues": [],
        }
        for index in range(1, 79)
    ]
    (results / "corpus_execution.json").write_text(
        json.dumps(summary), encoding="utf-8"
    )
    (results / "source_inventory.json").write_text(
        json.dumps(sources), encoding="utf-8"
    )
    (results / "mapping_outcomes.json").write_text(
        json.dumps(mappings), encoding="utf-8"
    )
    registry = {
        "experiments": {
            "D": {
                "environments": ["macOS-native", "github-actions-ubuntu"],
                "confirmatory_scope": "frozen-78-mapping-corpus",
            }
        }
    }
    manifest = create_corpus_reproducibility_manifest(
        ROOT,
        registry,
        {"mapping_denominator": 78},
        results,
        "macOS-native",
        "macOS-native-1",
    )
    assert manifest["pass"] is True
    assert manifest["validation_scope"] == "frozen-78-mapping-corpus"
    assert manifest["source_ledger_sha256"]
    assert manifest["mapping_ledger_sha256"]
    assert "/tmp" not in json.dumps(manifest)
    json.dumps(manifest, allow_nan=False)

    mismatched_runtime = dict(manifest["environment"]["packages"])
    mismatched_runtime["numpy"] = "9.9.9"
    monkeypatch.setattr(
        "harmo.validation_corpus._runtime_versions", lambda: mismatched_runtime
    )
    rejected = create_corpus_reproducibility_manifest(
        ROOT,
        registry,
        {"mapping_denominator": 78},
        results,
        "macOS-native",
        "macOS-native-mismatched-dependencies",
    )
    assert rejected["dependency_lock_pass"] is False
    assert rejected["pass"] is False


def test_multirate_csv_uses_and_checks_source_header_rates(tmp_path, monkeypatch):
    path = tmp_path / "trial.csv"
    path.write_text(
        "Sensor 1,Sensor 2\n"
        "EMG (1259Hz),ACC X (148Hz)\n"
        "3.4146342277526855,2\n3.6890244483947754,4\n",
        encoding="utf-8",
    )
    mapping = {
        "file_format": "csv",
        "reader_options": {"sep": ",", "header": [0, 1]},
        "multi_rate_info": [
            {
                "sampling_rate": 1259,
                "match_type": "contains",
                "pattern": "1259Hz",
            }
        ],
    }
    observed_options = {}
    original_read_csv = pd.read_csv

    def capture_read_csv(*args, **kwargs):
        observed_options.update(kwargs)
        return original_read_csv(*args, **kwargs)

    monkeypatch.setattr("harmo.real_data_validation.pd.read_csv", capture_read_csv)
    frame, summary = parse_mapping_sample(path, mapping, max_timepoints=2)
    assert frame["time"].drop_duplicates().tolist() == pytest.approx(
        [0.0, 1.0 / 1259.0]
    )
    assert frame["value"].tolist() == [
        float("3.4146342277526855"),
        float("3.6890244483947754"),
    ]
    assert summary["sampling_rate_hz"] == 1259.0
    assert summary["sampling_rates_hz"] == [1259.0]
    assert observed_options["engine"] == "python"
    assert observed_options["dtype"] is str
    assert summary["time_provenance"] == "source-file-derived"
    assert summary["trust_state"] == "traceable-format-ready"

    mapping["multi_rate_info"][0]["sampling_rate"] = 1000
    with pytest.raises(ValueError, match="header/config mismatch"):
        parse_mapping_sample(path, mapping, max_timepoints=2)


def test_explicit_mat_plugin_preserves_source_rate_and_applies_channel_prefix(tmp_path):
    path = tmp_path / "subject.mat"
    savemat(
        path,
        {
            "s": {
                "Data": {"EMG": np.arange(12, dtype=float).reshape(6, 2)},
                "EMGFreq": 1000,
                "EMGSide": "RX ",
                "EmgVarName": np.asarray(["TA ", "SOL "]),
            }
        },
    )
    mapping = {
        "file_format": "mat",
        "reader_plugin": "mat_mcos_explicit",
        "mat_data_key_template": "s.Data.EMG",
        "reader_options": {
            "fs_key": "s.EMGFreq",
            "channel_names_key": "s.EmgVarName",
            "extra_metadata_keys": {"side": "s.EMGSide"},
        },
        "column_transform": {
            "target_col": "channel",
            "prepend_col": "side",
            "separator": "_",
        },
    }
    frame, summary = parse_mapping_sample(path, mapping, max_timepoints=3)
    assert sorted(frame["channel"].unique()) == ["RX_SOL", "RX_TA"]
    assert frame["time"].drop_duplicates().tolist() == [0.0, 0.001, 0.002]
    assert summary["sampling_rate_hz"] == 1000.0
    assert summary["sampling_rate_reference"] == "source-file-key:s.EMGFreq"
    assert summary["trust_state"] == "traceable-format-ready"


def test_explicit_mat_plugin_uses_embedded_table_time_column(tmp_path):
    path = tmp_path / "table.mat"
    savemat(
        path,
        {
            "Data": np.asarray([[1.0, 10.0], [1.005, np.nan], [1.010, 30.0]]),
            "Names": np.asarray(["Header", "signal"]),
        },
    )
    mapping = {
        "file_format": "mat",
        "reader_plugin": "mat_mcos_explicit",
        "reader_options": {
            "data_key": "Data",
            "channel_names_key": "Names",
            "embedded_time_column_candidates": ["Header"],
            "time_unit": "s",
        },
    }
    frame, summary = parse_mapping_sample(path, mapping, max_timepoints=3)
    assert frame["channel"].unique().tolist() == ["signal"]
    assert frame["time"].tolist() == pytest.approx([1.0, 1.005, 1.010])
    assert len(frame) == 3
    assert frame["value"].isna().sum() == 1
    assert summary["nonfinite_value_rows"] == 1
    assert summary["time_provenance"] == "source-file-derived"
    assert summary["time_axis_semantics"] == "source-file-time-vector"
    assert summary["sampling_rate_reference"] == "source-file-key:table-column:Header"
    assert summary["trust_state"] == "traceable-format-ready"


def test_study33_replaces_absent_static_mapping_with_source_present_ik():
    config = yaml.safe_load(
        (ROOT / "studies/study_33/config.yaml").read_text(encoding="utf-8")
    )
    mappings = [item for item in config["file_mappings"] if item.get("enabled", True)]
    assert len(mappings) == 13
    assert not any("/static/" in item["input_path_template"] for item in mappings)
    ik = next(
        item for item in mappings if item["name"] == "Levelground Inverse Kinematics"
    )
    assert "/levelground/ik/" in ik["input_path_template"]
    assert all(mapping_semantic_issues(item) == [] for item in mappings)
