"""Scope-aware, source-bound semantic evidence enrichment.

The module deliberately separates three operations:

* acquisition: retrieve a declared local or HTTPS source and hash its bytes;
* extraction: run a frozen rule against a bounded document section;
* adjudication: compare article-authority claims with repository observations.

An article is never allowed to manufacture subject- or trial-specific
measurements.  It can supply a dataset- or channel-group-level semantic claim
only at the scope declared by the extraction plan.  More-specific repository
observations can corroborate or conflict with that claim.
"""
from __future__ import annotations

import hashlib
import json
import math
import os
import re
import tempfile
import urllib.parse
import urllib.request
import xml.etree.ElementTree as ET
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Dict, List, Mapping, Optional, Sequence, Tuple

import yaml

from harmo.evidence.io import canonical_json_bytes, sha256_file, write_json_atomic
from harmo.evidence.models import JsonContract
from harmo.evidence.run import current_repository_state


SEMANTIC_EVIDENCE_VERSION = "1.0.0"
_SHA256_RE = re.compile(r"^[0-9a-f]{64}$")
_PROPERTY_RE = re.compile(r"^[a-z][a-z0-9_-]*(?:\.[a-z][a-z0-9_-]*)+$")
_ID_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:-]*$")
_SCOPE_LEVELS = {
    "dataset",
    "cohort",
    "subject",
    "session",
    "trial",
    "channel-group",
    "channel",
}
_SOURCE_ROLES = {"authority", "observation"}
_SOURCE_TYPES = {
    "peer-reviewed-article",
    "supplement",
    "repository-metadata",
    "readme",
    "data-dictionary",
    "repository-config",
    "data-file",
}
_EXTRACTION_STATUSES = {"extracted", "not-found", "ambiguous", "invalid"}
_DECISION_STATUSES = {
    "accepted",
    "corroborated",
    "conditional",
    "conflicting",
    "unresolved",
    "rejected",
}
_MACHINE_ACTIONS = {"enrich", "boundary-only"}
_SCOPE_DESCENDANTS = {
    "dataset": {
        "cohort",
        "subject",
        "session",
        "trial",
        "channel-group",
        "channel",
    },
    "cohort": {"subject", "session", "trial", "channel-group", "channel"},
    "subject": {"session", "trial", "channel-group", "channel"},
    "session": {"trial", "channel-group", "channel"},
    "trial": {"channel-group", "channel"},
    "channel-group": {"channel"},
    "channel": set(),
}


def _now() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def _canonical_digest(value: Any) -> str:
    return hashlib.sha256(canonical_json_bytes(value)).hexdigest()


def _normalized_text(value: str) -> str:
    return re.sub(r"\s+", " ", value).strip()


def _canonical_value(value: Any) -> Any:
    if isinstance(value, str):
        return _normalized_text(value).casefold()
    if isinstance(value, list):
        return sorted(
            (_canonical_value(item) for item in value),
            key=lambda item: json.dumps(item, sort_keys=True, ensure_ascii=False),
        )
    if isinstance(value, dict):
        return {
            str(key): _canonical_value(item)
            for key, item in sorted(value.items())
        }
    return value


def _values_equal(left: Any, right: Any) -> bool:
    if (
        isinstance(left, (int, float))
        and not isinstance(left, bool)
        and isinstance(right, (int, float))
        and not isinstance(right, bool)
    ):
        return math.isclose(float(left), float(right), rel_tol=1e-12, abs_tol=1e-12)
    return _canonical_value(left) == _canonical_value(right)


def _selectors_overlap(
    left: Mapping[str, str], right: Mapping[str, str]
) -> bool:
    for key in set(left).intersection(right):
        if str(left[key]).casefold() != str(right[key]).casefold():
            return False
    return True


@dataclass
class SemanticSourceArtifact(JsonContract):
    source_id: str
    study_id: str
    source_type: str
    source_role: str
    identifier: str
    uri: str
    release_binding: str
    content_sha256: Optional[str]
    size_bytes: Optional[int]
    acquisition_status: str
    retrieved_at: Optional[str] = None
    error: Optional[str] = None

    def __post_init__(self) -> None:
        if not _ID_RE.match(self.source_id):
            raise ValueError("invalid semantic source_id")
        if (
            not self.study_id
            or not self.identifier
            or not self.uri
            or not self.release_binding
        ):
            raise ValueError("semantic source identity fields are required")
        if self.source_type not in _SOURCE_TYPES:
            raise ValueError("unsupported semantic source_type")
        if self.source_role not in _SOURCE_ROLES:
            raise ValueError("unsupported semantic source_role")
        if self.acquisition_status not in ("acquired", "failed"):
            raise ValueError("acquisition_status must be acquired or failed")
        if self.acquisition_status == "acquired":
            if not self.content_sha256 or not _SHA256_RE.match(self.content_sha256):
                raise ValueError("acquired semantic source requires SHA-256")
            if self.size_bytes is None or self.size_bytes < 1:
                raise ValueError("acquired semantic source requires positive size")
        if self.content_sha256 is not None and not _SHA256_RE.match(
            self.content_sha256
        ):
            raise ValueError("invalid semantic source SHA-256")


