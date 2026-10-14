from copy import deepcopy
from pathlib import Path

import pytest
import yaml

from harmo.evidence.anatomy_crosswalk import (
    VerifiedAnatomyCrosswalk,
    verify_anatomy_crosswalk,
)
from harmo.evidence.io import sha256_file
from harmo.evidence.models import Comparability, OntologyTerm
from harmo.evidence.policy import ComparabilityPolicyEngine
from test_evidence_contracts import bundle, passing_trust
from test_evidence_policy import _request, _semantic_bundle


def _other_bundle():
    value = deepcopy(bundle())
    value.recording_id = "study-2/sub-2/ses-1/emg"
    value.study_id = "study-2"
    value.subject_id = "sub-2"
    return value


def _verified_fixture(tmp_path: Path, predicate: str = "exactMatch"):
    obo = tmp_path / "anatomy.obo"
    obo.write_text(
        "\n".join(
            [
                "format-version: 1.2",
                "data-version: test/releases/1/anatomy.owl",
                "",
                "[Term]",
                "id: TEST:1",
                "name: tibialis anterior",
                'synonym: \"tibialis anticus\" EXACT []',
                "",
            ]
        ),
        encoding="utf-8",
    )
    crosswalk = {
        "schema_version": "1.0.0",
        "crosswalk_id": "test-crosswalk/1",
        "source_ontology": "HarmoAnatomy",
        "source_version": "1.0.0",
        "target_release": {
            "ontology": "TEST",
            "release_tag": "v1",
            "version_iri": "https://example.test/releases/1/anatomy.owl",
            "data_version": "test/releases/1/anatomy.owl",
            "asset_url": "https://example.test/anatomy.obo",
            "asset_sha256": sha256_file(str(obo)),
            "asset_size_bytes": obo.stat().st_size,
            "license": "CC0",
            "license_url": "https://creativecommons.org/publicdomain/zero/1.0/",
        },
        "entries": [
            {
                "source_term_id": "HARMOMUSCLE:tibialis_anterior",
                "source_label": "tibialis anterior muscle",
                "target_term_id": "TEST:1",
                "target_label": "tibialis anterior",
                "predicate": predicate,
                "match_basis": "preferred-label",
                "matched_text": "tibialis anterior",
            }
        ],
    }
    crosswalk_path = tmp_path / "crosswalk.yaml"
    crosswalk_path.write_text(
        yaml.safe_dump(crosswalk, sort_keys=False), encoding="utf-8"
    )
    return (
        verify_anatomy_crosswalk(str(crosswalk_path), str(obo)),
        crosswalk_path,
        obo,
    )


def test_crosswalk_verification_binds_release_and_terms(tmp_path):
    verified, crosswalk_path, obo = _verified_fixture(tmp_path)

    assert verified.crosswalk_sha256 == sha256_file(str(crosswalk_path))
    assert verified.crosswalk.target_release.asset_sha256 == sha256_file(str(obo))
    assert verified.crosswalk.entries[0].target_term.term_id == "TEST:1"
    assert verified.evidence_ref.endswith(verified.crosswalk_sha256)


def test_verified_crosswalk_cannot_be_fabricated(tmp_path):
    verified, _, _ = _verified_fixture(tmp_path)

    with pytest.raises(ValueError, match="must be created"):
        VerifiedAnatomyCrosswalk(
            verified.crosswalk,
            verified.crosswalk_sha256,
            verified.ontology_asset_path,
        )


def test_crosswalk_rejects_asset_and_expected_crosswalk_checksum_faults(tmp_path):
    _, crosswalk_path, obo = _verified_fixture(tmp_path)
    obo.write_text(obo.read_text(encoding="utf-8") + "\n", encoding="utf-8")

    with pytest.raises(ValueError, match="asset checksum mismatch"):
        verify_anatomy_crosswalk(str(crosswalk_path), str(obo))
    with pytest.raises(ValueError, match="crosswalk checksum mismatch"):
        verify_anatomy_crosswalk(
            str(crosswalk_path),
            str(obo),
            expected_crosswalk_sha256="0" * 64,
        )


def test_crosswalk_rejects_unsupported_label_claim(tmp_path):
    _, crosswalk_path, obo = _verified_fixture(tmp_path)
    value = yaml.safe_load(crosswalk_path.read_text(encoding="utf-8"))
    value["entries"][0]["target_label"] = "wrong label"
    crosswalk_path.write_text(
        yaml.safe_dump(value, sort_keys=False), encoding="utf-8"
    )

    with pytest.raises(ValueError, match="label differs"):
        verify_anatomy_crosswalk(str(crosswalk_path), str(obo))


def test_verified_exact_crosswalk_authorizes_cross_ontology_comparison(tmp_path):
    verified, _, _ = _verified_fixture(tmp_path)
    left = _semantic_bundle(bundle(), task_source="Walking")
    right = _semantic_bundle(_other_bundle(), task_source="Walking")
    right.channels[0].anatomy.ontology_term = OntologyTerm(
        "TEST",
        "https://example.test/releases/1/anatomy.owl",
        "TEST:1",
        "tibialis anterior",
    )

    assertion = ComparabilityPolicyEngine([verified]).evaluate(
        left,
        right,
        passing_trust(left.recording_id),
        passing_trust(right.recording_id),
        _request(),
    )

    assert assertion.status == Comparability.CONDITIONAL
    assert verified.evidence_ref in assertion.evidence_refs
    assert "cross-ontology" not in assertion.rationale


def test_close_crosswalk_does_not_authorize_equivalence(tmp_path):
    verified, _, _ = _verified_fixture(tmp_path, predicate="closeMatch")
    left = _semantic_bundle(bundle(), task_source="Walking")
    right = _semantic_bundle(_other_bundle(), task_source="Walking")
    right.channels[0].anatomy.ontology_term = OntologyTerm(
        "TEST",
        "https://example.test/releases/1/anatomy.owl",
        "TEST:1",
        "tibialis anterior",
    )

    assertion = ComparabilityPolicyEngine([verified]).evaluate(
        left,
        right,
        passing_trust(left.recording_id),
        passing_trust(right.recording_id),
        _request(),
    )

    assert assertion.status == Comparability.UNASSESSED
    assert "cross-ontology" in assertion.rationale
