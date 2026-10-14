import json
import subprocess
import sys
from copy import deepcopy
from pathlib import Path

import jsonschema
import numpy as np
import pandas as pd
import pytest

from harmo.evidence.io import read_json, sha256_file, write_json_atomic
from harmo.evidence.models import (
    Comparability,
    HarmoRecordingBundle,
    TrustReport,
)
from harmo.evidence.policy import (
    ComparabilityPolicyEngine,
    ComparabilityRequest,
)
from harmo.evidence.resolution import resolve_activation_timing
from test_evidence_contracts import bundle, passing_trust


ROOT = Path(__file__).resolve().parents[1]


def _source_pair(tmp_path):
    phases = np.linspace(0.0, 1.0, 100, endpoint=False)
    values = np.column_stack(
        [
            np.sin(2 * np.pi * phases) + 0.2 * np.sin(6 * np.pi * phases),
            np.cos(2 * np.pi * phases),
        ]
    )
    paths = {}
    bundles = {}
    trusts = {}
    for role, sampling_rate in (("left", 1000.0), ("right", 800.0)):
        sample_path = tmp_path / "{}.samples.csv".format(role)
        pd.DataFrame(values, columns=["TA", "GM"]).to_csv(
            sample_path, index=False, float_format="%.17g"
        )
        current = HarmoRecordingBundle.from_dict(deepcopy(bundle().to_dict()))
        current.recording_id = "study-{}/sub-1/ses-1/emg".format(role)
        current.study_id = "study-{}".format(role)
        current.timebase.sampling_rate_hz = sampling_rate
        current.samples.uri = sample_path.as_uri()
        current.samples.format = "csv"
        current.samples.checksum = sha256_file(str(sample_path))
        bundle_path = write_json_atomic(
            str(tmp_path / "{}.bundle.json".format(role)), current
        )
        trust_path = write_json_atomic(
            str(tmp_path / "{}.trust.json".format(role)),
            passing_trust(current.recording_id),
        )
        paths[role] = sample_path
        bundles[role] = bundle_path
        trusts[role] = trust_path
    return paths, bundles, trusts


def _config(tmp_path):
    evidence = tmp_path / "validation.txt"
    evidence.write_text("validated timing protocol\n", encoding="utf-8")
    processing = {
        "representation": "normalized-envelope",
        "mean_center": True,
        "bandpass_hz": None,
        "butterworth_order": 4,
        "zero_phase": True,
        "rectification": "absolute",
        "hampel_window_seconds": None,
        "hampel_sigma": 3.0,
        "rms_window_seconds": 0.01,
        "amplitude_normalization": "cycle-max",
    }
    return {
        "resolution_id": "test-common-phase",
        "compatibility_group": "test-timing-validation-v1",
        "intended_use": "activation-timing",
        "variable": "tibialis-anterior.activation-timing",
        "common_grid": {
            "points": 101,
            "landmark_start": "ipsilateral-heel-strike",
            "landmark_end": "next-ipsilateral-heel-strike",
        },
        "validation_evidence": [
            {
                "uri": "urn:test:timing-validation",
                "path": evidence.name,
                "sha256": sha256_file(str(evidence)),
                "media_type": "text/plain",
            }
        ],
        "left": {
            "comparison_channel_id": "ch-1",
            "side": "left",
            "event_method": "source-segmented-gait-cycle",
            "processing": processing,
        },
        "right": {
            "comparison_channel_id": "ch-1",
            "side": "left",
            "event_method": "source-segmented-gait-cycle",
            "processing": processing,
        },
        "release_policy": {
            "policy_id": "test-resolved-timing",
            "policy_version": "1.0.0",
            "allow_trust_warnings": True,
        },
    }


def _run_resolution(tmp_path):
    samples, bundles, trusts = _source_pair(tmp_path)
    config_path = write_json_atomic(
        str(tmp_path / "resolution.json"), _config(tmp_path)
    )
    output = tmp_path / "resolved"
    result = resolve_activation_timing(
        bundles["left"],
        trusts["left"],
        str(samples["left"]),
        bundles["right"],
        trusts["right"],
        str(samples["right"]),
        config_path,
        str(output),
    )
    return result, output