@dataclass
class SemanticEvidenceClaim(JsonContract):
    claim_id: str
    study_id: str
    property_path: str
    value: Any
    scope_level: str
    scope_selector: Dict[str, str]
    propagation: str
    machine_action: str
    source_id: str
    source_type: str
    source_role: str
    source_sha256: Optional[str]
    locator: str
    evidence_sha256: Optional[str]
    extraction_method: str
    extraction_status: str
    match_count: int
    match_spans: List[List[int]] = field(default_factory=list)
    requires_human_review: bool = False
    message: Optional[str] = None
    schema_version: str = SEMANTIC_EVIDENCE_VERSION

    def __post_init__(self) -> None:
        if not _ID_RE.match(self.claim_id):
            raise ValueError("invalid semantic claim_id")
        if not self.study_id or not _PROPERTY_RE.match(self.property_path):
            raise ValueError("semantic claim study and property are required")
        if self.scope_level not in _SCOPE_LEVELS:
            raise ValueError("unsupported semantic claim scope")
        if any(not str(key) or not str(value) for key, value in self.scope_selector.items()):
            raise ValueError("scope selectors require non-empty strings")
        if self.propagation not in ("none", "all-descendants"):
            raise ValueError("unsupported semantic claim propagation")
        if self.machine_action not in _MACHINE_ACTIONS:
            raise ValueError("unsupported semantic claim machine action")
        if self.source_type not in _SOURCE_TYPES or self.source_role not in _SOURCE_ROLES:
            raise ValueError("invalid semantic claim source classification")
        if self.extraction_status not in _EXTRACTION_STATUSES:
            raise ValueError("unsupported extraction status")
        if self.match_count < 0:
            raise ValueError("match_count must be non-negative")
        if self.extraction_status == "extracted":
            if self.value is None:
                raise ValueError("extracted semantic claim requires a value")
            if not self.source_sha256 or not _SHA256_RE.match(self.source_sha256):
                raise ValueError("extracted semantic claim requires source SHA-256")
            if not self.evidence_sha256 or not _SHA256_RE.match(
                self.evidence_sha256
            ):
                raise ValueError("extracted semantic claim requires evidence SHA-256")
        for span in self.match_spans:
            if len(span) != 2 or span[0] < 0 or span[1] <= span[0]:
                raise ValueError("semantic claim match span is invalid")
        if self.schema_version != SEMANTIC_EVIDENCE_VERSION:
            raise ValueError("unsupported semantic claim schema version")

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> "SemanticEvidenceClaim":
        return cls(
            claim_id=str(data["claim_id"]),
            study_id=str(data["study_id"]),
            property_path=str(data["property_path"]),
            value=data.get("value"),
            scope_level=str(data["scope_level"]),
            scope_selector={
                str(key): str(value)
                for key, value in (data.get("scope_selector") or {}).items()
            },
            propagation=str(data.get("propagation", "none")),
            machine_action=str(data.get("machine_action", "enrich")),
            source_id=str(data["source_id"]),
            source_type=str(data["source_type"]),
            source_role=str(data["source_role"]),
            source_sha256=data.get("source_sha256"),
            locator=str(data.get("locator", "document")),
            evidence_sha256=data.get("evidence_sha256"),
            extraction_method=str(data["extraction_method"]),
            extraction_status=str(data["extraction_status"]),
            match_count=int(data.get("match_count", 0)),
            match_spans=[
                [int(value[0]), int(value[1])]
                for value in data.get("match_spans", [])
            ],
            requires_human_review=bool(data.get("requires_human_review", False)),
            message=data.get("message"),
            schema_version=str(
                data.get("schema_version", SEMANTIC_EVIDENCE_VERSION)
            ),
        )


@dataclass
class SemanticClaimDecision(JsonContract):
    claim_id: str
    status: str
    reason: str
    machine_action: str
    evidence_refs: List[str]
    corroborating_claim_ids: List[str] = field(default_factory=list)
    conflicting_claim_ids: List[str] = field(default_factory=list)

    def __post_init__(self) -> None:
        if not _ID_RE.match(self.claim_id) or not self.reason:
            raise ValueError("semantic decision identity and reason are required")
        if self.status not in _DECISION_STATUSES:
            raise ValueError("unsupported semantic decision status")
        if self.machine_action not in _MACHINE_ACTIONS:
            raise ValueError("unsupported semantic decision machine action")
        if self.status in ("accepted", "corroborated", "conditional") and not (
            self.evidence_refs
        ):
            raise ValueError("positive semantic decisions require evidence")


@dataclass
class SemanticEnrichmentReport(JsonContract):
    plan_sha256: str
    implementation_commit: Optional[str]
    code_dirty: Optional[bool]
    working_tree_sha256: Optional[str]
    sources: List[SemanticSourceArtifact]
    claims: List[SemanticEvidenceClaim]
    decisions: List[SemanticClaimDecision]
    summary: Dict[str, int]
    claim_boundary: str
    created_at: str = field(default_factory=_now)
    schema_version: str = SEMANTIC_EVIDENCE_VERSION

    def __post_init__(self) -> None:
        if not _SHA256_RE.match(self.plan_sha256):
            raise ValueError("semantic enrichment report requires plan SHA-256")
        if self.implementation_commit not in (None, "unknown") and not re.match(
            r"^[0-9a-f]{40}$", str(self.implementation_commit)
        ):
            raise ValueError("implementation commit must be a full Git hash")
        if self.working_tree_sha256 is not None and not _SHA256_RE.match(
            self.working_tree_sha256
        ):
            raise ValueError("invalid working-tree SHA-256")
        if not self.sources or not self.claims or not self.decisions:
            raise ValueError("semantic enrichment report cannot be empty")
        claim_ids = [claim.claim_id for claim in self.claims]
        if len(claim_ids) != len(set(claim_ids)):
            raise ValueError("semantic claim IDs must be unique")
        if {decision.claim_id for decision in self.decisions} - set(claim_ids):
            raise ValueError("semantic decisions reference unknown claims")
        if not self.claim_boundary:
            raise ValueError("semantic enrichment claim boundary is required")
        if self.schema_version != SEMANTIC_EVIDENCE_VERSION:
            raise ValueError("unsupported semantic enrichment report version")

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> "SemanticEnrichmentReport":
        return cls(
            plan_sha256=str(data["plan_sha256"]),
            implementation_commit=data.get("implementation_commit"),
            code_dirty=data.get("code_dirty"),
            working_tree_sha256=data.get("working_tree_sha256"),
            sources=[
                SemanticSourceArtifact(**value)
                for value in data.get("sources", [])
            ],
            claims=[
                SemanticEvidenceClaim.from_dict(value)
                for value in data.get("claims", [])
            ],
            decisions=[
                SemanticClaimDecision(**value)
                for value in data.get("decisions", [])
            ],
            summary={
                str(key): int(value)
                for key, value in (data.get("summary") or {}).items()
            },
            claim_boundary=str(data["claim_boundary"]),
            created_at=str(data.get("created_at", _now())),
            schema_version=str(
                data.get("schema_version", SEMANTIC_EVIDENCE_VERSION)
            ),
        )


