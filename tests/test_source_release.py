from pathlib import Path

import yaml

from harmo.real_data_validation import load_source_registry
from harmo.source_release import (
    normalize_figshare_articles,
    source_rights_report,
    summarize_figshare_articles,
    verify_figshare_source,
    verify_mendeley_source,
)
from harmo.validation_study import gate_audit, load_registry


ROOT = Path(__file__).resolve().parents[1]


def _article(article_id=7, license_name="CC BY", license_url=None):
    return {
        "id": article_id,
        "doi": "10.123/example.v1",
        "version": 1,
        "license": {
            "name": license_name,
            "url": license_url or "https://creativecommons.org/licenses/by/4.0/",
        },
        "files": [
            {
                "id": 9,
                "name": "participant.zip",
                "size": 42,
                "computed_md5": "1" * 32,
            }
        ],
    }


def test_figshare_manifest_is_order_stable_and_preserves_license_version():
    first = _article(7)
    second = _article(
        3,
        license_name="CC0",
        license_url="https://creativecommons.org/publicdomain/zero/1.0/",
    )
    forward = summarize_figshare_articles([first, second])
    reverse = summarize_figshare_articles([second, first])
    assert forward == reverse
    assert forward["article_ids"] == [3, 7]
    assert forward["licenses"] == ["CC BY 4.0", "CC0 1.0"]
    assert len(forward["manifest_sha256"]) == 64
    assert normalize_figshare_articles([first])[0]["files"][0]["md5"] == "1" * 32


def test_online_figshare_verification_is_fail_closed():
    article = _article()
    expected = summarize_figshare_articles([article])
    source = {
        "license": "CC BY 4.0",
        "release": {
            "repository": "figshare",
            "record_type": "article",
            "api_record": "https://api.example/articles/7",
            "manifest": expected,
        },
    }
    result = verify_figshare_source("study", source, lambda _url: article)
    assert result["pass"] is True

    changed = dict(article, version=2)
    mismatch = verify_figshare_source("study", source, lambda _url: changed)
    assert mismatch["pass"] is False
    assert mismatch["checks"]["versions"] is False
    assert mismatch["checks"]["manifest_sha256"] is False


def test_online_mendeley_verification_requires_each_doi_version_and_license():
    source = {
        "license": "CC BY 4.0",
        "release": {
            "repository": "mendeley-data",
            "records": [
                {
                    "doi": "10.17632/example.2",
                    "version": 2,
                    "landing_page": "https://data.example/2",
                }
            ],
        },
    }
    page = "DOI: 10.17632/example.2 Version 2 Licence CC BY 4.0"
    assert verify_mendeley_source("study", source, lambda _url: page)["pass"] is True
    assert verify_mendeley_source("study", source, lambda _url: page.replace("2", "1"))[
        "pass"
    ] is False


def test_committed_source_registry_has_rights_evidence_for_all_frozen_studies():
    source_registry = load_source_registry(ROOT / "paper/source_releases.yaml")
    validation_registry = load_registry(ROOT / "paper/validation_registry.yaml")
    study_ids = [study["id"] for study in validation_registry["studies"]]
    report = source_rights_report(source_registry, study_ids)
    assert report["pass"] is True
    assert len(report["studies"]) == 8
    assert all(row["rights_ready"] for row in report["studies"])
    assert all(row["release_identity_ready"] for row in report["studies"])

    by_id = {study["id"]: study for study in validation_registry["studies"]}
    for study_id, source in source_registry["sources"].items():
        assert by_id[study_id]["license"] == source["license"]
        assert by_id[study_id]["redistribution"] == source["redistribution"]


def test_gate_audit_binds_m0_to_source_registry_and_checks_physiology(tmp_path):
    validation_registry = load_registry(ROOT / "paper/validation_registry.yaml")
    result_dir = tmp_path / "paper/results/validation"
    result_dir.mkdir(parents=True)
    source_path = tmp_path / "paper/source_releases.yaml"
    source_path.parent.mkdir(parents=True, exist_ok=True)
    source_path.write_text(
        (ROOT / "paper/source_releases.yaml").read_text(encoding="utf-8"),
        encoding="utf-8",
    )
    (result_dir / "source_release_verification.json").write_text(
        (ROOT / "paper/results/validation/source_release_verification.json").read_text(
            encoding="utf-8"
        ),
        encoding="utf-8",
    )
    audit = gate_audit(validation_registry, result_dir)
    m0 = [row for row in audit["checks"] if row["gate"] == "M0"]
    assert len(m0) == 4
    assert all(row["pass"] for row in m0)
    assert any(
        row["gate"] == "G3"
        and row["message"] == "physiology_positive_control.json is missing"
        and row["pass"] is False
        for row in audit["checks"]
    )


def test_registry_yaml_does_not_claim_pending_rights():
    value = yaml.safe_load(
        (ROOT / "paper/source_releases.yaml").read_text(encoding="utf-8")
    )
    serialized = yaml.safe_dump(value)
    assert "pending-verification" not in serialized
    assert "redistribution: unassessed" not in serialized
