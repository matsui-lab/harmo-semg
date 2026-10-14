import json
from pathlib import Path
from types import SimpleNamespace

import pandas as pd
import pytest
import yaml

from harmo.atlas_materialization import (
    DEFAULT_READY_MAPPING_IDS,
    _frozen_source_entry,
    _overlay_applies_to_bundle,
    _pair_matches,
    _resolve_retained_timestamps,
    _semantic_evidence_ref,
    _semantic_overlay_inputs,
    _source_release_terms,
    _task_metadata,
    _wide_mapped_frame,
)
from harmo.evidence.semantic_claims import SemanticOverlay, SemanticOverlayEntry


ROOT = Path(__file__).resolve().parents[1]


def test_default_materialization_scope_is_exactly_six_audited_mappings():
    coverage = json.loads(
        (
            ROOT
            / "paper/results/validation/active_mapping_semantic_coverage.json"
        ).read_text(encoding="utf-8")
    )
    ready = {
        row["mapping_id"]
        for row in coverage["mapping_rows"]
        if row["emg_semantics_apply"]
        and row["config_ready_for_recording_materialization"]
    }

    assert set(DEFAULT_READY_MAPPING_IDS) == ready
    assert len(DEFAULT_READY_MAPPING_IDS) == 6


def _overlay(study_id="study_28", sensor_type="surface-emg"):
    return SemanticOverlay(
        overlay_id="semantic-overlay-" + "a" * 20,
        study_id=study_id,
        target_scope_level="channel-group",
        target_scope_selector={"sensor_type": sensor_type},
        report_sha256="b" * 64,
        entries=[
            SemanticOverlayEntry(
                claim_id="article-rate",
                property_path="recording.emg.sampling_rate_hz",
                value=1000,
                claim_scope_level="channel-group",
                claim_scope_selector={"sensor_type": sensor_type},
                propagation="none",
                decision_status="corroborated",
                evidence_refs=["article-rate:sha256:" + "c" * 64],
            )
        ],
        blocked_claims=[],
        skipped_claims=[],
        claim_boundary="No recording values are created.",
    )


def _overlay_bundle():
    return SimpleNamespace(
        study_id="study_28",
        subject_id="SUBJ01",
        session_id="ses-reference",
        recording_id="study_28/example/SUBJ01/ses-reference/abc",
        channels=[
            SimpleNamespace(channel_id="TA", sensor_type="Surface-EMG")
        ],
        processing_state=None,
    )


def test_semantic_overlay_binding_is_explicit_and_non_mutating(tmp_path):
    overlay = _overlay()
    path = tmp_path / "semantic.overlay.json"
    path.write_text(overlay.to_json(), encoding="utf-8")
    bundle = _overlay_bundle()
    before = dict(bundle.__dict__)

    applies, reason = _overlay_applies_to_bundle(overlay, bundle)
    artifacts, bound = _semantic_overlay_inputs(
        bundle, [path], ROOT
    )

    assert applies is True
    assert "explicitly bound" in reason
    assert bound[0].overlay_id == overlay.overlay_id
    assert artifacts[0].artifact_id == overlay.overlay_id
    assert len(artifacts[0].sha256) == 64
    assert bundle.__dict__ == before


def test_semantic_overlay_binding_fails_closed_for_unverifiable_target(tmp_path):
    overlay = _overlay()
    overlay.target_scope_selector["trial_id"] = "trial-01"
    path = tmp_path / "semantic.overlay.json"
    path.write_text(overlay.to_json(), encoding="utf-8")

    applies, reason = _overlay_applies_to_bundle(overlay, _overlay_bundle())

    assert applies is False
    assert "unverifiable" in reason
    with pytest.raises(ValueError, match="cannot bind"):
        _semantic_overlay_inputs(_overlay_bundle(), [path], ROOT)


