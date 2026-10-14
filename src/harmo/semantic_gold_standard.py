"""Blinded human validation for literature-derived semantic claims."""

from __future__ import annotations

import hashlib
import json
import math
from pathlib import Path
from typing import Any, Dict, Mapping, Sequence

import yaml

from harmo.evidence.io import canonical_json_bytes, sha256_file


VERSION = "1.0.0"
ALLOWED_SUPPORT = {"supported", "unsupported", "uncertain"}
ALLOWED_ACTIONS = {"enrich", "boundary-only", "none"}


def _annotation_id(claim_id: str) -> str:
    return "SGS-" + hashlib.sha256(claim_id.encode("utf-8")).hexdigest()[:12]


def _load_yaml(path: Path) -> Dict[str, Any]:
    value = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    if not isinstance(value, dict):
        raise ValueError("semantic gold-standard protocol must be an object")
    return value


def build_blinded_packet(
    *,
    protocol_path: str,
    plan_path: str,
    report_path: str,
) -> Dict[str, Any]:
    """Create questions without regexes, expected values, or system outcomes."""
    protocol_file = Path(protocol_path).resolve()
    plan_file = Path(plan_path).resolve()
    report_file = Path(report_path).resolve()
    protocol = _load_yaml(protocol_file)
    plan = _load_yaml(plan_file)
    report = json.loads(report_file.read_text(encoding="utf-8"))
    report_claims = {row["claim_id"]: row for row in report["claims"]}

    rows = []
    for source in plan.get("sources", []):
        if source.get("source_role") != "authority":
            continue
        for claim in source.get("claims", []):
            claim_id = str(claim["claim_id"])
            observed = report_claims.get(claim_id)
            if observed is None or observed.get("source_role") != "authority":
                raise ValueError("authority claim is absent from system report: " + claim_id)
            rows.append(
                {
                    "annotation_id": _annotation_id(claim_id),
                    "study_id": str(source["study_id"]),
                    "source_id": str(source["source_id"]),
                    "source_identifier": str(source["identifier"]),
                    "source_format": str(source["format"]),
                    "source_sha256": str(observed["source_sha256"]),
                    "property_path": str(claim["property_path"]),
                    "scope_question": (
                        "At what most specific supported scope does the source "
                        "state this property?"
                    ),
                    "value_question": (
                        "Does the source explicitly support this property, and "
                        "if so what value does it state?"
                    ),
                }
            )
    rows.sort(key=lambda row: row["annotation_id"])
    expected = int(protocol["denominator"]["authority_questions"])
    if len(rows) != expected:
        raise ValueError(
            "blinded question denominator changed: {} != {}".format(
                len(rows), expected
            )
        )
    return {
        "schema_version": VERSION,
        "protocol_id": str(protocol["protocol_id"]),
        "protocol_sha256": sha256_file(str(protocol_file)),
        "plan_sha256": sha256_file(str(plan_file)),
        "system_report_sha256": sha256_file(str(report_file)),
        "blinding": {
            "hidden_fields": [
                "claim_id",
                "regex",
                "expected_value",
                "system_extracted_value",
                "system_decision",
            ],
            "instructions": protocol["annotation_instructions"],
        },
        "rows": rows,
        "packet_sha256": hashlib.sha256(canonical_json_bytes(rows)).hexdigest(),
        "claim_boundary": (
            "The packet evaluates registered authority questions only. It does "
            "not estimate recall for facts outside the frozen question set."
        ),
    }


def build_annotation_template(
    *, packet_path: str, annotator_id: str
) -> Dict[str, Any]:
    """Create a complete, still-blinded response form for one annotator."""
    packet_file = Path(packet_path).resolve()
    packet = json.loads(packet_file.read_text(encoding="utf-8"))
    annotator = str(annotator_id).strip()
    if not annotator:
        raise ValueError("annotator_id is required")
    rows = packet.get("rows")
    if not isinstance(rows, list) or not rows:
        raise ValueError("blinded packet must contain question rows")
    annotations = []
    for row in rows:
        annotations.append(
            {
                "annotation_id": row["annotation_id"],
                "study_id": row["study_id"],
                "source_identifier": row["source_identifier"],
                "property_path": row["property_path"],
                "support": None,
                "value": None,
                "scope_level": None,
                "machine_action": None,
                "notes": "",
            }
        )
    return {
        "schema_version": VERSION,
        "packet_sha256": sha256_file(str(packet_file)),
        "attestation": {
            "annotator_id": annotator,
            "involved_in_extractor_development": False,
        },
        "annotations": annotations,
        "completion_instruction": (
            "Replace every null support and machine_action, and complete value "
            "and scope_level when supported. Do not inspect system outcomes."
        ),
    }


