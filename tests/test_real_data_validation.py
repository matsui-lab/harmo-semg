from __future__ import annotations

import hashlib
import json
import zipfile
from pathlib import Path

import numpy as np
import pytest
import yaml
from scipy.io import savemat

from harmo.real_data_validation import (
    audit_archive_mappings,
    extract_selected_members,
    fetch_source_archive,
    is_safe_zip_member,
    load_source_registry,
    mapping_semantic_issues,
    parse_mapping_sample,
    reconcile_declared_absences,
    summarize_real_data_cohort,
    validate_source_registry,
    verify_source_archive,
)
from harmo.validation_study import file_sha256


ROOT = Path(__file__).resolve().parents[1]


def _zip(path: Path) -> None:
    with zipfile.ZipFile(path, "w") as archive:
        archive.writestr("P01/P01.mat", b"mat")
        archive.writestr(
            "P01/RawData/Level Ground/LG_Walk_Fast/trial_filtered.trc", b"trc"
        )
        archive.writestr("__MACOSX/P01/._P01.mat", b"metadata")


def _asset(path: Path):
    payload = path.read_bytes()
    return {
        "file_id": 1,
        "name": path.name,
        "download_url": "https://example.org/data.zip",
        "size_bytes": len(payload),
        "md5": hashlib.md5(payload).hexdigest(),  # nosec B324: release checksum
    }


def test_source_registry_rejects_unverifiable_assets():
    invalid = {
        "registry_version": "1.1.0",
        "sources": {
            "study": {
                "doi": "10/example",
                "landing_page": "https://example.org",
                "api_record": "https://example.org/api",
                "license": "CC0",
                "assets": {"P01": {"name": "P01.zip"}},
            }
        },
    }
    errors = validate_source_registry(invalid)
    assert any("file_id" in value for value in errors)
    assert any("invalid MD5" in value for value in errors)


def test_archive_verification_checks_official_bytes_and_zip_safety(tmp_path):
    path = tmp_path / "P01.zip"
    _zip(path)
    result = verify_source_archive(path, _asset(path))
    assert result["pass"] is True
    assert result["member_count"] == 3
    assert result["unsafe_members"] == []
    wrong = dict(_asset(path), md5="0" * 32)
    assert verify_source_archive(path, wrong)["pass"] is False


def test_fetch_moves_only_a_verified_archive_into_place(tmp_path):
    source = tmp_path / "source.zip"
    _zip(source)
    asset = dict(_asset(source), download_url=source.as_uri())
    destination = tmp_path / "private-data/P01.zip"
    result = fetch_source_archive(asset, destination)
    assert result["pass"] is True
    assert result["downloaded"] is True
    assert destination.read_bytes() == source.read_bytes()
    assert not destination.with_suffix(".zip.part").exists()


def test_mapping_audit_is_complete_deterministic_and_ignores_macos_metadata(tmp_path):
    path = tmp_path / "P01.zip"
    _zip(path)
    config = {
        "file_mappings": [
            {
                "name": "subject MAT",
                "file_format": "mat",
                "input_path_template": "{sub_id}/{sub_id}.mat",
                "path_patterns": {"sub_id": "(P\\d+)"},
                "session_id_template": "ses-{sub_id}",
            },
            {
                "name": "walk TRC",
                "file_format": "trc",
                "input_path_template": (
                    "{sub_id}/RawData/Level Ground/LG_Walk_{speed_dir}/"
                    "{file_stem}_filtered.trc"
                ),
                "path_patterns": {
                    "sub_id": "(P\\d+)",
                    "speed_dir": "(Fast|Slow)",
                    "file_stem": "([A-Za-z0-9_]+)",
                },
                "session_id_template": "ses-{speed_dir}",
            },
        ]
    }
    first, members = audit_archive_mappings(path, config, "study_32")
    second, _ = audit_archive_mappings(path, config, "study_32")
    assert first == second
    assert [row["matched_files"] for row in first] == [1, 1]
    assert first[0]["subject_id"] == "P01"
    assert first[1]["session_id"] == "ses-Fast"
    assert members == [
        "P01/P01.mat",
        "P01/RawData/Level Ground/LG_Walk_Fast/trial_filtered.trc",
    ]