def test_committed_semantic_overlay_materialization_audit_passes():
    result = json.loads(
        (
            ROOT
            / "paper/results/validation/"
            "semantic_overlay_materialization_audit.json"
        ).read_text(encoding="utf-8")
    )
    assert result["pass"] is True
    assert result["candidate_code_dirty"] is False
    assert len(result["audit_implementation_sha256"]) == 64
    assert result["summary"]["recording_bundle_invariant_count"] == 12
    assert result["summary"]["overlay_checksum_bound_count"] == 12
    assert result["summary"]["trust_dimension_invariant_count"] == 12
    assert result["summary"]["comparison_policy_invariant_count"] == 6
    assert result["summary"]["release_policy_invariant_count"] == 12
    assert result["summary"]["candidate_conditional_release_count"] == 12
    assert result["summary"]["candidate_exchange_export_count"] == 12
    assert result["summary"]["dirty_control_fail_closed"] is True


def test_frozen_source_entry_matches_corpus_identity():
    corpus = yaml.safe_load(
        (ROOT / "paper/validation_corpus.yaml").read_text(encoding="utf-8")
    )
    inventory = json.loads(
        (
            ROOT / "paper/results/validation/source_inventory.json"
        ).read_text(encoding="utf-8")
    )
    asset = corpus["studies"]["study_28"]["assets"]["healthy"]

    row = _frozen_source_entry(inventory, "study_28", "healthy", asset)

    assert row["pass"] is True
    assert row["sha256"] == (
        "6fdbeb911b0d58879d807cb6c806e17d"
        "778bd797f4b34ebe15c4dc2c1ace432f"
    )


def test_frozen_source_entry_rejects_changed_release_checksum():
    corpus = yaml.safe_load(
        (ROOT / "paper/validation_corpus.yaml").read_text(encoding="utf-8")
    )
    inventory = json.loads(
        (
            ROOT / "paper/results/validation/source_inventory.json"
        ).read_text(encoding="utf-8")
    )
    asset = dict(corpus["studies"]["study_28"]["assets"]["healthy"])
    asset["checksum"] = "0" * 32

    with pytest.raises(ValueError, match="does not match"):
        _frozen_source_entry(inventory, "study_28", "healthy", asset)


def test_source_release_terms_are_explicitly_redistributable():
    releases = yaml.safe_load(
        (ROOT / "paper/source_releases.yaml").read_text(encoding="utf-8")
    )

    study_28 = _source_release_terms(releases, "study_28")
    study_33 = _source_release_terms(releases, "study_33")

    assert study_28["license"] == "CC0 1.0"
    assert study_28["redistribution"] == "permitted"
    assert study_33["license"] == "CC BY 4.0"
    assert study_33["redistribution"] == "permitted-with-attribution"
    assert "Camargo" in study_33["attribution"]


def test_source_release_terms_reject_nonpermitted_redistribution():
    releases = {
        "sources": {
            "study_x": {
                "license": "unknown",
                "license_url": "https://example.org",
                "redistribution": "not-permitted",
                "attribution": "Example",
            }
        }
    }

    with pytest.raises(ValueError, match="does not permit"):
        _source_release_terms(releases, "study_x")


def test_pair_selection_retains_task_facets_and_changes_trial():
    mapping = {
        "name": "test ramp EMG",
        "input_path_template": (
            "{sub_id}/ramp_{ramp_grade}_{side}_{trial_num}_{rep}.mat"
        ),
        "path_patterns": {
            "sub_id": "(AB\\d+)",
            "ramp_grade": "(\\d+)",
            "side": "([lr])",
            "trial_num": "(\\d+)",
            "rep": "(\\d+)",
        },
    }
    pairs = [
        ("AB01/ramp_1_l_01_01.mat", "AB01/ramp_1_l_01_01.mat"),
        ("AB01/ramp_1_l_01_02.mat", "AB01/ramp_1_l_01_02.mat"),
        ("AB01/ramp_2_l_01_01.mat", "AB01/ramp_2_l_01_01.mat"),
    ]

    selected = _pair_matches(pairs, mapping)

    assert [item["raw"] for item in selected] == [
        "AB01/ramp_1_l_01_01.mat",
        "AB01/ramp_1_l_01_02.mat",
    ]
    assert selected[0]["groups"]["ramp_grade"] == "1"
    assert selected[1]["groups"]["side"] == "l"


