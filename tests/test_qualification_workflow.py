import json
import subprocess
import sys
from pathlib import Path

import jsonschema

from harmo.evidence.io import (
    read_json,
    sha256_contract,
    sha256_file,
    write_json_atomic,
)
from harmo.evidence.models import (
    AnatomicalEntity,
    HarmoRecordingBundle,
    OntologyTerm,
    SemanticMapping,
    TaskDescriptor,
)
from test_evidence_contracts import bundle, passing_trust


def _reference(tmp_path):
    reference = HarmoRecordingBundle.from_dict(bundle().to_dict())
    reference.recording_id = "reference/sub-001/ses-001/emg"
    reference.study_id = "reference"
    reference.subject_id = "sub-001"
    bundle_path = write_json_atomic(
        str(tmp_path / "reference.bundle.json"), reference
    )
    trust_path = write_json_atomic(
        str(tmp_path / "reference.trust.json"),
        passing_trust(reference.recording_id),
    )
    return bundle_path, trust_path


def _config(intended_use="activation-timing"):
    return {
        "recording_id": "study-new/sub-002/ses-001/emg",
        "study_id": "study-new",
        "subject_id": "sub-002",
        "session_id": "ses-001",
        "source_uri": "https://example.test/original-public-recording.mat",
        "source_dataset_release": "test-release-v1",
        "time_column": "time",
        "channel_units": {"TA": "V", "GM": "V"},
        "channel_metadata": {
            "TA": {
                "muscle": "tibialis-anterior",
                "side": "left",
            },
            "GM": {
                "muscle": "gastrocnemius-medialis",
                "side": "left",
            },
        },
        "validation_profile": {
            "expected_sampling_rate_hz": 1000.0,
            "expected_start_seconds": 0.0,
            "units_by_sensor_type": {"surface-emg": ["V"]},
            "required_channel_names": ["TA", "GM"],
        },
        "comparison": {
            "variable": "tibialis-anterior.{}".format(intended_use),
            "intended_use": intended_use,
            "reference_channel_id": "ch-1",
            "recording_channel_id": "ch-001",
        },
        "release_policy": {
            "policy_id": "test-ai-ready",
            "policy_version": "1.0.0",
            "required_dimensions": [
                "semantic_fidelity",
                "scientific_validity",
                "computational_reproducibility",
                "traceability",
            ],
            "allow_trust_warnings": True,
        },
    }


def _semantic_mapping(identifier):
    return {
        "method": "curated-source-dictionary",
        "mapping_version": "test-mapping/1.0.0",
        "status": "mapped",
        "evidence_refs": [
            "mapping:{}:sha256:{}".format(identifier, identifier * 64)
        ],
        "confidence": 1.0,
    }


def _anatomy(source_label, canonical, term_id, identifier):
    return {
        "entity_type": "muscle",
        "canonical_label": canonical,
        "ontology_term": {
            "ontology": "HarmoAnatomy",
            "ontology_version": "1.0.0",
            "term_id": term_id,
            "label": canonical.replace("-", " ") + " muscle",
        },
        "laterality": "left",
        "source_label": source_label,
        "mapping": _semantic_mapping(identifier),
    }


def _semantic_config():
    value = _config()
    value["task"] = {
        "activity": "walking",
        "source_label": "LevelWalking",
        "ontology_terms": [
            {
                "ontology": "HED",
                "ontology_version": "8.4.0",
                "term_id": "Walk",
                "label": "Walk",
            }
        ],
        "facets": {
            "terrain": "level",
            "direction": "forward",
            "speed": "self-selected",
            "state": "steady-state",
        },
        "mapping": _semantic_mapping("1"),
        "compatibility_group": "walking/level/forward",
    }
    value["channel_metadata"]["TA"]["anatomy"] = _anatomy(
        "TA",
        "tibialis-anterior",
        "HARMOMUSCLE:tibialis_anterior",
        "2",
    )
    value["channel_metadata"]["GM"]["anatomy"] = _anatomy(
        "GM",
        "gastrocnemius-medialis",
        "HARMOMUSCLE:gastrocnemius_medialis",
        "3",
    )
    return value


