#!/usr/bin/env python3
"""Audit G8 manuscript reporting against frozen machine-readable evidence."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
from pathlib import Path
from typing import Any, Dict, List, Mapping, Optional, Sequence

import yaml


SHA256 = re.compile(r"^[0-9a-f]{64}$")


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        while True:
            chunk = stream.read(1024 * 1024)
            if not chunk:
                break
            digest.update(chunk)
    return digest.hexdigest()


def read_json(path: Path) -> Dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def read_yaml(path: Path) -> Dict[str, Any]:
    return yaml.safe_load(path.read_text(encoding="utf-8")) or {}


def write_json(path: Path, value: Mapping[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(
        json.dumps(
            value,
            ensure_ascii=False,
            sort_keys=True,
            indent=2,
            allow_nan=False,
        )
        + "\n",
        encoding="utf-8",
    )
    os.replace(str(temporary), str(path))


def _check(name: str, passed: bool, detail: Any) -> Dict[str, Any]:
    return {"name": name, "pass": bool(passed), "detail": detail}


def _citation_keys(manuscript: str) -> List[str]:
    keys = set()
    for block in re.findall(r"\[@([^\]]+)\]", manuscript):
        for item in block.split(";"):
            keys.add(item.strip().lstrip("@"))
    return sorted(keys)


def _bibliography_keys(bibliography: str) -> List[str]:
    return sorted(set(re.findall(r"^@\w+\{([^,]+),", bibliography, re.MULTILINE)))


def _artifact_checks(
    root: Path,
    protocol: Mapping[str, Any],
    artifact_manifest: Mapping[str, Any],
) -> List[Dict[str, Any]]:
    checks = []
    rows = artifact_manifest.get("generated_artifacts") or []
    indexed = {str(row.get("path")): row for row in rows}
    expected = ["paper/figures/README.md"]
    for item in protocol["figures"]:
        for extension in protocol["figure_formats"]:
            expected.append(
                "paper/figures/{}.{}".format(item["basename"], extension)
            )
    expected.extend(protocol.get("figure_supplemental_artifacts") or [])
    for item in protocol["tables"]:
        for extension in protocol["table_formats"]:
            expected.append(
                "paper/tables/{}.{}".format(item["basename"], extension)
            )
    checks.append(
        _check(
            "generated-artifact-denominator",
            sorted(indexed) == sorted(expected),
            {"expected": len(expected), "indexed": len(indexed)},
        )
    )
    for relative in expected:
        path = root / relative
        row = indexed.get(relative) or {}
        identity_ok = (
            path.is_file()
            and int(row.get("bytes", -1)) == path.stat().st_size
            and row.get("sha256") == sha256_file(path)
        )
        checks.append(_check("artifact:{}".format(relative), identity_ok, row))
    return checks


def _evidence_checks(root: Path) -> List[Dict[str, Any]]:
    results = root / "paper/results/validation"
    corpus = read_json(results / "corpus_execution.json")
    fidelity = read_json(results / "format_fidelity.json")
    faults = read_json(results / "fault_detection.json")
    reproducibility = read_json(results / "reproducibility.json")
    opensim = read_json(results / "opensim_quality_summary.json")
    disposition = read_json(results / "g6_mechanics_disposition.json")
    adapter = read_json(results / "adapter_metrics.json")
    physiology = read_json(results / "physiology_positive_control.json")
    literature = read_json(results / "literature_semantic_enrichment.json")
    semantic_coverage = read_json(
        results / "semantic_coverage_comparison.json"
    )
    overlay_audit = read_json(
        results / "semantic_overlay_materialization_audit.json"
    )
    full_record = read_json(
        results / "full_record_materialization_benchmark.json"
    )
    semantic_packet = read_json(
        root / "paper/semantic_gold_standard_packet.json"
    )
    gate = read_json(results / "gate_audit.json")

    source = opensim["source_denominator"]
    analysis = opensim["analysis_denominator"]
    evidence_checks = disposition["evidence_checks"]
    if isinstance(evidence_checks, dict):
        disposition_check_pass = all(bool(value) for value in evidence_checks.values())
        disposition_check_count = len(evidence_checks)
    else:
        disposition_check_pass = all(
            bool(value.get("pass")) for value in evidence_checks
        )
        disposition_check_count = len(evidence_checks)
    return [
        _check(
            "A-corpus",
            corpus.get("pass") is True
            and corpus.get("mapping_denominator") == 78
            and (corpus.get("outcomes") or {}).get("converted") == 78
            and corpus.get("verified_source_assets")
            == corpus.get("source_asset_count"),
            {
                "mappings": corpus.get("mapping_denominator"),
                "outcomes": corpus.get("outcomes"),
            },
        ),
        _check(
            "B-format-fidelity",
            fidelity.get("pass") is True
            and fidelity["denominator"]["comparison_attempts"] == 96
            and fidelity["denominator"]["required_equivalence_passed"] == 32
            and fidelity["denominator"]["required_equivalence_comparisons"] == 32,
            fidelity["denominator"],
        ),
        _check(
            "C-faults",
            faults.get("pass") is True
            and faults.get("faults_detected") == 8
            and faults.get("faults_total") == 8
            and faults.get("critical_sensitivity") == 1.0
            and faults.get("clean_specificity") == 1.0,
            {
                "detected": faults.get("faults_detected"),
                "total": faults.get("faults_total"),
            },
        ),
        _check(
            "D-reproducibility",
            reproducibility.get("pass") is True
            and reproducibility.get("run_count") == 6
            and reproducibility.get("exact_numeric_match") is True
            and reproducibility.get("max_observed_numeric_difference") == 0.0,
            {
                "runs": reproducibility.get("run_count"),
                "maximum_difference": reproducibility.get(
                    "max_observed_numeric_difference"
                ),
            },
        ),
        _check(
            "E-denominator",
            source.get("frozen_trials") == 336
            and source.get("source_available") == 330
            and source.get("source_absent") == 6
            and analysis.get("source_available_trials_completely_assessed") == 0
            and opensim.get("pass") is False,
            {
                "source": source,
                "assessed": analysis.get(
                    "source_available_trials_completely_assessed"
                ),
            },
        ),
        _check(
            "E-disposition",
            disposition.get("reporting_complete") is True
            and disposition.get("scientific_endpoint_pass") is False
            and disposition.get("default_mechanics_export") == "deny"
            and disposition.get("evidence_complete") is True
            and len(disposition.get("evidence_artifacts") or []) == 26
            and disposition_check_count == 106
            and disposition_check_pass,
            {
                "artifacts": len(disposition.get("evidence_artifacts") or []),
                "checks": disposition_check_count,
                "export": disposition.get("default_mechanics_export"),
            },
        ),
        _check(
            "F-held-out-adapter",
            adapter.get("pass") is True
            and adapter.get("subject_count") == 13
            and adapter.get("trial_count") == 208
            and adapter.get("core_changed_lines") == 0
            and adapter.get("core_dataset_id_branches") == 0,
            {
                "subjects": adapter.get("subject_count"),
                "trials": adapter.get("trial_count"),
                "core_changed_lines": adapter.get("core_changed_lines"),
            },
        ),
        _check(
            "G-physiology",
            physiology.get("scientific_endpoint_pass") is True
            and physiology.get("reporting_complete") is True
            and physiology.get("amplitude_pooled_across_studies") is False
            and len(physiology.get("metrics") or []) == 40
            and len(physiology.get("study_eligibility") or []) == 2
            and all(
                row.get("exact_frozen_subjects") is True
                and row.get("eligible_subject_count") == 10
                for row in physiology.get("study_eligibility", [])
            )
            and all(
                row.get("pass") is True
                for row in physiology.get(
                    "cross_study_centroid_concordance", []
                )
            ),
            {
                "metrics": len(physiology.get("metrics") or []),
                "studies": physiology.get("study_eligibility"),
            },
        ),
        _check(
            "literature-semantic-enrichment",
            literature.get("code_dirty") is False
            and literature.get("working_tree_sha256") is None
            and literature.get("summary", {}).get("source_count") == 21
            and literature.get("summary", {}).get("source_acquired") == 21
            and literature.get("summary", {}).get("claim_count") == 67
            and literature.get("summary", {}).get("extracted_claim_count") == 67
            and literature.get("summary", {}).get("authority_claim_count") == 43
            and literature.get("summary", {}).get("observation_claim_count") == 24
            and literature.get("summary", {}).get("enrichment_ready_count") == 38
            and literature.get("summary", {}).get("boundary_evidence_count") == 5
            and literature.get("summary", {}).get("conflicting_decision_count") == 0
            and literature.get("summary", {}).get("unresolved_decision_count") == 0,
            literature.get("summary"),
        ),
        _check(
            "semantic-coverage-comparison",
            semantic_coverage.get("summary", {}).get("study_count") == 8
            and semantic_coverage.get("summary", {}).get(
                "file_only_study_property_pairs"
            )
            == 24
            and semantic_coverage.get("summary", {}).get(
                "paper_assisted_study_property_pairs"
            )
            == 43
            and semantic_coverage.get("summary", {}).get(
                "literature_added_study_property_pairs"
            )
            == 19
            and semantic_coverage.get("summary", {}).get(
                "conflicting_claim_count"
            )
            == 0
            and semantic_coverage.get("summary", {}).get(
                "unresolved_claim_count"
            )
            == 0,
            semantic_coverage.get("summary"),
        ),
        _check(
            "semantic-overlay-materialization",
            overlay_audit.get("pass") is True
            and overlay_audit.get("candidate_code_dirty") is False
            and overlay_audit.get("summary", {}).get(
                "recording_bundle_invariant_count"
            )
            == 12
            and overlay_audit.get("summary", {}).get(
                "overlay_checksum_bound_count"
            )
            == 12
            and overlay_audit.get("summary", {}).get(
                "trust_dimension_invariant_count"
            )
            == 12
            and overlay_audit.get("summary", {}).get(
                "comparison_policy_invariant_count"
            )
            == 6
            and overlay_audit.get("summary", {}).get(
                "release_policy_invariant_count"
            )
            == 12
            and overlay_audit.get("summary", {}).get(
                "candidate_conditional_release_count"
            )
            == 12
            and overlay_audit.get("summary", {}).get(
                "candidate_exchange_export_count"
            )
            == 12
            and overlay_audit.get("summary", {}).get(
                "dirty_control_fail_closed"
            )
            is True,
            overlay_audit.get("summary"),
        ),
        _check(
            "full-record-materialization",
            full_record.get("summary", {}).get("pass") is True
            and full_record.get("summary", {}).get("subject_count") == 10
            and full_record.get("summary", {}).get("record_count") == 130
            and full_record.get("summary", {}).get("timepoint_count") == 138638
            and full_record.get("summary", {}).get("signal_value_count") == 1109104
            and full_record.get("summary", {}).get("conditional_release_count") == 130
            and full_record.get("summary", {}).get("clean_code_runs") == 2
            and full_record.get("summary", {}).get(
                "scientific_digest_identical"
            )
            is True
            and full_record.get("summary", {}).get(
                "signal_and_record_tables_identical"
            )
            is True,
            full_record.get("summary"),
        ),
        _check(
            "semantic-gold-standard-packet",
            len(semantic_packet.get("rows") or []) == 43
            and bool(
                SHA256.fullmatch(
                    str(semantic_packet.get("packet_sha256", ""))
                )
            )
            and "expected_value"
            in semantic_packet.get("blinding", {}).get(
                "hidden_fields", []
            ),
            {
                "questions": len(semantic_packet.get("rows") or []),
                "status": "pending-independent-human-annotations",
            },
        ),
        _check(
            "G0-G7-gate",
            gate.get("submission_ready") is True
            and gate.get("blocking_failed_checks") == 0,
            {
                "submission_ready": gate.get("submission_ready"),
                "blocking_failed_checks": gate.get("blocking_failed_checks"),
                "nonblocking_failed_checks": gate.get("failed_checks"),
            },
        ),
    ]


def audit(
    root: Path,
    require_external: bool = False,
) -> Dict[str, Any]:
    protocol_path = root / "paper/g8_reporting_protocol.yaml"
    protocol = read_yaml(protocol_path)
    manuscript_path = root / protocol["manuscript"]["path"]
    manuscript = manuscript_path.read_text(encoding="utf-8")
    bibliography_path = root / protocol["manuscript"]["references"]
    bibliography = bibliography_path.read_text(encoding="utf-8")
    metadata = read_yaml(root / "paper/submission_metadata.yaml")
    artifact_manifest_path = root / "paper/results/g8_reporting_artifacts.json"
    artifact_manifest = read_json(artifact_manifest_path)

    checks: List[Dict[str, Any]] = []
    headings = set(re.findall(r"^# (.+)$", manuscript, re.MULTILINE))
    required_headings = set(protocol["manuscript"]["required_sections"])
    checks.append(
        _check(
            "manuscript-sections",
            required_headings.issubset(headings),
            {"missing": sorted(required_headings - headings)},
        )
    )
    found_placeholders = [
        value
        for value in protocol["manuscript"]["forbidden_placeholder_phrases"]
        if value.lower() in manuscript.lower()
    ]
    checks.append(
        _check(
            "manuscript-placeholders-absent",
            not found_placeholders,
            found_placeholders,
        )
    )
    citation_keys = _citation_keys(manuscript)
    bibliography_keys = _bibliography_keys(bibliography)
    checks.append(
        _check(
            "manuscript-citations-resolve",
            bool(citation_keys)
            and not (set(citation_keys) - set(bibliography_keys)),
            {
                "citation_count": len(citation_keys),
                "missing": sorted(set(citation_keys) - set(bibliography_keys)),
                "uncited": sorted(set(bibliography_keys) - set(citation_keys)),
            },
        )
    )
    checks.append(
        _check(
            "artifact-manifest-generator",
            artifact_manifest.get("generator")
            == "scripts/paper/generate_g8_artifacts.py"
            and artifact_manifest.get("generator_sha256")
            == sha256_file(
                root / "scripts/paper/generate_g8_artifacts.py"
            ),
            artifact_manifest.get("generator_sha256"),
        )
    )
    checks.extend(_artifact_checks(root, protocol, artifact_manifest))
    checks.extend(_evidence_checks(root))

    archive_manifest_path = root / "paper/release/archive_manifest.json"
    if archive_manifest_path.is_file():
        archive = read_json(archive_manifest_path)
        archive_ok = (
            archive.get("construction") == "git archive --format=tar.gz"
            and isinstance(archive.get("size_bytes"), int)
            and archive.get("size_bytes", 0) > 0
            and bool(SHA256.fullmatch(str(archive.get("sha256", ""))))
            and bool(re.fullmatch(r"[0-9a-f]{40}", str(archive.get("source_commit", ""))))
            and archive.get("deterministic_repeat_identical") is True
        )
        archive_detail: Any = archive
    else:
        archive_ok = False
        archive_detail = "paper/release/archive_manifest.json is missing"
    checks.append(_check("deterministic-source-archive", archive_ok, archive_detail))

    external_fields = [
        str(value)
        for value in protocol["external_submission_metadata"][
            "required_before_journal_submission"
        ]
    ]
    external_key_map = {
        "final author list and affiliations": ("authors", "affiliations"),
        "corresponding author and email": (
            "corresponding_author",
            "corresponding_author_email",
        ),
        "target journal and style": ("target_journal",),
        "funding statement": ("funding_statement",),
        "competing-interest confirmation": (
            "competing_interests_statement",
        ),
        "archive deposition DOI": ("archive_doi",),
    }
    missing_external = []
    for label in external_fields:
        keys = external_key_map[label]
        if any(metadata.get(key) in (None, "", []) for key in keys):
            missing_external.append(label)

    local_ready = all(row["pass"] for row in checks)
    external_ready = not missing_external
    result = {
        "schema_version": "1.0",
        "protocol_id": protocol["protocol_id"],
        "protocol_sha256": sha256_file(protocol_path),
        "manuscript_sha256": sha256_file(manuscript_path),
        "bibliography_sha256": sha256_file(bibliography_path),
        "checks": checks,
        "passed_checks": sum(row["pass"] for row in checks),
        "failed_checks": sum(not row["pass"] for row in checks),
        "local_reporting_ready": local_ready,
        "external_submission_metadata_ready": external_ready,
        "missing_external_submission_metadata": missing_external,
        "journal_submission_ready": local_ready and external_ready,
        "require_external_requested": bool(require_external),
    }
    return result


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--root", default=str(Path(__file__).resolve().parents[2])
    )
    parser.add_argument(
        "--output", default="paper/results/g8_reporting_audit.json"
    )
    parser.add_argument("--check-only", action="store_true")
    parser.add_argument("--require-external", action="store_true")
    return parser


def main(argv: Optional[Sequence[str]] = None) -> int:
    args = _parser().parse_args(argv)
    root = Path(args.root).resolve()
    result = audit(root, require_external=args.require_external)
    if not args.check_only:
        output = Path(args.output)
        if not output.is_absolute():
            output = root / output
        write_json(output, result)
    print(
        json.dumps(
            {
                "local_reporting_ready": result["local_reporting_ready"],
                "external_submission_metadata_ready": result[
                    "external_submission_metadata_ready"
                ],
                "journal_submission_ready": result[
                    "journal_submission_ready"
                ],
                "failed_checks": result["failed_checks"],
                "missing_external_submission_metadata": result[
                    "missing_external_submission_metadata"
                ],
            },
            indent=2,
            ensure_ascii=False,
            sort_keys=True,
        )
    )
    if not result["local_reporting_ready"]:
        return 1
    if args.require_external and not result["external_submission_metadata_ready"]:
        return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