def test_pair_selection_fails_when_only_different_task_facets_exist():
    mapping = {
        "name": "test",
        "input_path_template": "{sub_id}/walk_{speed}_{trial_num}.mat",
        "path_patterns": {
            "sub_id": "(AB\\d+)",
            "speed": "(fast|slow)",
            "trial_num": "(\\d+)",
        },
    }
    pairs = [
        ("AB01/walk_fast_01.mat", "AB01/walk_fast_01.mat"),
        ("AB01/walk_slow_01.mat", "AB01/walk_slow_01.mat"),
    ]

    with pytest.raises(ValueError, match="same task facets"):
        _pair_matches(pairs, mapping)


def test_task_metadata_uses_real_hed_and_checksum_evidence():
    config_path = ROOT / "studies/study_33/config.yaml"
    config = yaml.safe_load(config_path.read_text(encoding="utf-8"))
    mapping = config["file_mappings"][4]
    hed_path = ROOT / "studies/study_33/definitions/hed_task.json"

    task = _task_metadata(
        "study_33",
        mapping,
        {"ramp_grade": "1", "side": "l"},
        config_path,
        hed_path,
    )

    assert task["activity"] == "walking"
    assert task["facets"]["terrain"] == "sloped"
    assert task["facets"]["ramp_grade"] == "1"
    assert task["facets"]["sampling_scope"] == "source-spanning-100-timepoints"
    assert {term["term_id"] for term in task["ontology_terms"]} == {
        "Walk",
        "Sloped-terrain",
    }
    assert all(
        ":sha256:" in value for value in task["mapping"]["evidence_refs"]
    )


def test_semantic_evidence_reference_is_repository_portable():
    config = ROOT / "studies/study_33/config.yaml"

    value = _semantic_evidence_ref(config, "mapping", ROOT)

    assert value.startswith(
        "urn:harmo:repository-file:studies/study_33/config.yaml#mapping:"
    )
    assert "/Users/" not in value


def test_wide_frame_keeps_only_explicitly_qualified_channels():
    normalized = pd.DataFrame(
        {
            "time": [0.0, 0.0, 0.001, 0.001],
            "channel": ["TA", "unknown", "TA", "unknown"],
            "value": [1.0, 2.0, 3.0, 4.0],
        }
    )

    wide = _wide_mapped_frame(normalized, {"TA": {"muscle": "ta"}})

    assert list(wide.columns) == ["time", "TA"]
    assert wide["TA"].tolist() == [1.0, 3.0]


def test_wide_frame_rejects_duplicate_observations():
    normalized = pd.DataFrame(
        {
            "time": [0.0, 0.0],
            "channel": ["TA", "TA"],
            "value": [1.0, 2.0],
        }
    )

    with pytest.raises(ValueError, match="duplicate"):
        _wide_mapped_frame(normalized, {"TA": {"muscle": "ta"}})


def test_irregular_grid_warning_is_resolved_only_after_timestamp_retention(
    tmp_path,
):
    path = tmp_path / "timestamps.csv"
    pd.DataFrame({"time_seconds": [0.0, 0.034, 0.069]}).to_csv(
        path, index=False
    )
    warning = SimpleNamespace(
        code="irregular-timebase",
        severity="warning",
        message="retain timestamps",
        evidence=None,
    )
    bundle = SimpleNamespace(
        timebase=SimpleNamespace(sample_count=3, timestamps_uri="urn:timestamps"),
        warnings=[warning],
    )

    _resolve_retained_timestamps(bundle, path)

    assert warning.severity == "info"
    assert warning.evidence.startswith("sha256:")


def test_timestamp_retention_rejects_non_increasing_grid(tmp_path):
    path = tmp_path / "timestamps.csv"
    pd.DataFrame({"time_seconds": [0.0, 0.034, 0.034]}).to_csv(
        path, index=False
    )
    bundle = SimpleNamespace(
        timebase=SimpleNamespace(sample_count=3, timestamps_uri="urn:timestamps"),
        warnings=[],
    )

    with pytest.raises(ValueError, match="retention verification"):
        _resolve_retained_timestamps(bundle, path)