def test_resolver_executes_conditions_and_promotes_timing_only(tmp_path):
    result, output = _run_resolution(tmp_path)

    assert result["source_status"] == "conditionally_comparable"
    assert result["resolved_status"] == "comparable"
    assert result["conditions_before"]
    assert result["conditions_after"] == []
    assert result["amplitude_comparability"] == "not-established"
    assert set(result["exchange_files"]) == {"left", "right"}

    resolved = HarmoRecordingBundle.from_dict(
        read_json(str(output / "right" / "recording.bundle.json"))
    )
    assert resolved.schema_version == "1.1.0"
    assert resolved.timebase.sampling_rate_hz is None
    assert resolved.temporal_reference.axis == "gait-cycle-percent"
    assert resolved.temporal_reference.sample_count == 101
    assert resolved.processing_state.amplitude_comparability == "not-established"
    assert resolved.processing_state.source_recording_sha256

    comparison = read_json(str(output / "comparability.after.json"))
    assert comparison["status"] == "comparable"
    assert comparison["conditions"] == []

    amplitude = ComparabilityPolicyEngine().evaluate(
        HarmoRecordingBundle.from_dict(
            read_json(str(output / "left" / "recording.bundle.json"))
        ),
        resolved,
        TrustReport.from_dict(
            read_json(str(output / "left" / "trust.report.json"))
        ),
        TrustReport.from_dict(
            read_json(str(output / "right" / "trust.report.json"))
        ),
        ComparabilityRequest(
            "tibialis-anterior.amplitude",
            "amplitude-pooling",
            "ch-1",
            "ch-1",
        ),
    )
    assert amplitude.status == Comparability.UNASSESSED


def test_resolved_bundle_and_index_match_public_schemas(tmp_path):
    _, output = _run_resolution(tmp_path)
    for schema_name, value_path in (
        (
            "recording-bundle-1.1.0.json",
            output / "left" / "recording.bundle.json",
        ),
        (
            "comparability-resolution-index-1.0.0.json",
            output / "comparability.resolution.index.json",
        ),
    ):
        schema = json.loads(
            (ROOT / "schemas" / schema_name).read_text(encoding="utf-8")
        )
        jsonschema.Draft202012Validator(schema).validate(
            read_json(str(value_path))
        )


def test_resolver_rejects_unverified_validation_evidence(tmp_path):
    samples, bundles, trusts = _source_pair(tmp_path)
    config = _config(tmp_path)
    config["validation_evidence"][0]["sha256"] = "0" * 64
    config_path = write_json_atomic(str(tmp_path / "resolution.json"), config)

    with pytest.raises(ValueError, match="validation evidence checksum mismatch"):
        resolve_activation_timing(
            bundles["left"],
            trusts["left"],
            str(samples["left"]),
            bundles["right"],
            trusts["right"],
            str(samples["right"]),
            config_path,
            str(tmp_path / "resolved"),
        )


def test_resolve_comparability_cli(tmp_path):
    samples, bundles, trusts = _source_pair(tmp_path)
    config_path = write_json_atomic(
        str(tmp_path / "resolution.json"), _config(tmp_path)
    )
    output = tmp_path / "resolved"
    result = subprocess.run(
        [
            sys.executable,
            "-m",
            "harmo.cli",
            "resolve-comparability",
            "--left-bundle",
            bundles["left"],
            "--left-trust",
            trusts["left"],
            "--left-samples",
            str(samples["left"]),
            "--right-bundle",
            bundles["right"],
            "--right-trust",
            trusts["right"],
            "--right-samples",
            str(samples["right"]),
            "--config",
            config_path,
            "--output",
            str(output),
        ],
        check=False,
        capture_output=True,
        text=True,
    )

    assert result.returncode == 0, result.stdout + result.stderr
    summary = json.loads(result.stdout)
    assert summary["resolved_status"] == "comparable"


def test_frozen_cross_study_resolution_result_retains_claim_boundary():
    result = read_json(
        str(
            ROOT
            / "paper/results/validation/"
            "comparability_resolution_study.json"
        )
    )

    assert result["comparison_count"] == 20
    assert result["studies"] == {
        "study_38": {"subjects": 10},
        "study_120": {"subjects": 10},
    }
    assert result["all_code_clean"] is True
    assert result["all_source_pairs_conditional"] is True
    assert result["all_timing_pairs_resolved_comparable"] is True
    assert result["all_amplitude_comparability_not_established"] is True
    assert result["recording_identities_and_decisions_reproducible"] is True
    assert all(
        row["left_release_status"] == "allow"
        and row["right_release_status"] == "allow"
        and row["conditions_before"]
        == [
            "establish a validated processing compatibility group for "
            "activation timing"
        ]
        and row["conditions_after"] == []
        for row in result["comparisons"]
    )