def test_selected_extraction_rejects_traversal_and_extracts_only_requested(tmp_path):
    path = tmp_path / "P01.zip"
    _zip(path)
    assert is_safe_zip_member("P01/P01.mat") is True
    assert is_safe_zip_member("../escape") is False
    assert is_safe_zip_member("..\\escape") is False
    assert is_safe_zip_member("") is False
    outputs = extract_selected_members(path, ["P01/P01.mat"], tmp_path / "out")
    assert outputs["P01/P01.mat"].read_bytes() == b"mat"
    assert not (tmp_path / "out/__MACOSX").exists()


def test_study32_format_metadata_is_semantically_queryable():
    config = yaml.safe_load(
        (ROOT / "studies/study_32/config.yaml").read_text(encoding="utf-8")
    )
    mappings = [
        item for item in config["file_mappings"] if item.get("enabled", True)
    ]
    assert all(mapping_semantic_issues(item) == [] for item in mappings)
    mat_root = next(item for item in mappings if item["name"] == "MAT Subject Root Data")
    assert mat_root["plugin_options"]["channel_prefix"] == "EMG_ch"


def test_semantic_check_detects_force_plate_typo():
    mapping = {
        "file_format": "mot",
        "static_metadata": {"sensor_type": "Fotion-capture"},
    }
    issues = mapping_semantic_issues(mapping)
    assert any("sensor_type" in value for value in issues)
    assert any("data_type" in value for value in issues)


def test_mat_sample_index_is_converted_to_seconds_from_cited_rate(tmp_path):
    path = tmp_path / "sample.mat"
    savemat(path, {"EMG": np.arange(400, dtype=float).reshape(200, 2)})
    mapping = {
        "file_format": "mat",
        "reader_plugin": "study32_mcos_binary_scan",
        "plugin_options": {
            "candidate_min_rows": 50,
            "prefer_key_patterns": ["EMG"],
            "time_axis_semantics": "sample-index-from-zero",
            "sampling_rate_hz": 2000,
            "sampling_rate_reference": (
                "https://doi.org/10.1038/s41597-023-02679-x"
            ),
        },
    }
    frame, summary = parse_mapping_sample(path, mapping, max_timepoints=3)
    assert frame["time"].drop_duplicates().tolist() == [0.0, 0.0005, 0.001]
    assert summary["time_provenance"] == "source-publication-derived"
    assert summary["time_unit"] == "s"
    assert summary["sampling_rate_hz"] == 2000.0
    assert summary["trust_state"] == "traceable-format-ready"
    assert mapping_semantic_issues(mapping) == []

    missing_reference = dict(mapping, plugin_options={"sampling_rate_hz": 2000})
    issues = mapping_semantic_issues(missing_reference)
    assert any("time_axis_semantics" in value for value in issues)
    assert any("sampling_rate_reference" in value for value in issues)


def test_declared_source_absence_is_explicit_and_cannot_hide_present_data():
    rows = [
        {
            "mapping_id": "study_32-001",
            "selected_member": "P02/data.trc",
            "outcome": "discovered",
        },
        {
            "mapping_id": "study_32-017",
            "selected_member": None,
            "outcome": "missing",
            "error": "no archive member matched the configured template",
        },
    ]
    declaration = {
        "mapping_id": "study_32-017",
        "reason": "not present in the source archive",
        "evidence_basis": "full ZIP inventory",
    }
    reconciled = reconcile_declared_absences(
        rows, {"declared_absent_mappings": [declaration]}
    )
    assert reconciled[1]["outcome"] == "source-absent-declared"
    assert reconciled[1]["declared_absence"] == declaration
    assert reconciled[1]["error"] is None

    with pytest.raises(ValueError, match="declared absent but matches"):
        reconcile_declared_absences(
            rows,
            {
                "declared_absent_mappings": [
                    dict(declaration, mapping_id="study_32-001")
                ]
            },
        )
    with pytest.raises(ValueError, match="unknown mappings"):
        reconcile_declared_absences(
            rows,
            {
                "declared_absent_mappings": [
                    dict(declaration, mapping_id="study_32-999")
                ]
            },
        )