def _semantic_reference(tmp_path):
    reference = HarmoRecordingBundle.from_dict(bundle().to_dict())
    reference.recording_id = "reference/sub-001/ses-001/emg"
    reference.study_id = "reference"
    reference.subject_id = "sub-001"
    reference.schema_version = "1.2.0"
    mapping = SemanticMapping(**_semantic_mapping("1"))
    reference.task = TaskDescriptor(
        "walking",
        "Walking",
        [OntologyTerm("HED", "8.4.0", "Walk", "Walk")],
        {
            "terrain": "level",
            "direction": "forward",
            "speed": "self-selected",
            "state": "steady-state",
        },
        mapping,
        "walking/level/forward",
    )
    for index, config in enumerate(
        (
            _anatomy(
                "TA",
                "tibialis-anterior",
                "HARMOMUSCLE:tibialis_anterior",
                "2",
            ),
            _anatomy(
                "GM",
                "gastrocnemius-medialis",
                "HARMOMUSCLE:gastrocnemius_medialis",
                "3",
            ),
        )
    ):
        term = OntologyTerm(**config["ontology_term"])
        entity_mapping = SemanticMapping(**config["mapping"])
        reference.channels[index].anatomy = AnatomicalEntity(
            config["entity_type"],
            config["canonical_label"],
            term,
            config["laterality"],
            config["source_label"],
            entity_mapping,
        )
    reference.__post_init__()
    bundle_path = write_json_atomic(
        str(tmp_path / "reference.semantic.bundle.json"), reference
    )
    trust_path = write_json_atomic(
        str(tmp_path / "reference.semantic.trust.json"),
        passing_trust(reference.recording_id),
    )
    return bundle_path, trust_path


def _run(*args):
    return subprocess.run(
        [sys.executable, "-m", "harmo.cli", *args],
        check=False,
        capture_output=True,
        text=True,
    )


def test_qualification_config_matches_public_schema():
    schema_path = (
        Path(__file__).resolve().parents[1]
        / "schemas"
        / "qualification-config-1.0.0.json"
    )
    schema = json.loads(schema_path.read_text(encoding="utf-8"))
    jsonschema.Draft202012Validator(schema).validate(_config())


def test_semantic_qualification_config_matches_public_schema():
    schema_path = (
        Path(__file__).resolve().parents[1]
        / "schemas"
        / "qualification-config-1.1.0.json"
    )
    schema = json.loads(schema_path.read_text(encoding="utf-8"))
    jsonschema.Draft202012Validator(schema).validate(_semantic_config())


def test_qualify_atlas_materializes_task_and_anatomy(tmp_path):
    source = tmp_path / "recording.csv"
    source.write_text(
        "time,TA,GM\n0,0.1,0.2\n0.001,0.2,0.3\n0.002,0.3,0.4\n",
        encoding="utf-8",
    )
    config = write_json_atomic(
        str(tmp_path / "semantic-qualification.json"),
        _semantic_config(),
    )
    reference_bundle, reference_trust = _semantic_reference(tmp_path)
    output = tmp_path / "qualified-semantic"

    result = _run(
        "qualify-atlas",
        "--source",
        str(source),
        "--config",
        config,
        "--reference-bundle",
        reference_bundle,
        "--reference-trust",
        reference_trust,
        "--output",
        str(output),
    )

    assert result.returncode == 0, result.stderr + result.stdout
    recording = read_json(str(output / "recording.bundle.json"))
    assertion = read_json(str(output / "comparability.assertion.json"))
    assert recording["schema_version"] == "1.2.0"
    assert recording["task"]["activity"] == "walking"
    assert recording["channels"][0]["anatomy"]["ontology_term"]["term_id"] == (
        "HARMOMUSCLE:tibialis_anterior"
    )
    assert assertion["status"] == "conditionally_comparable"
    assert not any("task" in item for item in assertion["conditions"])