@dataclass
class SemanticOverlayEntry(JsonContract):
    """One accepted claim, still bound to its source and declared scope."""

    claim_id: str
    property_path: str
    value: Any
    claim_scope_level: str
    claim_scope_selector: Dict[str, str]
    propagation: str
    decision_status: str
    evidence_refs: List[str]

    def __post_init__(self) -> None:
        if not _ID_RE.match(self.claim_id):
            raise ValueError("invalid semantic overlay claim_id")
        if not _PROPERTY_RE.match(self.property_path):
            raise ValueError("invalid semantic overlay property path")
        if self.claim_scope_level not in _SCOPE_LEVELS:
            raise ValueError("unsupported semantic overlay claim scope")
        if self.propagation not in ("none", "all-descendants"):
            raise ValueError("unsupported semantic overlay propagation")
        if self.decision_status not in ("accepted", "corroborated"):
            raise ValueError("semantic overlay accepts only positive decisions")
        if not self.evidence_refs:
            raise ValueError("semantic overlay entry requires evidence")

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> "SemanticOverlayEntry":
        return cls(
            claim_id=str(data["claim_id"]),
            property_path=str(data["property_path"]),
            value=data.get("value"),
            claim_scope_level=str(data["claim_scope_level"]),
            claim_scope_selector={
                str(key): str(value)
                for key, value in (
                    data.get("claim_scope_selector") or {}
                ).items()
            },
            propagation=str(data["propagation"]),
            decision_status=str(data["decision_status"]),
            evidence_refs=[str(value) for value in data.get("evidence_refs", [])],
        )


@dataclass
class SemanticOverlay(JsonContract):
    """Safe, immutable bridge from a literature report to R/M/T/C/D.

    The overlay is intentionally not a patch. Consumers can verify or expose
    its entries, but cannot silently promote them beyond the bound target.
    """

    overlay_id: str
    study_id: str
    target_scope_level: str
    target_scope_selector: Dict[str, str]
    report_sha256: str
    entries: List[SemanticOverlayEntry]
    blocked_claims: List[Dict[str, str]]
    skipped_claims: List[Dict[str, str]]
    claim_boundary: str
    created_at: str = field(default_factory=_now)
    schema_version: str = SEMANTIC_EVIDENCE_VERSION

    def __post_init__(self) -> None:
        if not _ID_RE.match(self.overlay_id) or not self.study_id:
            raise ValueError("semantic overlay identity is required")
        if self.target_scope_level not in _SCOPE_LEVELS:
            raise ValueError("unsupported semantic overlay target scope")
        if any(
            not str(key) or not str(value)
            for key, value in self.target_scope_selector.items()
        ):
            raise ValueError("overlay target selectors require non-empty strings")
        if not _SHA256_RE.match(self.report_sha256):
            raise ValueError("semantic overlay requires report SHA-256")
        if not self.entries:
            raise ValueError("semantic overlay cannot be empty")
        claim_ids = [entry.claim_id for entry in self.entries]
        if len(claim_ids) != len(set(claim_ids)):
            raise ValueError("semantic overlay claim IDs must be unique")
        if not self.claim_boundary:
            raise ValueError("semantic overlay claim boundary is required")
        if self.schema_version != SEMANTIC_EVIDENCE_VERSION:
            raise ValueError("unsupported semantic overlay version")

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> "SemanticOverlay":
        return cls(
            overlay_id=str(data["overlay_id"]),
            study_id=str(data["study_id"]),
            target_scope_level=str(data["target_scope_level"]),
            target_scope_selector={
                str(key): str(value)
                for key, value in (
                    data.get("target_scope_selector") or {}
                ).items()
            },
            report_sha256=str(data["report_sha256"]),
            entries=[
                SemanticOverlayEntry.from_dict(value)
                for value in data.get("entries", [])
            ],
            blocked_claims=[
                {str(key): str(value) for key, value in item.items()}
                for item in data.get("blocked_claims", [])
            ],
            skipped_claims=[
                {str(key): str(value) for key, value in item.items()}
                for item in data.get("skipped_claims", [])
            ],
            claim_boundary=str(data["claim_boundary"]),
            created_at=str(data.get("created_at", _now())),
            schema_version=str(
                data.get("schema_version", SEMANTIC_EVIDENCE_VERSION)
            ),
        )