def _cohort_result(digest: str, trust_ready: bool = False):
    return {
        "archive": {"pass": True, "sha256": digest},
        "config": {
            "sha256": "config-digest",
            "enabled_mappings": 18,
            "channel_definitions": {"sha256": "channel-digest"},
        },
        "archive_inventory": {"usable_files": 100, "matched_mappings": 18},
        "conversion": {
            "converted_mappings": 18,
            "declared_absent_mappings": 0,
            "unaccounted_missing_mappings": 0,
            "failed_mappings": 0,
            "normalized_sample_rows": 1000,
            "conditionally_comparable_mappings": 3,
            "semantic_issue_count": 0,
        },
        "duckdb_parquet_audit": {"scaled_rows": 100},
        "reproducibility": {
            "normalized_record_sha256": [digest, digest],
            "pass": True,
        },
        "pass": True,
        "trust_ready": trust_ready,
        "complete_mapping_coverage": True,
    }


def test_cohort_summary_preserves_denominator_and_checks_cross_participant_identity():
    assets = {
        "P01": {"file_id": 1, "name": "P01.zip", "size_bytes": 10, "md5": "1" * 32},
        "P02": {"file_id": 2, "name": "P02.zip", "size_bytes": 20, "md5": "2" * 32},
    }
    source = {"doi": "10/example", "release_version": 2, "license": "CC BY 4.0"}
    summary = summarize_real_data_cohort(
        "study_32",
        source,
        assets,
        {"P01": _cohort_result("a" * 64), "P02": _cohort_result("b" * 64)},
        run_environment={"code_commit": "abc", "code_dirty": "false"},
    )
    assert summary["denominator"]["expected_participants"] == 2
    assert summary["denominator"]["completed_participants"] == 2
    assert summary["conversion"]["converted_mappings"] == 36
    assert summary["cross_participant_checks"]["config_consistent"] is True
    assert summary["cross_participant_checks"]["unique_normalized_digests"] == 2
    assert summary["pass"] is True
    assert summary["trust_ready"] is False
    assert summary["complete_mapping_coverage"] is True
    assert summary["environment"]["code_dirty"] == "false"

    duplicate = summarize_real_data_cohort(
        "study_32",
        source,
        assets,
        {"P01": _cohort_result("a" * 64), "P02": _cohort_result("a" * 64)},
    )
    assert duplicate["cross_participant_checks"][
        "duplicate_normalized_digests"
    ] == [{"normalized_record_sha256": "a" * 64, "asset_ids": ["P01", "P02"]}]
    assert duplicate["pass"] is False

    missing = summarize_real_data_cohort(
        "study_32",
        source,
        assets,
        {"P01": _cohort_result("a" * 64)},
        failures={"P02": "ValueError: checksum mismatch"},
    )
    assert missing["denominator"]["failed_or_missing_participants"] == 1
    assert missing["participant_ledger"][1]["outcome"] == "failed"
    assert missing["pass"] is False

    declared_result = _cohort_result("b" * 64)
    declared_result["config"]["enabled_mappings"] = 18
    declared_result["archive_inventory"]["matched_mappings"] = 17
    declared_result["conversion"]["converted_mappings"] = 17
    declared_result["conversion"]["declared_absent_mappings"] = 1
    declared_result["complete_mapping_coverage"] = False
    declared = summarize_real_data_cohort(
        "study_32",
        source,
        assets,
        {"P01": _cohort_result("a" * 64), "P02": declared_result},
    )
    assert declared["conversion"]["converted_mappings"] == 35
    assert declared["conversion"]["declared_absent_mappings"] == 1
    assert declared["conversion"]["accounted_mappings"] == 36
    assert declared["pass"] is True
    assert declared["complete_mapping_coverage"] is False


def test_committed_p01_pilot_is_bound_to_current_sources_and_config():
    result = json.loads(
        (ROOT / "paper/results/validation/study32_p01_real_data_pilot.json")
        .read_text(encoding="utf-8")
    )
    registry = load_source_registry(ROOT / "paper/source_releases.yaml")
    asset = registry["sources"]["study_32"]["assets"]["P01"]
    config_path = ROOT / "studies/study_32/config.yaml"
    channels_path = ROOT / "studies/study_32/definitions/channels.yaml"

    assert result["scope"] == "single-subject-real-data-pilot-not-confirmatory-A-G"
    assert result["source"]["file_id"] == asset["file_id"]
    assert result["archive"]["expected_md5"] == asset["md5"]
    assert result["archive"]["expected_size_bytes"] == asset["size_bytes"]
    assert result["config"]["sha256"] == file_sha256(config_path)
    assert result["config"]["channel_definitions"]["sha256"] == file_sha256(
        channels_path
    )
    assert result["conversion"]["converted_mappings"] == 18
    assert result["conversion"]["failed_mappings"] == 0
    assert result["conversion"]["conditionally_comparable_mappings"] == 0
    assert result["conversion"]["semantic_issue_count"] == 0
    assert result["reproducibility"]["pass"] is True
    assert result["environment"]["code_dirty"] == "false"
    assert result["pass"] is True
    assert result["trust_ready"] is True
    assert result["complete_mapping_coverage"] is True


