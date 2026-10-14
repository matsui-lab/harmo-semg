import json
from pathlib import Path

import jsonschema
import pytest
import yaml

from harmo.evidence.semantic_claims import (
    SemanticEnrichmentReport,
    SemanticOverlay,
    build_semantic_overlay,
    compare_semantic_coverage,
    run_semantic_enrichment,
)


ROOT = Path(__file__).resolve().parents[1]


ARTICLE_XML = b"""<?xml version="1.0" encoding="UTF-8"?>
<article>
  <body>
    <sec>
      <title>Instrumentation</title>
      <p>A surface EMG system was acquiring at 2000 Hz.</p>
      <p>Signals were recorded from both lower limbs.</p>
    </sec>
  </body>
</article>
"""


def _plan(config_rate=2000, human_review=False):
    return {
        "schema_version": "1.0.0",
        "claim_boundary": (
            "Dataset claims remain dataset-scoped and do not create "
            "subject-specific observations."
        ),
        "sources": [
            {
                "source_id": "article",
                "study_id": "study",
                "source_type": "peer-reviewed-article",
                "source_role": "authority",
                "identifier": "doi:10.1/example",
                "url": "https://example.test/article.xml",
                "format": "jats-xml",
                "release_binding": "doi:10.1/dataset.v1",
                "claims": [
                    {
                        "claim_id": "article-rate",
                        "property_path": "recording.emg.sampling_rate_hz",
                        "scope_level": "channel-group",
                        "scope_selector": {"sensor_type": "surface-emg"},
                        "propagation": "all-descendants",
                        "locator": "Instrumentation",
                        "pattern": r"acquiring at (?P<value>[0-9]+) Hz",
                        "mode": "capture",
                        "value_type": "number",
                        "expected": 2000,
                        "requires_human_review": human_review,
                    }
                ],
            },
            {
                "source_id": "config",
                "study_id": "study",
                "source_type": "repository-config",
                "source_role": "observation",
                "identifier": "repository:config.yaml",
                "path": "config.yaml",
                "format": "yaml",
                "release_binding": "doi:10.1/dataset.v1",
                "claims": [
                    {
                        "claim_id": "config-rate",
                        "property_path": "recording.emg.sampling_rate_hz",
                        "scope_level": "channel-group",
                        "scope_selector": {
                            "sensor_type": "surface-emg",
                            "subject_id": "P01",
                        },
                        "locator": "document",
                        "pattern": r"sampling_rate_hz: (?P<value>[0-9]+)",
                        "mode": "capture",
                        "value_type": "number",
                        "expected": config_rate,
                    }
                ],
            },
        ],
    }


def _run(tmp_path, plan, article=ARTICLE_XML):
    (tmp_path / "config.yaml").write_text(
        "sampling_rate_hz: {}\n".format(
            plan["sources"][1]["claims"][0]["expected"]
        ),
        encoding="utf-8",
    )
    plan_path = tmp_path / "plan.yaml"
    plan_path.write_text(yaml.safe_dump(plan, sort_keys=False), encoding="utf-8")
    report = run_semantic_enrichment(
        plan_path=str(plan_path),
        repository_root=str(tmp_path),
        cache_dir=str(tmp_path / "cache"),
        fetcher=lambda _url: article,
    )
    return report


def test_article_claim_is_corroborated_by_more_specific_repository_evidence(
    tmp_path,
):
    report = _run(tmp_path, _plan())
    assert report.summary["source_acquired"] == 2
    assert report.summary["extracted_claim_count"] == 2
    assert report.summary["corroborated_decision_count"] == 1
    decision = report.decisions[0]
    assert decision.status == "corroborated"
    assert decision.corroborating_claim_ids == ["config-rate"]
    authority = [
        claim for claim in report.claims if claim.claim_id == "article-rate"
    ][0]
    assert authority.scope_level == "channel-group"
    assert authority.scope_selector == {"sensor_type": "surface-emg"}
    assert authority.propagation == "all-descendants"
    assert len(authority.source_sha256) == 64
    assert len(authority.evidence_sha256) == 64