@dataclass
class SemanticCoverageComparisonReport(JsonContract):
    """File-only versus paper-assisted semantic coverage, by study."""

    enrichment_report_sha256: str
    study_rows: List[Dict[str, Any]]
    summary: Dict[str, int]
    claim_boundary: str
    created_at: str = field(default_factory=_now)
    schema_version: str = SEMANTIC_EVIDENCE_VERSION

    def __post_init__(self) -> None:
        if not _SHA256_RE.match(self.enrichment_report_sha256):
            raise ValueError("coverage comparison requires report SHA-256")
        if not self.study_rows or not self.claim_boundary:
            raise ValueError("coverage comparison cannot be empty")
        study_ids = [str(row.get("study_id", "")) for row in self.study_rows]
        if any(not value for value in study_ids) or len(study_ids) != len(
            set(study_ids)
        ):
            raise ValueError("coverage comparison study IDs must be unique")
        if self.schema_version != SEMANTIC_EVIDENCE_VERSION:
            raise ValueError("unsupported coverage comparison version")

    @classmethod
    def from_dict(
        cls, data: Mapping[str, Any]
    ) -> "SemanticCoverageComparisonReport":
        return cls(
            enrichment_report_sha256=str(
                data["enrichment_report_sha256"]
            ),
            study_rows=[dict(value) for value in data.get("study_rows", [])],
            summary={
                str(key): int(value)
                for key, value in (data.get("summary") or {}).items()
            },
            claim_boundary=str(data["claim_boundary"]),
            created_at=str(data.get("created_at", _now())),
            schema_version=str(
                data.get("schema_version", SEMANTIC_EVIDENCE_VERSION)
            ),
        )


def compare_semantic_coverage(
    report: SemanticEnrichmentReport,
    *,
    report_sha256: str,
    study_ids: Sequence[str],
) -> SemanticCoverageComparisonReport:
    """Measure what papers add without treating them as measurements."""
    if not _SHA256_RE.match(report_sha256):
        raise ValueError("report_sha256 must be lowercase SHA-256")
    ordered_studies = [str(value) for value in study_ids]
    if not ordered_studies or len(ordered_studies) != len(set(ordered_studies)):
        raise ValueError("study_ids must be non-empty and unique")
    known_studies = {claim.study_id for claim in report.claims}
    unknown = sorted(set(ordered_studies) - known_studies)
    if unknown:
        raise ValueError(
            "coverage comparison references unknown studies: {}".format(
                unknown
            )
        )
    decisions = {decision.claim_id: decision for decision in report.decisions}
    rows: List[Dict[str, Any]] = []
    for study_id in ordered_studies:
        claims = [claim for claim in report.claims if claim.study_id == study_id]
        observations = [
            claim
            for claim in claims
            if claim.source_role == "observation"
            and claim.extraction_status == "extracted"
        ]
        positive_authority = [
            claim
            for claim in claims
            if claim.source_role == "authority"
            and decisions[claim.claim_id].status in ("accepted", "corroborated")
            and decisions[claim.claim_id].machine_action == "enrich"
        ]
        boundary_authority = [
            claim
            for claim in claims
            if claim.source_role == "authority"
            and decisions[claim.claim_id].status
            in ("accepted", "corroborated", "conditional")
            and decisions[claim.claim_id].machine_action == "boundary-only"
        ]
        file_properties = sorted(
            {claim.property_path for claim in observations}
        )
        authority_properties = sorted(
            {claim.property_path for claim in positive_authority}
        )
        assisted_properties = sorted(
            set(file_properties) | set(authority_properties)
        )
        added_properties = sorted(
            set(authority_properties) - set(file_properties)
        )
        emg_prefixes = (
            "recording.emg.",
            "processing.emg.",
            "acquisition.emg.",
            "limitations.emg.",
        )
        rows.append(
            {
                "study_id": study_id,
                "file_only_claim_count": len(observations),
                "file_only_property_count": len(file_properties),
                "file_only_properties": file_properties,
                "authority_enrichment_claim_count": len(positive_authority),
                "authority_enrichment_property_count": len(
                    authority_properties
                ),
                "authority_enrichment_properties": authority_properties,
                "paper_assisted_property_count": len(assisted_properties),
                "paper_assisted_properties": assisted_properties,
                "properties_added_by_literature_count": len(added_properties),
                "properties_added_by_literature": added_properties,
                "corroborated_authority_claim_count": sum(
                    decisions[claim.claim_id].status == "corroborated"
                    for claim in positive_authority
                ),
                "boundary_claim_count": len(boundary_authority),
                "boundary_properties": sorted(
                    {claim.property_path for claim in boundary_authority}
                ),
                "conflicting_claim_count": sum(
                    decisions[claim.claim_id].status == "conflicting"
                    for claim in claims
                    if claim.source_role == "authority"
                ),
                "unresolved_claim_count": sum(
                    decisions[claim.claim_id].status == "unresolved"
                    for claim in claims
                    if claim.source_role == "authority"
                ),
                "emg_semantics_present": any(
                    property_path.startswith(emg_prefixes)
                    for property_path in assisted_properties
                ),
                "paper_adds_emg_semantics": any(
                    property_path.startswith(emg_prefixes)
                    for property_path in added_properties
                ),
                "authority_source_ids": sorted(
                    {
                        claim.source_id
                        for claim in claims
                        if claim.source_role == "authority"
                    }
                ),
                "observation_source_ids": sorted(
                    {
                        claim.source_id
                        for claim in claims
                        if claim.source_role == "observation"
                    }
                ),
            }
        )
    return SemanticCoverageComparisonReport(
        enrichment_report_sha256=report_sha256,
        study_rows=rows,
        summary={
            "study_count": len(rows),
            "studies_with_file_only_evidence": sum(
                row["file_only_claim_count"] > 0 for row in rows
            ),
            "studies_with_authority_evidence": sum(
                row["authority_enrichment_claim_count"] > 0 for row in rows
            ),
            "studies_with_literature_added_properties": sum(
                row["properties_added_by_literature_count"] > 0 for row in rows
            ),
            "emg_semantic_study_count": sum(
                row["emg_semantics_present"] for row in rows
            ),
            "studies_with_literature_added_emg_properties": sum(
                row["paper_adds_emg_semantics"] for row in rows
            ),
            "file_only_study_property_pairs": sum(
                row["file_only_property_count"] for row in rows
            ),
            "paper_assisted_study_property_pairs": sum(
                row["paper_assisted_property_count"] for row in rows
            ),
            "literature_added_study_property_pairs": sum(
                row["properties_added_by_literature_count"] for row in rows
            ),
            "corroborated_authority_claim_count": sum(
                row["corroborated_authority_claim_count"] for row in rows
            ),
            "boundary_claim_count": sum(
                row["boundary_claim_count"] for row in rows
            ),
            "conflicting_claim_count": sum(
                row["conflicting_claim_count"] for row in rows
            ),
            "unresolved_claim_count": sum(
                row["unresolved_claim_count"] for row in rows
            ),
        },
        claim_boundary=(
            "Coverage counts declared properties, not recording completeness. "
            "Paper-assisted properties remain at their source scope; they do "
            "not create subject/trial observations or prove comparability."
        ),
    )


