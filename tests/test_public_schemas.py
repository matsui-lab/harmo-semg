import json
from pathlib import Path

import pytest
import yaml

from harmo.evidence.audit import export_atlas_exchange
from harmo.evidence.models import (
    AnatomicalEntity,
    ArtifactReference,
    Comparability,
    ComparabilityAssertion,
    OntologyTerm,
    OpenSimArtifact,
    SemanticMapping,
    TaskDescriptor,
    TrustEvidence,
    TrustReport,
    TrustStatus,
)
from harmo.evidence.io import read_json
from test_evidence_contracts import (
    ZERO_SHA,
    bundle,
    comparison_for_bundle,
    manifest,
    passing_trust,
    release_for_bundle,
)


jsonschema = pytest.importorskip("jsonschema")
ROOT = Path(__file__).resolve().parents[1]


def test_recording_bundle_matches_published_schema():
    schema = json.loads((ROOT / "schemas/recording-bundle-1.0.0.json").read_text())
    jsonschema.Draft202012Validator(schema).validate(bundle().to_dict())


def test_recording_bundle_1_2_matches_published_schema():
    value = bundle()
    mapping = SemanticMapping(
        "curated-dictionary",
        "test/1.0.0",
        "mapped",
        ["test:mapping:sha256:{}".format("1" * 64)],
        1.0,
    )
    value.schema_version = "1.2.0"
    value.task = TaskDescriptor(
        "walking",
        "LevelWalking",
        [OntologyTerm("HED", "8.4.0", "Walk", "Walk")],
        {"terrain": "level", "direction": "forward"},
        mapping,
        "walking/level/forward",
    )
    value.channels[0].anatomy = AnatomicalEntity(
        "muscle",
        "tibialis-anterior",
        OntologyTerm(
            "HarmoAnatomy",
            "1.0.0",
            "HARMOMUSCLE:tibialis_anterior",
            "tibialis anterior muscle",
        ),
        "left",
        "TA",
        mapping,
    )
    value.__post_init__()

    _validate("recording-bundle-1.2.0.json", value.to_dict())


def _validate(schema_name, value):
    schema = json.loads((ROOT / "schemas" / schema_name).read_text(encoding="utf-8"))
    jsonschema.Draft202012Validator(schema).validate(value)


def test_all_public_contract_examples_match_their_schemas():
    trust = TrustReport(bundle().recording_id, [
        TrustEvidence("traceability", TrustStatus.PASS, "source.hash", "source hashed")
    ])
    comparison = ComparabilityAssertion(
        "study-a", "study-b", "activation-timing", Comparability.CONDITIONAL,
        "different acquisition protocols", ["compare within gait-cycle phase only"],
    )
    opensim = OpenSimArtifact(
        "osim-1", bundle().recording_id,
        ArtifactReference("model", "file:///model.osim", ZERO_SHA, "application/xml"),
        [], ik_rms_error_m=0.01,
    )
    _validate("evidence-manifest-1.0.0.json", manifest().to_dict())
    _validate("trust-report-1.0.0.json", trust.to_dict())
    _validate("comparability-assertion-1.1.0.json", comparison.to_dict())
    _validate("opensim-artifact-1.0.0.json", opensim.to_dict())
    release = release_for_bundle()
    _validate("release-decision-1.0.0.json", release.to_dict())


def test_generated_exchange_index_matches_schema(tmp_path):
    trust = passing_trust()
    comparison = comparison_for_bundle()
    decision = release_for_bundle(trust, comparison)
    files = export_atlas_exchange(
        str(tmp_path), bundle(), manifest(), trust, comparison, decision
    )
    _validate("exchange-2.0.0.json", read_json(files["index"]))


def test_committed_anatomy_crosswalk_matches_published_schema():
    value = yaml.safe_load(
        (
            ROOT
            / "vocabularies/anatomy/"
            "harmo-muscle-to-uberon-2026-06.yaml"
        ).read_text(encoding="utf-8")
    )

    _validate("anatomy-crosswalk-1.0.0.json", value)


def test_frozen_anatomy_audits_match_published_schemas():
    result_dir = ROOT / "paper/results/validation"
    _validate(
        "anatomy-crosswalk-validation-1.0.0.json",
        read_json(str(result_dir / "anatomy_crosswalk_validation.json")),
    )
    _validate(
        "semantic-coverage-audit-1.0.0.json",
        read_json(str(result_dir / "active_mapping_semantic_coverage.json")),
    )
    _validate(
        "semantic-enrichment-report-1.0.0.json",
        read_json(str(result_dir / "literature_semantic_enrichment.json")),
    )
    _validate(
        "semantic-coverage-comparison-1.0.0.json",
        read_json(str(result_dir / "semantic_coverage_comparison.json")),
    )
    for name in (
        "semantic_overlay_study28_emg.json",
        "semantic_overlay_study33_dataset.json",
    ):
        _validate(
            "semantic-overlay-1.0.0.json",
            read_json(str(result_dir / name)),
        )
    _validate(
        "semantic-overlay-materialization-audit-1.0.0.json",
        read_json(
            str(
                result_dir
                / "semantic_overlay_materialization_audit.json"
            )
        ),
    )


def test_published_schemas_are_valid_draft_2020_12():
    for path in (ROOT / "schemas").glob("*.json"):
        schema = json.loads(path.read_text(encoding="utf-8"))
        jsonschema.Draft202012Validator.check_schema(schema)