def _load_annotations(
    path: Path, expected_ids: set[str]
) -> tuple[Dict[str, Dict[str, Any]], Dict[str, Any]]:
    value = json.loads(path.read_text(encoding="utf-8"))
    attestation = value.get("attestation") or {}
    annotator_id = str(attestation.get("annotator_id", ""))
    if not annotator_id:
        raise ValueError("{} lacks annotator_id".format(path))
    if attestation.get("involved_in_extractor_development") is not False:
        raise ValueError("{} is not independent of extractor development".format(path))
    rows = value.get("annotations")
    if not isinstance(rows, list):
        raise ValueError("{} annotations must be an array".format(path))
    indexed = {}
    for row in rows:
        annotation_id = str(row.get("annotation_id", ""))
        if annotation_id in indexed:
            raise ValueError("duplicate annotation ID: " + annotation_id)
        support = str(row.get("support", ""))
        if support not in ALLOWED_SUPPORT:
            raise ValueError("invalid support state for " + annotation_id)
        scope = row.get("scope_level")
        action = str(row.get("machine_action", "none"))
        if action not in ALLOWED_ACTIONS:
            raise ValueError("invalid machine_action for " + annotation_id)
        indexed[annotation_id] = {
            "support": support,
            "value": row.get("value"),
            "scope_level": scope,
            "machine_action": action,
        }
    if set(indexed) != expected_ids:
        missing = sorted(expected_ids - set(indexed))
        extra = sorted(set(indexed) - expected_ids)
        raise ValueError(
            "annotation denominator mismatch; missing={}, extra={}".format(
                missing, extra
            )
        )
    return indexed, attestation


def _cohen_kappa(left: Sequence[str], right: Sequence[str]) -> float | None:
    if len(left) != len(right) or not left:
        raise ValueError("paired labels are required")
    observed = sum(a == b for a, b in zip(left, right)) / len(left)
    labels = sorted(set(left) | set(right))
    expected = sum(
        (left.count(label) / len(left)) * (right.count(label) / len(right))
        for label in labels
    )
    if math.isclose(expected, 1.0):
        return None
    return (observed - expected) / (1.0 - expected)