def _claim_applies_to_target(
    claim: SemanticEvidenceClaim,
    target_scope_level: str,
    target_scope_selector: Mapping[str, str],
) -> Tuple[bool, str]:
    for key, value in claim.scope_selector.items():
        if key not in target_scope_selector:
            return False, "target selector lacks {}".format(key)
        if str(target_scope_selector[key]).casefold() != str(value).casefold():
            return False, "target selector differs for {}".format(key)
    if claim.scope_level == target_scope_level:
        return True, "exact scope"
    if (
        claim.propagation == "all-descendants"
        and target_scope_level in _SCOPE_DESCENDANTS[claim.scope_level]
    ):
        return True, "declared descendant propagation"
    return False, "claim does not propagate to target scope"


def build_semantic_overlay(
    report: SemanticEnrichmentReport,
    *,
    report_sha256: str,
    study_id: str,
    target_scope_level: str,
    target_scope_selector: Mapping[str, str],
) -> SemanticOverlay:
    """Select only adjudicated, scope-applicable enrichment claims."""
    if not _SHA256_RE.match(report_sha256):
        raise ValueError("report_sha256 must be lowercase SHA-256")
    if target_scope_level not in _SCOPE_LEVELS:
        raise ValueError("unsupported semantic overlay target scope")
    decisions = {decision.claim_id: decision for decision in report.decisions}
    entries: List[SemanticOverlayEntry] = []
    blocked: List[Dict[str, str]] = []
    skipped: List[Dict[str, str]] = []
    property_values: Dict[str, Any] = {}
    for claim in report.claims:
        if claim.study_id != study_id or claim.source_role != "authority":
            continue
        decision = decisions.get(claim.claim_id)
        if decision is None:
            blocked.append(
                {
                    "claim_id": claim.claim_id,
                    "status": "unresolved",
                    "reason": "claim has no adjudication decision",
                }
            )
            continue
        if (
            decision.status not in ("accepted", "corroborated")
            or decision.machine_action != "enrich"
        ):
            blocked.append(
                {
                    "claim_id": claim.claim_id,
                    "status": decision.status,
                    "reason": (
                        "machine action is {}".format(decision.machine_action)
                        if decision.machine_action != "enrich"
                        else decision.reason
                    ),
                }
            )
            continue
        applies, reason = _claim_applies_to_target(
            claim, target_scope_level, target_scope_selector
        )
        if not applies:
            skipped.append(
                {
                    "claim_id": claim.claim_id,
                    "status": decision.status,
                    "reason": reason,
                }
            )
            continue
        existing = property_values.get(claim.property_path)
        if (
            claim.property_path in property_values
            and not _values_equal(existing, claim.value)
        ):
            raise ValueError(
                "positive claims disagree for {}".format(claim.property_path)
            )
        property_values[claim.property_path] = claim.value
        entries.append(
            SemanticOverlayEntry(
                claim_id=claim.claim_id,
                property_path=claim.property_path,
                value=claim.value,
                claim_scope_level=claim.scope_level,
                claim_scope_selector=dict(claim.scope_selector),
                propagation=claim.propagation,
                decision_status=decision.status,
                evidence_refs=list(decision.evidence_refs),
            )
        )
    if not entries:
        raise ValueError(
            "no positive semantic claims apply to the declared overlay target"
        )
    identity = {
        "study_id": study_id,
        "target_scope_level": target_scope_level,
        "target_scope_selector": dict(target_scope_selector),
        "report_sha256": report_sha256,
        "entries": [entry.to_dict() for entry in entries],
    }
    return SemanticOverlay(
        overlay_id="semantic-overlay-{}".format(_canonical_digest(identity)[:20]),
        study_id=study_id,
        target_scope_level=target_scope_level,
        target_scope_selector={
            str(key): str(value)
            for key, value in target_scope_selector.items()
        },
        report_sha256=report_sha256,
        entries=entries,
        blocked_claims=blocked,
        skipped_claims=skipped,
        claim_boundary=(
            "{} Overlay entries remain evidence-backed assertions at the "
            "declared target. They do not overwrite source measurements, "
            "assign channel anatomy, or create subject/trial observations."
        ).format(report.claim_boundary),
    )


@dataclass
class _AcquiredSource:
    declaration: Mapping[str, Any]
    artifact: SemanticSourceArtifact
    content: Optional[bytes]


def _validate_https_url(url: str) -> None:
    parsed = urllib.parse.urlparse(url)
    if parsed.scheme != "https" or not parsed.netloc:
        raise ValueError("remote semantic sources require an absolute HTTPS URL")


def _safe_cache_path(cache_root: Path, source_id: str, format_name: str) -> Path:
    suffix = {
        "jats-xml": ".xml",
        "xml": ".xml",
        "html": ".html",
        "text": ".txt",
        "yaml": ".yaml",
        "json": ".json",
    }.get(format_name, ".bin")
    target = (cache_root / (source_id + suffix)).resolve()
    if cache_root.resolve() not in target.parents:
        raise ValueError("semantic cache path escapes cache root")
    return target