def test_repository_conflict_prevents_literature_promotion(tmp_path):
    plan = _plan(config_rate=1000)
    report = _run(tmp_path, plan)
    decision = report.decisions[0]
    assert decision.status == "conflicting"
    assert decision.conflicting_claim_ids == ["config-rate"]
    assert report.summary["enrichment_ready_count"] == 0


def test_human_review_rule_remains_conditional_even_when_corroborated(tmp_path):
    report = _run(tmp_path, _plan(human_review=True))
    decision = report.decisions[0]
    assert decision.status == "conditional"
    assert decision.corroborating_claim_ids == ["config-rate"]
    assert report.summary["enrichment_ready_count"] == 0


def test_missing_locator_is_unresolved_not_silently_inferred(tmp_path):
    plan = _plan()
    plan["sources"][0]["claims"][0]["locator"] = "Missing section"
    report = _run(tmp_path, plan)
    authority = [
        claim for claim in report.claims if claim.claim_id == "article-rate"
    ][0]
    assert authority.extraction_status == "invalid"
    assert "locator" in authority.message
    assert report.decisions[0].status == "unresolved"


def test_changed_article_value_fails_frozen_expectation(tmp_path):
    changed = ARTICLE_XML.replace(b"2000 Hz", b"1000 Hz")
    report = _run(tmp_path, _plan(), article=changed)
    authority = [
        claim for claim in report.claims if claim.claim_id == "article-rate"
    ][0]
    assert authority.extraction_status == "invalid"
    assert authority.value == 1000
    assert report.decisions[0].status == "unresolved"


def test_remote_digest_mismatch_fails_closed(tmp_path):
    plan = _plan()
    plan["sources"][0]["expected_sha256"] = "0" * 64
    report = _run(tmp_path, plan)
    source = [item for item in report.sources if item.source_id == "article"][0]
    assert source.acquisition_status == "failed"
    assert "digest differs" in source.error
    assert report.decisions[0].status == "unresolved"


def test_duplicate_claim_ids_are_rejected(tmp_path):
    plan = _plan()
    plan["sources"][1]["claims"][0]["claim_id"] = "article-rate"
    (tmp_path / "config.yaml").write_text(
        "sampling_rate_hz: 2000\n", encoding="utf-8"
    )
    plan_path = tmp_path / "plan.yaml"
    plan_path.write_text(yaml.safe_dump(plan), encoding="utf-8")
    with pytest.raises(ValueError, match="claim IDs must be unique"):
        run_semantic_enrichment(
            plan_path=str(plan_path),
            repository_root=str(tmp_path),
            cache_dir=str(tmp_path / "cache"),
            fetcher=lambda _url: ARTICLE_XML,
        )


def test_report_roundtrip_and_public_json_schema(tmp_path):
    report = _run(tmp_path, _plan())
    restored = SemanticEnrichmentReport.from_dict(
        json.loads(report.to_json())
    )
    assert restored.to_dict() == report.to_dict()
    schema = json.loads(
        (
            ROOT / "schemas/semantic-enrichment-report-1.0.0.json"
        ).read_text(encoding="utf-8")
    )
    jsonschema.Draft202012Validator(schema).validate(report.to_dict())


def test_overlay_promotes_only_positive_scope_applicable_claims(tmp_path):
    report = _run(tmp_path, _plan())
    overlay = build_semantic_overlay(
        report,
        report_sha256="a" * 64,
        study_id="study",
        target_scope_level="channel",
        target_scope_selector={
            "sensor_type": "surface-emg",
            "subject_id": "P01",
            "channel_id": "TA",
        },
    )

    assert [entry.claim_id for entry in overlay.entries] == ["article-rate"]
    assert overlay.entries[0].decision_status == "corroborated"
    assert overlay.entries[0].claim_scope_level == "channel-group"
    assert overlay.entries[0].propagation == "all-descendants"
    assert overlay.target_scope_selector["subject_id"] == "P01"
    assert overlay.report_sha256 == "a" * 64
    assert "do not overwrite source measurements" in overlay.claim_boundary