def test_study32_release_registry_freezes_all_ten_participant_archives():
    registry = load_source_registry(ROOT / "paper/source_releases.yaml")
    assets = registry["sources"]["study_32"]["assets"]
    assert sorted(assets) == ["P{:02d}".format(index) for index in range(1, 11)]
    assert len({asset["file_id"] for asset in assets.values()}) == 10
    assert len({asset["md5"] for asset in assets.values()}) == 10
    assert sum(asset["size_bytes"] for asset in assets.values()) == 2786913292
    assert assets["P02"]["declared_absent_mappings"][0]["mapping_id"] == (
        "study_32-017"
    )


def test_committed_study32_cohort_has_a_checksum_bound_complete_denominator():
    summary = json.loads(
        (ROOT / "paper/results/validation/study32_real_data_cohort.json")
        .read_text(encoding="utf-8")
    )
    registry = load_source_registry(ROOT / "paper/source_releases.yaml")
    assets = registry["sources"]["study_32"]["assets"]
    config_path = ROOT / "studies/study_32/config.yaml"
    channels_path = ROOT / "studies/study_32/definitions/channels.yaml"

    assert summary["scope"] == (
        "complete-study-real-data-cohort-computational-validation"
    )
    assert summary["denominator"]["participant_ids"] == sorted(assets)
    assert summary["denominator"]["expected_participants"] == 10
    assert summary["denominator"]["completed_participants"] == 10
    assert summary["denominator"]["passed_participants"] == 10
    assert summary["denominator"]["trust_ready_participants"] == 10
    assert summary["denominator"]["complete_mapping_participants"] == 9
    assert summary["conversion"]["enabled_mappings"] == 180
    assert summary["conversion"]["converted_mappings"] == 179
    assert summary["conversion"]["declared_absent_mappings"] == 1
    assert summary["conversion"]["accounted_mappings"] == 180
    assert summary["conversion"]["failed_mappings"] == 0
    assert summary["conversion"]["unaccounted_missing_mappings"] == 0
    assert summary["conversion"]["conditionally_comparable_mappings"] == 0
    assert summary["cross_participant_checks"]["config_sha256"] == [
        file_sha256(config_path)
    ]
    assert summary["cross_participant_checks"][
        "channel_definitions_sha256"
    ] == [file_sha256(channels_path)]
    assert summary["cross_participant_checks"][
        "within_participant_reproducible"
    ] == 10
    assert summary["cross_participant_checks"]["unique_normalized_digests"] == 10
    assert summary["cross_participant_checks"][
        "duplicate_normalized_digests"
    ] == []
    assert summary["environment"]["code_dirty"] == "false"
    assert summary["pass"] is True
    assert summary["trust_ready"] is True
    assert summary["complete_mapping_coverage"] is False

    by_asset = {row["asset_id"]: row for row in summary["participant_ledger"]}
    assert sorted(by_asset) == sorted(assets)
    for asset_id, asset in assets.items():
        row = by_asset[asset_id]
        assert row["file_id"] == asset["file_id"]
        assert row["expected_md5"] == asset["md5"]
        result_path = ROOT / row["result_path"]
        assert result_path.is_file()
        assert file_sha256(result_path) == row["result_sha256"]
        result = json.loads(result_path.read_text(encoding="utf-8"))
        assert result["environment"]["code_commit"] == summary["environment"][
            "code_commit"
        ]
        assert result["environment"]["code_dirty"] == "false"
        assert result["pass"] is True
        assert result["trust_ready"] is True
    assert by_asset["P02"]["declared_absent_mappings"] == 1
    assert by_asset["P02"]["complete_mapping_coverage"] is False