def _atomic_write_bytes(path: Path, content: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, temporary = tempfile.mkstemp(prefix=path.name + ".", dir=str(path.parent))
    try:
        with os.fdopen(fd, "wb") as handle:
            handle.write(content)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, str(path))
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)


def _fetch_https(url: str, maximum_bytes: int = 20 * 1024 * 1024) -> bytes:
    _validate_https_url(url)
    request = urllib.request.Request(
        url,
        headers={"User-Agent": "harmo-semantic-evidence/1.0"},
    )
    with urllib.request.urlopen(request, timeout=60) as response:  # nosec B310
        final_url = response.geturl()
        _validate_https_url(final_url)
        content = response.read(maximum_bytes + 1)
    if len(content) > maximum_bytes:
        raise ValueError("semantic source exceeds the acquisition byte limit")
    if not content:
        raise ValueError("semantic source response is empty")
    return content


def _acquire_source(
    declaration: Mapping[str, Any],
    repository_root: Path,
    cache_root: Path,
    offline: bool,
    refresh: bool,
    fetcher: Optional[Callable[[str], bytes]] = None,
) -> _AcquiredSource:
    source_id = str(declaration["source_id"])
    study_id = str(declaration["study_id"])
    format_name = str(declaration.get("format", "text"))
    local_path = declaration.get("path")
    url = declaration.get("url")
    retrieved_at: Optional[str] = None
    content: Optional[bytes] = None
    uri = ""
    try:
        if bool(local_path) == bool(url):
            raise ValueError("semantic source requires exactly one of path or url")
        if local_path:
            path = (repository_root / str(local_path)).resolve()
            if repository_root.resolve() not in path.parents:
                raise ValueError("semantic source path escapes repository root")
            content = path.read_bytes()
            uri = str(path.relative_to(repository_root))
        else:
            url_value = str(url)
            _validate_https_url(url_value)
            cache_path = _safe_cache_path(cache_root, source_id, format_name)
            if offline:
                content = cache_path.read_bytes()
            elif cache_path.is_file() and not refresh:
                content = cache_path.read_bytes()
            else:
                content = (fetcher or _fetch_https)(url_value)
                _atomic_write_bytes(cache_path, content)
            uri = url_value
            retrieved_at = _now()
        if not content:
            raise ValueError("semantic source is empty")
        digest = hashlib.sha256(content).hexdigest()
        expected = declaration.get("expected_sha256")
        if expected is not None and digest != str(expected):
            raise ValueError(
                "semantic source digest differs: expected {}, observed {}".format(
                    expected, digest
                )
            )
        artifact = SemanticSourceArtifact(
            source_id=source_id,
            study_id=study_id,
            source_type=str(declaration["source_type"]),
            source_role=str(declaration["source_role"]),
            identifier=str(declaration["identifier"]),
            uri=uri,
            release_binding=str(declaration["release_binding"]),
            content_sha256=digest,
            size_bytes=len(content),
            acquisition_status="acquired",
            retrieved_at=retrieved_at,
        )
        return _AcquiredSource(declaration, artifact, content)
    except Exception as exc:
        error = "{}: {}".format(type(exc).__name__, exc)
        artifact = SemanticSourceArtifact(
            source_id=source_id,
            study_id=study_id,
            source_type=str(declaration["source_type"]),
            source_role=str(declaration["source_role"]),
            identifier=str(declaration["identifier"]),
            uri=str(local_path or url or ""),
            release_binding=str(declaration["release_binding"]),
            content_sha256=None,
            size_bytes=None,
            acquisition_status="failed",
            retrieved_at=retrieved_at,
            error=error,
        )
        return _AcquiredSource(declaration, artifact, None)


def _xml_sections(content: bytes) -> Dict[str, str]:
    root = ET.fromstring(content)
    sections: Dict[str, List[str]] = {"document": []}
    for paragraph in root.findall(".//p"):
        text = _normalized_text("".join(paragraph.itertext()))
        if text:
            sections["document"].append(text)
    for section in root.findall(".//sec"):
        title_element = section.find("./title")
        if title_element is None:
            continue
        title = _normalized_text("".join(title_element.itertext()))
        if not title:
            continue
        paragraphs = []
        for paragraph in section.findall("./p"):
            text = _normalized_text("".join(paragraph.itertext()))
            if text:
                paragraphs.append(text)
        if paragraphs:
            sections.setdefault(title.casefold(), []).extend(paragraphs)
    if not sections["document"]:
        fallback = _normalized_text(" ".join(root.itertext()))
        if fallback:
            sections["document"].append(fallback)
    return {
        key: _normalized_text(" ".join(values))
        for key, values in sections.items()
    }


def _document_sections(content: bytes, format_name: str) -> Dict[str, str]:
    if format_name in ("jats-xml", "xml"):
        return _xml_sections(content)
    return {"document": _normalized_text(content.decode("utf-8"))}


def _coerce_value(value: Any, value_type: str) -> Any:
    if value_type == "number":
        number = float(str(value))
        return int(number) if number.is_integer() else number
    if value_type == "integer":
        return int(str(value))
    if value_type == "boolean":
        normalized = str(value).strip().casefold()
        if normalized in ("true", "yes", "1"):
            return True
        if normalized in ("false", "no", "0"):
            return False
        raise ValueError("cannot coerce semantic value to boolean")
    if value_type == "string":
        return _normalized_text(str(value))
    raise ValueError("unsupported semantic extraction value_type")