def test_overlay_does_not_cross_an_undeclared_scope_or_selector(tmp_path):
    report = _run(tmp_path, _plan())
    with pytest.raises(ValueError, match="no positive semantic claims"):
        build_semantic_overlay(
            report,
            report_sha256="a" * 64,
            study_id="study",
            target_scope_level="trial",
            target_scope_selector={"sensor_type": "intramuscular-emg"},
        )

    report.claims[0].propagation = "none"
    with pytest.raises(ValueError, match="no positive semantic claims"):
        build_semantic_overlay(
            report,
            report_sha256="a" * 64,
            study_id="study",
            target_scope_level="channel",
            target_scope_selector={"sensor_type": "surface-emg"},
        )


def test_boundary_and_conflicting_claims_never_enter_overlay(tmp_path):
    boundary_report = _run(tmp_path, _plan())
    boundary_report.decisions[0].machine_action = "boundary-only"
    with pytest.raises(ValueError, match="no positive semantic claims"):
        build_semantic_overlay(
            boundary_report,
            report_sha256="a" * 64,
            study_id="study",
            target_scope_level="channel-group",
            target_scope_selector={"sensor_type": "surface-emg"},
        )

    conflict_report = _run(tmp_path, _plan(config_rate=1000))
    with pytest.raises(ValueError, match="no positive semantic claims"):
        build_semantic_overlay(
            conflict_report,
            report_sha256="a" * 64,
            study_id="study",
            target_scope_level="channel-group",
            target_scope_selector={"sensor_type": "surface-emg"},
        )


def test_overlay_roundtrip_and_public_json_schema(tmp_path):
    report = _run(tmp_path, _plan())
    overlay = build_semantic_overlay(
        report,
        report_sha256="a" * 64,
        study_id="study",
        target_scope_level="channel-group",
        target_scope_selector={"sensor_type": "surface-emg"},
    )
    restored = SemanticOverlay.from_dict(json.loads(overlay.to_json()))
    assert restored.to_dict() == overlay.to_dict()
    schema = json.loads(
        (ROOT / "schemas/semantic-overlay-1.0.0.json").read_text(
            encoding="utf-8"
        )
    )
    jsonschema.Draft202012Validator(schema).validate(overlay.to_dict())


def test_file_only_and_paper_assisted_coverage_are_counted_separately(
    tmp_path,
):
    report = _run(tmp_path, _plan())
    comparison = compare_semantic_coverage(
        report,
        report_sha256="d" * 64,
        study_ids=["study"],
    )

    row = comparison.study_rows[0]
    assert row["file_only_properties"] == [
        "recording.emg.sampling_rate_hz"
    ]
    assert row["authority_enrichment_properties"] == [
        "recording.emg.sampling_rate_hz"
    ]
    assert row["properties_added_by_literature"] == []
    assert row["corroborated_authority_claim_count"] == 1
    assert comparison.summary["file_only_study_property_pairs"] == 1
    assert comparison.summary["paper_assisted_study_property_pairs"] == 1
    schema = json.loads(
        (
            ROOT
            / "schemas/semantic-coverage-comparison-1.0.0.json"
        ).read_text(encoding="utf-8")
    )
    jsonschema.Draft202012Validator(schema).validate(comparison.to_dict())


def test_committed_plan_matches_public_json_schema():
    plan = yaml.safe_load(
        (ROOT / "paper/literature_semantic_enrichment.yaml").read_text(
            encoding="utf-8"
        )
    )
    schema = json.loads(
        (
            ROOT / "schemas/semantic-evidence-plan-1.0.0.json"
        ).read_text(encoding="utf-8")
    )
    jsonschema.Draft202012Validator(schema).validate(plan)