def test_qualify_atlas_builds_five_contracts_and_conditional_exchange(tmp_path):
    source = tmp_path / "recording.csv"
    source.write_text(
        "time,TA,GM\n0,0.1,0.2\n0.001,0.2,0.3\n0.002,0.3,0.4\n",
        encoding="utf-8",
    )
    source_asset = tmp_path / "original-public-recording.mat"
    source_asset.write_bytes(b"public-source-bytes")
    config = write_json_atomic(str(tmp_path / "qualification.json"), _config())
    reference_bundle, reference_trust = _reference(tmp_path)
    output = tmp_path / "qualified"

    result = _run(
        "qualify-atlas",
        "--source",
        str(source),
        "--source-asset",
        str(source_asset),
        "--config",
        config,
        "--reference-bundle",
        reference_bundle,
        "--reference-trust",
        reference_trust,
        "--output",
        str(output),
    )

    assert result.returncode == 0, result.stderr + result.stdout
    summary = json.loads(result.stdout)
    assert summary["release_status"] == "conditional"
    assert summary["exchange_exported"] is True
    for name in (
        "recording.bundle.json",
        "evidence.manifest.json",
        "trust.report.json",
        "comparability.assertion.json",
        "release.decision.json",
    ):
        assert (output / name).is_file()
    assert (output / "exchange" / "atlas.exchange.json").is_file()
    manifest = read_json(str(output / "evidence.manifest.json"))
    assert {step["step_id"] for step in manifest["steps"]} == {
        "normalize",
        "describe",
        "assess",
        "compare",
    }
    assert {item["artifact_id"] for item in manifest["inputs"]} >= {
        "wide-source",
        "source-asset",
    }
    recording = read_json(str(output / "recording.bundle.json"))
    assert recording["sources"][0]["sha256"] == sha256_file(str(source_asset))
    assert recording["sources"][0]["uri"] == _config()["source_uri"]
    assert recording["samples"]["uri"] == (
        "urn:harmo:artifact:normalized-samples"
    )


def test_qualify_atlas_retains_quarantine_without_exchange(tmp_path):
    source = tmp_path / "recording.csv"
    source.write_text(
        "time,TA,GM\n0,0.1,0.2\n0.001,0.2,0.3\n",
        encoding="utf-8",
    )
    config = write_json_atomic(
        str(tmp_path / "qualification.json"),
        _config("amplitude-pooling"),
    )
    reference_bundle, reference_trust = _reference(tmp_path)
    output = tmp_path / "qualified"

    result = _run(
        "qualify-atlas",
        "--source",
        str(source),
        "--config",
        config,
        "--reference-bundle",
        reference_bundle,
        "--reference-trust",
        reference_trust,
        "--output",
        str(output),
    )

    assert result.returncode == 0, result.stderr + result.stdout
    summary = json.loads(result.stdout)
    assert summary["release_status"] == "quarantine"
    assert summary["exchange_exported"] is False
    assert (output / "release.decision.json").is_file()
    assert not (output / "exchange").exists()


def test_qualify_atlas_rejects_unknown_config_before_writing_output(tmp_path):
    source = tmp_path / "recording.csv"
    source.write_text(
        "time,TA,GM\n0,0.1,0.2\n0.001,0.2,0.3\n",
        encoding="utf-8",
    )
    invalid = _config()
    invalid["sampling_raet_hz"] = 1000.0
    config = write_json_atomic(
        str(tmp_path / "invalid-qualification.json"), invalid
    )
    reference_bundle, reference_trust = _reference(tmp_path)
    output = tmp_path / "qualified"

    result = _run(
        "qualify-atlas",
        "--source",
        str(source),
        "--config",
        config,
        "--reference-bundle",
        reference_bundle,
        "--reference-trust",
        reference_trust,
        "--output",
        str(output),
    )

    assert result.returncode == 1
    assert "schema validation failed" in result.stdout
    assert not output.exists()


def test_recording_identity_is_independent_of_work_directory(tmp_path):
    source = tmp_path / "recording.csv"
    source.write_text(
        "time,TA,GM\n0,0.1,0.2\n0.001,0.2,0.3\n",
        encoding="utf-8",
    )
    source_asset = tmp_path / "original.mat"
    source_asset.write_bytes(b"source")
    config = write_json_atomic(str(tmp_path / "qualification.json"), _config())
    reference_bundle, reference_trust = _reference(tmp_path)
    outputs = [tmp_path / "run-a", tmp_path / "run-b"]

    for output in outputs:
        result = _run(
            "qualify-atlas",
            "--source",
            str(source),
            "--source-asset",
            str(source_asset),
            "--config",
            config,
            "--reference-bundle",
            reference_bundle,
            "--reference-trust",
            reference_trust,
            "--output",
            str(output),
        )
        assert result.returncode == 0, result.stderr + result.stdout

    left = read_json(str(outputs[0] / "recording.bundle.json"))
    right = read_json(str(outputs[1] / "recording.bundle.json"))
    assert sha256_contract(left) == sha256_contract(right)