def _match_claim(
    source: _AcquiredSource, rule: Mapping[str, Any]
) -> SemanticEvidenceClaim:
    artifact = source.artifact
    base = {
        "claim_id": str(rule["claim_id"]),
        "study_id": artifact.study_id,
        "property_path": str(rule["property_path"]),
        "scope_level": str(rule["scope_level"]),
        "scope_selector": {
            str(key): str(value)
            for key, value in (rule.get("scope_selector") or {}).items()
        },
        "propagation": str(rule.get("propagation", "none")),
        "machine_action": str(rule.get("machine_action", "enrich")),
        "source_id": artifact.source_id,
        "source_type": artifact.source_type,
        "source_role": artifact.source_role,
        "source_sha256": artifact.content_sha256,
        "locator": str(rule.get("locator", "document")),
        "extraction_method": "frozen-regex:{}".format(rule.get("mode", "capture")),
        "requires_human_review": bool(rule.get("requires_human_review", False)),
    }
    if source.content is None:
        return SemanticEvidenceClaim(
            value=None,
            evidence_sha256=None,
            extraction_status="invalid",
            match_count=0,
            match_spans=[],
            message=artifact.error or "source acquisition failed",
            **base
        )
    try:
        sections = _document_sections(
            source.content, str(source.declaration.get("format", "text"))
        )
        locator = str(rule.get("locator", "document"))
        section = sections.get(locator.casefold())
        if section is None:
            raise KeyError("declared locator is absent from source")
        flags = re.IGNORECASE
        if bool(rule.get("dotall", False)):
            flags |= re.DOTALL
        pattern = re.compile(str(rule["pattern"]), flags)
        matches = list(pattern.finditer(section))
        spans = [[match.start(), match.end()] for match in matches]
        if not matches:
            return SemanticEvidenceClaim(
                value=None,
                evidence_sha256=None,
                extraction_status="not-found",
                match_count=0,
                match_spans=[],
                message="frozen extraction rule did not match",
                **base
            )
        mode = str(rule.get("mode", "capture"))
        value_type = str(rule.get("value_type", "string"))
        if mode == "capture":
            values = [
                _coerce_value(match.group("value"), value_type)
                for match in matches
            ]
            unique: List[Any] = []
            for value in values:
                if not any(_values_equal(value, existing) for existing in unique):
                    unique.append(value)
            if len(unique) != 1:
                return SemanticEvidenceClaim(
                    value=values,
                    evidence_sha256=_canonical_digest(
                        [_normalized_text(match.group(0)) for match in matches]
                    ),
                    extraction_status="ambiguous",
                    match_count=len(matches),
                    match_spans=spans,
                    message="capture rule produced multiple distinct values",
                    **base
                )
            value = unique[0]
        elif mode == "presence":
            value = rule["expected"]
        elif mode == "count":
            value = len(matches)
        elif mode == "set":
            values = [
                _coerce_value(match.group("value"), value_type)
                for match in matches
            ]
            value = sorted(
                {
                    _normalized_text(str(item))
                    for item in values
                },
                key=str.casefold,
            )
        else:
            raise ValueError("unsupported semantic extraction mode")
        expected = rule.get("expected")
        if expected is not None and not _values_equal(value, expected):
            return SemanticEvidenceClaim(
                value=value,
                evidence_sha256=_canonical_digest(
                    [_normalized_text(match.group(0)) for match in matches]
                ),
                extraction_status="invalid",
                match_count=len(matches),
                match_spans=spans,
                message="extracted value differs from frozen expected value",
                **base
            )
        return SemanticEvidenceClaim(
            value=value,
            evidence_sha256=_canonical_digest(
                [_normalized_text(match.group(0)) for match in matches]
            ),
            extraction_status="extracted",
            match_count=len(matches),
            match_spans=spans,
            **base
        )
    except Exception as exc:
        return SemanticEvidenceClaim(
            value=None,
            evidence_sha256=None,
            extraction_status="invalid",
            match_count=0,
            match_spans=[],
            message="{}: {}".format(type(exc).__name__, exc),
            **base
        )


def adjudicate_semantic_claims(
    claims: Sequence[SemanticEvidenceClaim],
) -> List[SemanticClaimDecision]:
    """Adjudicate authority claims against same-property observed evidence."""
    observations = [
        claim for claim in claims if claim.source_role == "observation"
    ]
    decisions: List[SemanticClaimDecision] = []
    for claim in claims:
        if claim.source_role != "authority":
            continue
        evidence_refs = []
        if claim.evidence_sha256:
            evidence_refs.append(
                "{}:sha256:{}".format(claim.claim_id, claim.evidence_sha256)
            )
        if claim.extraction_status != "extracted":
            decisions.append(
                SemanticClaimDecision(
                    claim_id=claim.claim_id,
                    status="unresolved",
                    reason=claim.message or "authority claim was not extracted",
                    machine_action=claim.machine_action,
                    evidence_refs=[],
                )
            )
            continue
        relevant = [
            observed
            for observed in observations
            if observed.study_id == claim.study_id
            and observed.property_path == claim.property_path
            and observed.extraction_status == "extracted"
            and _selectors_overlap(
                observed.scope_selector, claim.scope_selector
            )
        ]
        conflicts = [
            observed
            for observed in relevant
            if not _values_equal(observed.value, claim.value)
        ]
        corroborating = [
            observed
            for observed in relevant
            if _values_equal(observed.value, claim.value)
        ]
        if conflicts:
            decisions.append(
                SemanticClaimDecision(
                    claim_id=claim.claim_id,
                    status="conflicting",
                    reason=(
                        "more-specific repository evidence conflicts with the "
                        "article-authority claim"
                    ),
                    machine_action=claim.machine_action,
                    evidence_refs=evidence_refs
                    + [
                        "{}:sha256:{}".format(
                            item.claim_id, item.evidence_sha256
                        )
                        for item in conflicts
                        if item.evidence_sha256
                    ],
                    conflicting_claim_ids=[
                        item.claim_id for item in conflicts
                    ],
                )
            )
        elif claim.requires_human_review:
            decisions.append(
                SemanticClaimDecision(
                    claim_id=claim.claim_id,
                    status="conditional",
                    reason="frozen rule requires human semantic review",
                    machine_action=claim.machine_action,
                    evidence_refs=evidence_refs,
                    corroborating_claim_ids=[
                        item.claim_id for item in corroborating
                    ],
                )
            )
        elif corroborating:
            decisions.append(
                SemanticClaimDecision(
                    claim_id=claim.claim_id,
                    status="corroborated",
                    reason=(
                        "article claim is explicitly extracted and agrees with "
                        "repository evidence at an overlapping scope"
                    ),
                    machine_action=claim.machine_action,
                    evidence_refs=evidence_refs
                    + [
                        "{}:sha256:{}".format(
                            item.claim_id, item.evidence_sha256
                        )
                        for item in corroborating
                        if item.evidence_sha256
                    ],
                    corroborating_claim_ids=[
                        item.claim_id for item in corroborating
                    ],
                )
            )
        else:
            decisions.append(
                SemanticClaimDecision(
                    claim_id=claim.claim_id,
                    status="accepted",
                    reason=(
                        "explicit article claim is accepted only at its declared "
                        "scope; no recording-specific observation was available"
                    ),
                    machine_action=claim.machine_action,
                    evidence_refs=evidence_refs,
                )
            )
    return decisions


