from __future__ import annotations

import copy
import json
from pathlib import Path

from harmo.mechanics_disposition import (
    build_mechanics_disposition,
    validate_mechanics_disposition,
    write_mechanics_disposition,
)
from harmo.validation_study import gate_audit, load_registry


ROOT = Path(__file__).resolve().parents[1]
RESULTS = ROOT / "paper" / "results" / "validation"
POLICY = ROOT / "paper" / "g6_mechanics_disposition_policy.yaml"


def test_real_g6_evidence_closes_reporting_without_promoting_endpoint():
    result = build_mechanics_disposition(POLICY, RESULTS, ROOT)
    assert result["evidence_complete"] is True
    assert result["reporting_complete"] is True
    assert result["scientific_endpoint_pass"] is False
    assert result["pass"] is False
    assert result["frozen_endpoint_status"] == "failed"
    assert result["mechanics_readiness"] == "unvalidated"
    assert result["dynamic_personalization_readiness"] == "unvalidated"
    assert result["default_mechanics_export"] == "deny"
    assert result["thresholds_or_upstream_results_changed"] is False
    assert all(row["pass"] for row in result["evidence_checks"])


def test_promoted_upstream_result_fails_closed(tmp_path):
    local_results = tmp_path / "paper" / "results" / "validation"
    local_results.mkdir(parents=True)
    local_policy = tmp_path / "paper" / POLICY.name
    local_policy.write_bytes(POLICY.read_bytes())
    for artifact in RESULTS.iterdir():
        if artifact.suffix == ".json":
            (local_results / artifact.name).write_bytes(artifact.read_bytes())

    promoted_path = local_results / "g6c_s6d_bounded_arm_sensitivity.json"
    promoted = json.loads(promoted_path.read_text(encoding="utf-8"))
    promoted["mechanics_closed"] = True
    promoted_path.write_text(json.dumps(promoted), encoding="utf-8")
    result = build_mechanics_disposition(local_policy, local_results, tmp_path)
    assert result["evidence_complete"] is False
    assert result["reporting_complete"] is False
    assert any(
        row["check_id"] == "bounded_arm_sensitivity:mechanics_closed"
        and row["pass"] is False
        for row in result["evidence_checks"]
    )


def test_stored_disposition_detects_changed_evidence_hash():
    result = build_mechanics_disposition(POLICY, RESULTS, ROOT)
    checks = validate_mechanics_disposition(result, RESULTS, ROOT, POLICY)
    assert all(checks.values())

    changed = copy.deepcopy(result)
    changed["evidence_artifacts"][0]["sha256"] = "0" * 64
    checks = validate_mechanics_disposition(changed, RESULTS, ROOT, POLICY)
    assert checks["evidence_hashes"] is False


def test_policy_is_explicitly_post_outcome():
    result = build_mechanics_disposition(POLICY, RESULTS, ROOT)
    assert result["post_outcome_reporting_policy"] is True
    assert "preregistered" not in result["publication_role"].lower()


def test_submission_audit_separates_negative_endpoint_from_reporting(tmp_path):
    local_results = tmp_path / "paper" / "results" / "validation"
    local_results.mkdir(parents=True)
    local_policy = tmp_path / "paper" / POLICY.name
    local_policy.write_bytes(POLICY.read_bytes())
    for artifact in RESULTS.iterdir():
        if artifact.suffix == ".json":
            (local_results / artifact.name).write_bytes(artifact.read_bytes())
    disposition = build_mechanics_disposition(
        local_policy,
        local_results,
        tmp_path,
    )
    write_mechanics_disposition(
        disposition,
        local_results / "g6_mechanics_disposition.json",
    )

    audit = gate_audit(
        load_registry(ROOT / "paper" / "validation_registry.yaml"),
        local_results,
        ROOT / "paper" / "source_releases.yaml",
    )
    negative_endpoint_checks = [
        row
        for row in audit["checks"]
        if row["gate"] == "G6" and row["pass"] is False
    ]
    reporting_checks = [
        row for row in audit["checks"] if row["gate"] == "G6-reporting"
    ]
    assert negative_endpoint_checks
    assert all(row["submission_blocking"] is False for row in negative_endpoint_checks)
    assert reporting_checks
    assert all(row["pass"] is True for row in reporting_checks)
    assert all(row["submission_blocking"] is True for row in reporting_checks)