def evaluate_gold_standard(
    *,
    protocol_path: str,
    packet_path: str,
    report_path: str,
    annotation_paths: Sequence[str],
    adjudication_path: str | None = None,
) -> Dict[str, Any]:
    """Compare system claims with two blinded annotations and adjudication."""
    protocol_file = Path(protocol_path).resolve()
    packet_file = Path(packet_path).resolve()
    report_file = Path(report_path).resolve()
    protocol = _load_yaml(protocol_file)
    packet = json.loads(packet_file.read_text(encoding="utf-8"))
    report = json.loads(report_file.read_text(encoding="utf-8"))
    expected_ids = {row["annotation_id"] for row in packet["rows"]}
    if len(annotation_paths) != 2:
        raise ValueError("exactly two independent annotation files are required")
    loaded = [
        _load_annotations(Path(path).resolve(), expected_ids)
        for path in annotation_paths
    ]
    if loaded[0][1]["annotator_id"] == loaded[1][1]["annotator_id"]:
        raise ValueError("annotator IDs must be different")
    annotations = [item[0] for item in loaded]

    fields = ("support", "value", "scope_level", "machine_action")
    disagreements = {
        annotation_id
        for annotation_id in expected_ids
        if any(
            annotations[0][annotation_id][field]
            != annotations[1][annotation_id][field]
            for field in fields
        )
    }
    adjudicated: Dict[str, Dict[str, Any]] = {}
    if disagreements:
        if adjudication_path is None:
            raise ValueError(
                "adjudication is required for {} disagreements".format(
                    len(disagreements)
                )
            )
        adjudicated, _ = _load_annotations(
            Path(adjudication_path).resolve(), expected_ids
        )
    gold = {}
    for annotation_id in expected_ids:
        if annotation_id in disagreements:
            gold[annotation_id] = adjudicated[annotation_id]
        else:
            gold[annotation_id] = annotations[0][annotation_id]

    claim_by_annotation = {}
    for claim in report["claims"]:
        if claim.get("source_role") == "authority":
            claim_by_annotation[_annotation_id(str(claim["claim_id"]))] = claim
    decision_by_claim = {
        str(row["claim_id"]): row for row in report.get("decisions", [])
    }
    results = []
    for annotation_id in sorted(expected_ids):
        claim = claim_by_annotation.get(annotation_id)
        if claim is None:
            raise ValueError("system report lacks " + annotation_id)
        decision = decision_by_claim[str(claim["claim_id"])]
        predicted_support = (
            "supported"
            if claim.get("extraction_status") == "extracted"
            else "unsupported"
        )
        expected = gold[annotation_id]
        results.append(
            {
                "annotation_id": annotation_id,
                "gold_support": expected["support"],
                "system_support": predicted_support,
                "support_match": predicted_support == expected["support"],
                "value_match": (
                    expected["support"] != "supported"
                    or claim.get("value") == expected["value"]
                ),
                "scope_match": (
                    expected["support"] != "supported"
                    or claim.get("scope_level") == expected["scope_level"]
                ),
                "machine_action_match": (
                    expected["support"] != "supported"
                    or decision.get("machine_action") == expected["machine_action"]
                ),
            }
        )
    supported = [row for row in results if row["gold_support"] == "supported"]
    unsupported = [row for row in results if row["gold_support"] == "unsupported"]
    uncertain = [row for row in results if row["gold_support"] == "uncertain"]

    def rate(rows: Sequence[Mapping[str, Any]], key: str) -> float | None:
        return (
            sum(bool(row[key]) for row in rows) / len(rows)
            if rows
            else None
        )

    support_left = [
        annotations[0][key]["support"] for key in sorted(expected_ids)
    ]
    support_right = [
        annotations[1][key]["support"] for key in sorted(expected_ids)
    ]
    summary = {
        "question_count": len(results),
        "supported_gold_count": len(supported),
        "unsupported_gold_count": len(unsupported),
        "uncertain_gold_count": len(uncertain),
        "rater_exact_agreement": sum(
            key not in disagreements for key in expected_ids
        )
        / len(expected_ids),
        "rater_support_kappa": _cohen_kappa(support_left, support_right),
        "adjudicated_disagreement_count": len(disagreements),
        "system_support_accuracy": rate(results, "support_match"),
        "system_value_accuracy_supported": rate(supported, "value_match"),
        "system_scope_accuracy_supported": rate(supported, "scope_match"),
        "system_action_accuracy_supported": rate(
            supported, "machine_action_match"
        ),
        "unsupported_promoted_count": sum(
            row["gold_support"] == "unsupported"
            and row["system_support"] == "supported"
            for row in results
        ),
    }
    thresholds = protocol["decision_rules"]
    summary["pass"] = bool(
        not uncertain
        and summary["rater_exact_agreement"]
        >= float(thresholds["minimum_rater_exact_agreement"])
        and summary["system_support_accuracy"]
        >= float(thresholds["minimum_system_accuracy"])
        and summary["system_value_accuracy_supported"]
        >= float(thresholds["minimum_system_accuracy"])
        and summary["system_scope_accuracy_supported"]
        >= float(thresholds["minimum_system_accuracy"])
        and summary["system_action_accuracy_supported"]
        >= float(thresholds["minimum_system_accuracy"])
        and summary["unsupported_promoted_count"] == 0
    )
    return {
        "schema_version": VERSION,
        "protocol_id": protocol["protocol_id"],
        "protocol_sha256": sha256_file(str(protocol_file)),
        "packet_sha256": sha256_file(str(packet_file)),
        "system_report_sha256": sha256_file(str(report_file)),
        "annotator_attestations": [item[1] for item in loaded],
        "summary": summary,
        "results": results,
        "claim_boundary": (
            "This evaluates accuracy for the frozen authority-question "
            "denominator. It does not estimate open-ended recall for "
            "unregistered facts or validate subject-level measurements."
        ),
    }