def _validate_plan(plan: Mapping[str, Any]) -> None:
    if plan.get("schema_version") != SEMANTIC_EVIDENCE_VERSION:
        raise ValueError(
            "semantic evidence plan schema_version must be {}".format(
                SEMANTIC_EVIDENCE_VERSION
            )
        )
    sources = plan.get("sources")
    if not isinstance(sources, list) or not sources:
        raise ValueError("semantic evidence plan requires sources")
    source_ids = []
    claim_ids = []
    for source in sources:
        if not isinstance(source, Mapping):
            raise ValueError("semantic source declaration must be an object")
        for key in (
            "source_id",
            "study_id",
            "source_type",
            "source_role",
            "identifier",
            "release_binding",
            "format",
            "claims",
        ):
            if key not in source:
                raise ValueError("semantic source lacks {}".format(key))
        source_ids.append(str(source["source_id"]))
        if source["source_type"] not in _SOURCE_TYPES:
            raise ValueError("unsupported semantic source_type")
        if source["source_role"] not in _SOURCE_ROLES:
            raise ValueError("unsupported semantic source_role")
        claims = source.get("claims")
        if not isinstance(claims, list) or not claims:
            raise ValueError("semantic source requires extraction claims")
        for claim in claims:
            for key in (
                "claim_id",
                "property_path",
                "scope_level",
                "pattern",
            ):
                if key not in claim:
                    raise ValueError("semantic extraction claim lacks {}".format(key))
            claim_ids.append(str(claim["claim_id"]))
    if len(source_ids) != len(set(source_ids)):
        raise ValueError("semantic source IDs must be unique")
    if len(claim_ids) != len(set(claim_ids)):
        raise ValueError("semantic claim IDs must be unique")


def run_semantic_enrichment(
    plan_path: str,
    repository_root: str,
    cache_dir: str,
    output_path: Optional[str] = None,
    offline: bool = False,
    refresh: bool = False,
    fetcher: Optional[Callable[[str], bytes]] = None,
) -> SemanticEnrichmentReport:
    """Run one frozen, source-bound semantic enrichment plan."""
    plan_file = Path(plan_path).resolve()
    root = Path(repository_root).resolve()
    cache_root = Path(cache_dir).resolve()
    plan = yaml.safe_load(plan_file.read_text(encoding="utf-8")) or {}
    if not isinstance(plan, Mapping):
        raise ValueError("semantic evidence plan root must be an object")
    _validate_plan(plan)
    acquired = [
        _acquire_source(
            declaration=source,
            repository_root=root,
            cache_root=cache_root,
            offline=offline,
            refresh=refresh,
            fetcher=fetcher,
        )
        for source in plan["sources"]
    ]
    claims = [
        _match_claim(source, rule)
        for source in acquired
        for rule in source.declaration["claims"]
    ]
    decisions = adjudicate_semantic_claims(claims)
    decision_counts: Dict[str, int] = {
        status: 0 for status in sorted(_DECISION_STATUSES)
    }
    for decision in decisions:
        decision_counts[decision.status] += 1
    repository_state = current_repository_state(str(root))
    report = SemanticEnrichmentReport(
        plan_sha256=sha256_file(str(plan_file)),
        implementation_commit=(
            str(repository_state.get("commit") or "unknown")
        ),
        code_dirty=repository_state.get("dirty"),
        working_tree_sha256=repository_state.get("working_tree_sha256"),
        sources=[source.artifact for source in acquired],
        claims=claims,
        decisions=decisions,
        summary={
            "source_count": len(acquired),
            "source_acquired": sum(
                source.artifact.acquisition_status == "acquired"
                for source in acquired
            ),
            "claim_count": len(claims),
            "authority_claim_count": sum(
                claim.source_role == "authority" for claim in claims
            ),
            "observation_claim_count": sum(
                claim.source_role == "observation" for claim in claims
            ),
            "extracted_claim_count": sum(
                claim.extraction_status == "extracted" for claim in claims
            ),
            "enrichment_ready_count": sum(
                decision.status in ("accepted", "corroborated")
                and decision.machine_action == "enrich"
                for decision in decisions
            ),
            "boundary_evidence_count": sum(
                decision.status in ("accepted", "corroborated", "conditional")
                and decision.machine_action == "boundary-only"
                for decision in decisions
            ),
            **{
                "{}_decision_count".format(status): count
                for status, count in decision_counts.items()
            },
        },
        claim_boundary=str(plan["claim_boundary"]),
    )
    if output_path:
        write_json_atomic(output_path, report)
    return report