def test_committed_report_retains_scope_and_boundary():
    report = SemanticEnrichmentReport.from_dict(
        json.loads(
            (
                ROOT
                / "paper/results/validation/literature_semantic_enrichment.json"
            ).read_text(encoding="utf-8")
        )
    )
    assert report.code_dirty is False
    assert report.working_tree_sha256 is None
    assert report.implementation_commit == (
        "3a9564b2208975c7977acd578225c8788981d925"
    )
    assert report.summary["source_acquired"] == 21
    assert report.summary["extracted_claim_count"] == 67
    assert report.summary["authority_claim_count"] == 43
    assert report.summary["observation_claim_count"] == 24
    assert report.summary["enrichment_ready_count"] == 38
    assert report.summary["boundary_evidence_count"] == 5
    assert report.summary["conflicting_decision_count"] == 0
    assert report.summary["unresolved_decision_count"] == 0
    limitation = [
        claim
        for claim in report.claims
        if claim.claim_id == "study32-article-single-muscle-limit"
    ][0]
    assert limitation.scope_level == "channel-group"
    assert limitation.machine_action == "boundary-only"
    assert limitation.value == "not-established"

    comparison = json.loads(
        (
            ROOT
            / "paper/results/validation/semantic_coverage_comparison.json"
        ).read_text(encoding="utf-8")
    )
    assert comparison["summary"]["study_count"] == 8
    assert comparison["summary"]["file_only_study_property_pairs"] == 24
    assert comparison["summary"]["paper_assisted_study_property_pairs"] == 43
    assert comparison["summary"]["literature_added_study_property_pairs"] == 19
    assert comparison["summary"]["conflicting_claim_count"] == 0
    assert comparison["summary"]["unresolved_claim_count"] == 0

    study28 = SemanticOverlay.from_dict(
        json.loads(
            (
                ROOT
                / "paper/results/validation/semantic_overlay_study28_emg.json"
            ).read_text(encoding="utf-8")
        )
    )
    assert len(study28.entries) == 3
    assert len(study28.blocked_claims) == 1
    assert len(study28.skipped_claims) == 3
    study33 = SemanticOverlay.from_dict(
        json.loads(
            (
                ROOT
                / "paper/results/validation/"
                "semantic_overlay_study33_dataset.json"
            ).read_text(encoding="utf-8")
        )
    )
    assert len(study33.entries) == 1


def test_committed_plan_extracts_article_and_repository_claims_offline(tmp_path):
    plan = yaml.safe_load(
        (ROOT / "paper/literature_semantic_enrichment.yaml").read_text(
            encoding="utf-8"
        )
    )
    # The committed remote byte digests are tested online by the publication
    # command. Unit tests replace URLs with minimal deterministic fixtures and
    # remove their frozen remote digests while retaining all local claims.
    for source in plan["sources"]:
        if "url" in source:
            source.pop("expected_sha256", None)
            source["claims"] = source["claims"][:1]
            if source["study_id"] == "study31":
                source["claims"][0]["locator"] = "Instrumentation"
                source["claims"][0]["pattern"] = (
                    r"acquiring at (?P<value>[0-9]+) Hz"
                )
            else:
                source["claims"][0]["locator"] = "Instrumentation"
                source["claims"][0]["pattern"] = (
                    r"acquiring at (?P<value>[0-9]+) Hz"
                )
        else:
            source["claims"] = source["claims"][:1]
    plan["sources"] = [
        source
        for source in plan["sources"]
        if source["source_id"] in (
            "study31-article-jats",
            "study31-device-config",
        )
    ]
    plan["sources"][0]["claims"][0]["expected"] = 2000
    plan_path = tmp_path / "plan.yaml"
    plan_path.write_text(yaml.safe_dump(plan, sort_keys=False), encoding="utf-8")
    report = run_semantic_enrichment(
        plan_path=str(plan_path),
        repository_root=str(ROOT),
        cache_dir=str(tmp_path / "cache"),
        fetcher=lambda _url: ARTICLE_XML,
    )
    assert report.summary["source_acquired"] == 2
    assert report.summary["corroborated_decision_count"] == 1
