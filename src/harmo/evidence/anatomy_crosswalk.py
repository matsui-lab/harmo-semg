"""Checksum-bound anatomical ontology crosswalks.

Crosswalk files are curated assertions, not ontology truth.  A crosswalk only
becomes usable by the comparability policy after this module has verified the
declared ontology release checksum, version header, term identifiers, labels,
and exact synonyms against the release asset itself.
"""
from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path
from typing import Dict, Iterable, List, Mapping, Optional, Tuple

import yaml

from harmo.evidence.io import sha256_file
from harmo.evidence.models import JsonContract, OntologyTerm


_SHA256_RE = re.compile(r"^[0-9a-f]{64}$")
_EXACT_SYNONYM_RE = re.compile(r'^synonym:\s+"(.*)"\s+EXACT(?:\s|$)')
_SUPPORTED_PREDICATES = ("exactMatch", "closeMatch", "broadMatch")
_SUPPORTED_MATCH_BASIS = ("preferred-label", "exact-synonym")
_VERIFIED_TOKEN = object()


@dataclass(frozen=True)
class OntologyRelease(JsonContract):
    ontology: str
    release_tag: str
    version_iri: str
    data_version: str
    asset_url: str
    asset_sha256: str
    asset_size_bytes: int
    license: str
    license_url: str

    def __post_init__(self) -> None:
        if not all(
            (
                self.ontology,
                self.release_tag,
                self.version_iri,
                self.data_version,
                self.asset_url,
                self.license,
                self.license_url,
            )
        ):
            raise ValueError("ontology release fields are required")
        if not self.asset_url.startswith("https://"):
            raise ValueError("ontology asset_url must use HTTPS")
        if not self.license_url.startswith("https://"):
            raise ValueError("ontology license_url must use HTTPS")
        if not _SHA256_RE.fullmatch(self.asset_sha256):
            raise ValueError("ontology asset_sha256 is invalid")
        if self.asset_size_bytes <= 0:
            raise ValueError("ontology asset_size_bytes must be positive")


@dataclass(frozen=True)
class AnatomyCrosswalkEntry(JsonContract):
    source_term: OntologyTerm
    target_term: OntologyTerm
    predicate: str
    match_basis: str
    matched_text: str

    def __post_init__(self) -> None:
        if self.predicate not in _SUPPORTED_PREDICATES:
            raise ValueError(
                "unsupported anatomy crosswalk predicate: {}".format(
                    self.predicate
                )
            )
        if self.match_basis not in _SUPPORTED_MATCH_BASIS:
            raise ValueError(
                "unsupported anatomy match basis: {}".format(self.match_basis)
            )
        if not self.matched_text:
            raise ValueError("anatomy crosswalk matched_text is required")

    def term_pair(self) -> Tuple[Tuple[str, str, str], Tuple[str, str, str]]:
        return (_term_key(self.source_term), _term_key(self.target_term))


@dataclass(frozen=True)
class AnatomyCrosswalk(JsonContract):
    schema_version: str
    crosswalk_id: str
    source_ontology: str
    source_version: str
    target_release: OntologyRelease
    entries: Tuple[AnatomyCrosswalkEntry, ...]

    def __post_init__(self) -> None:
        if self.schema_version != "1.0.0":
            raise ValueError("anatomy crosswalk schema_version must be 1.0.0")
        if not all(
            (self.crosswalk_id, self.source_ontology, self.source_version)
        ):
            raise ValueError("anatomy crosswalk identity fields are required")
        if not self.entries:
            raise ValueError("anatomy crosswalk requires entries")
        source_ids = [entry.source_term.term_id for entry in self.entries]
        if len(source_ids) != len(set(source_ids)):
            raise ValueError("anatomy crosswalk source term IDs must be unique")
        for entry in self.entries:
            if (
                entry.source_term.ontology != self.source_ontology
                or entry.source_term.ontology_version != self.source_version
            ):
                raise ValueError(
                    "crosswalk entry source ontology/version does not match header"
                )
            if (
                entry.target_term.ontology
                != self.target_release.ontology
                or entry.target_term.ontology_version
                != self.target_release.version_iri
            ):
                raise ValueError(
                    "crosswalk entry target ontology/version does not match release"
                )


@dataclass(frozen=True, init=False)
class VerifiedAnatomyCrosswalk(JsonContract):
    """A crosswalk whose target assertions were checked against an asset."""

    crosswalk: AnatomyCrosswalk
    crosswalk_sha256: str
    ontology_asset_path: str

    def __init__(
        self,
        crosswalk: AnatomyCrosswalk,
        crosswalk_sha256: str,
        ontology_asset_path: str,
        *,
        _verification_token: object = None,
    ) -> None:
        if _verification_token is not _VERIFIED_TOKEN:
            raise ValueError(
                "VerifiedAnatomyCrosswalk must be created by "
                "verify_anatomy_crosswalk"
            )
        if not _SHA256_RE.fullmatch(crosswalk_sha256):
            raise ValueError("crosswalk_sha256 is invalid")
        if not ontology_asset_path:
            raise ValueError("verified ontology asset path is required")
        object.__setattr__(self, "crosswalk", crosswalk)
        object.__setattr__(self, "crosswalk_sha256", crosswalk_sha256)
        object.__setattr__(self, "ontology_asset_path", ontology_asset_path)

    @property
    def evidence_ref(self) -> str:
        return "anatomy-crosswalk:{}:sha256:{}".format(
            self.crosswalk.crosswalk_id,
            self.crosswalk_sha256,
        )

    def exact_equivalence_evidence(
        self, left: OntologyTerm, right: OntologyTerm
    ) -> Optional[str]:
        left_key = _term_key(left)
        right_key = _term_key(right)
        for entry in self.crosswalk.entries:
            if entry.predicate != "exactMatch":
                continue
            source_key, target_key = entry.term_pair()
            if (left_key, right_key) in (
                (source_key, target_key),
                (target_key, source_key),
            ):
                return self.evidence_ref
        return None


def _term_key(term: OntologyTerm) -> Tuple[str, str, str]:
    return (term.ontology, term.ontology_version, term.term_id)


def _term_from_dict(value: Mapping[str, object]) -> OntologyTerm:
    return OntologyTerm(
        ontology=str(value.get("ontology", "")),
        ontology_version=str(value.get("ontology_version", "")),
        term_id=str(value.get("term_id", "")),
        label=str(value.get("label", "")),
    )


def load_anatomy_crosswalk(path: str) -> AnatomyCrosswalk:
    value = yaml.safe_load(Path(path).read_text(encoding="utf-8")) or {}
    release_value = value.get("target_release") or {}
    if not isinstance(release_value, Mapping):
        raise ValueError("target_release must be an object")
    release = OntologyRelease(
        ontology=str(release_value.get("ontology", "")),
        release_tag=str(release_value.get("release_tag", "")),
        version_iri=str(release_value.get("version_iri", "")),
        data_version=str(release_value.get("data_version", "")),
        asset_url=str(release_value.get("asset_url", "")),
        asset_sha256=str(release_value.get("asset_sha256", "")),
        asset_size_bytes=int(release_value.get("asset_size_bytes", 0)),
        license=str(release_value.get("license", "")),
        license_url=str(release_value.get("license_url", "")),
    )
    entries: List[AnatomyCrosswalkEntry] = []
    raw_entries = value.get("entries") or []
    if not isinstance(raw_entries, list):
        raise ValueError("crosswalk entries must be an array")
    for raw in raw_entries:
        if not isinstance(raw, Mapping):
            raise ValueError("crosswalk entry must be an object")
        source = {
            "ontology": value.get("source_ontology"),
            "ontology_version": value.get("source_version"),
            "term_id": raw.get("source_term_id"),
            "label": raw.get("source_label"),
        }
        target = {
            "ontology": release.ontology,
            "ontology_version": release.version_iri,
            "term_id": raw.get("target_term_id"),
            "label": raw.get("target_label"),
        }
        entries.append(
            AnatomyCrosswalkEntry(
                source_term=_term_from_dict(source),
                target_term=_term_from_dict(target),
                predicate=str(raw.get("predicate", "")),
                match_basis=str(raw.get("match_basis", "")),
                matched_text=str(raw.get("matched_text", "")),
            )
        )
    return AnatomyCrosswalk(
        schema_version=str(value.get("schema_version", "")),
        crosswalk_id=str(value.get("crosswalk_id", "")),
        source_ontology=str(value.get("source_ontology", "")),
        source_version=str(value.get("source_version", "")),
        target_release=release,
        entries=tuple(entries),
    )


def _parse_obo(path: str) -> Tuple[Dict[str, str], Dict[str, Dict[str, object]]]:
    headers: Dict[str, str] = {}
    terms: Dict[str, Dict[str, object]] = {}
    current: Optional[Dict[str, object]] = None
    with open(path, "r", encoding="utf-8") as handle:
        for raw_line in handle:
            line = raw_line.rstrip("\n")
            if line == "[Term]":
                if current and current.get("id"):
                    terms[str(current["id"])] = current
                current = {"exact_synonyms": []}
                continue
            if line.startswith("[") and line.endswith("]"):
                if current and current.get("id"):
                    terms[str(current["id"])] = current
                current = None
                continue
            if not line or line.startswith("!"):
                continue
            if current is None:
                if ": " in line:
                    key, value = line.split(": ", 1)
                    headers.setdefault(key, value)
                continue
            if line.startswith("id: "):
                current["id"] = line[4:]
            elif line.startswith("name: "):
                current["name"] = line[6:]
            elif line == "is_obsolete: true":
                current["is_obsolete"] = True
            else:
                match = _EXACT_SYNONYM_RE.match(line)
                if match:
                    synonyms = current["exact_synonyms"]
                    if isinstance(synonyms, list):
                        synonyms.append(match.group(1))
    if current and current.get("id"):
        terms[str(current["id"])] = current
    return headers, terms


def verify_anatomy_crosswalk(
    crosswalk_path: str,
    ontology_asset_path: str,
    *,
    expected_crosswalk_sha256: Optional[str] = None,
) -> VerifiedAnatomyCrosswalk:
    """Verify a curated crosswalk against one immutable ontology release."""
    crosswalk_digest = sha256_file(crosswalk_path)
    if (
        expected_crosswalk_sha256 is not None
        and crosswalk_digest != expected_crosswalk_sha256
    ):
        raise ValueError(
            "anatomy crosswalk checksum mismatch: expected {}, observed {}".format(
                expected_crosswalk_sha256, crosswalk_digest
            )
        )
    crosswalk = load_anatomy_crosswalk(crosswalk_path)
    release = crosswalk.target_release
    observed_asset_sha256 = sha256_file(ontology_asset_path)
    if observed_asset_sha256 != release.asset_sha256:
        raise ValueError(
            "ontology asset checksum mismatch: expected {}, observed {}".format(
                release.asset_sha256, observed_asset_sha256
            )
        )
    observed_size = Path(ontology_asset_path).stat().st_size
    if observed_size != release.asset_size_bytes:
        raise ValueError(
            "ontology asset size mismatch: expected {}, observed {}".format(
                release.asset_size_bytes, observed_size
            )
        )
    headers, terms = _parse_obo(ontology_asset_path)
    if headers.get("data-version") != release.data_version:
        raise ValueError(
            "ontology data-version mismatch: expected {}, observed {}".format(
                release.data_version, headers.get("data-version")
            )
        )
    errors = []
    for entry in crosswalk.entries:
        target = terms.get(entry.target_term.term_id)
        if target is None:
            errors.append(
                "{} is absent from the ontology release".format(
                    entry.target_term.term_id
                )
            )
            continue
        if target.get("is_obsolete") is True:
            errors.append(
                "{} is obsolete".format(entry.target_term.term_id)
            )
        if target.get("name") != entry.target_term.label:
            errors.append(
                "{} label differs: {} != {}".format(
                    entry.target_term.term_id,
                    entry.target_term.label,
                    target.get("name"),
                )
            )
        if entry.match_basis == "preferred-label":
            accepted = target.get("name") == entry.matched_text
        else:
            accepted = entry.matched_text in target.get("exact_synonyms", [])
        if not accepted:
            errors.append(
                "{} does not support {} {!r}".format(
                    entry.target_term.term_id,
                    entry.match_basis,
                    entry.matched_text,
                )
            )
    if errors:
        raise ValueError("invalid anatomy crosswalk:\n- " + "\n- ".join(errors))
    return VerifiedAnatomyCrosswalk(
        crosswalk=crosswalk,
        crosswalk_sha256=crosswalk_digest,
        ontology_asset_path=str(Path(ontology_asset_path).resolve()),
        _verification_token=_VERIFIED_TOKEN,
    )


def exact_equivalence_evidence(
    left: OntologyTerm,
    right: OntologyTerm,
    crosswalks: Iterable[VerifiedAnatomyCrosswalk],
) -> Optional[str]:
    for crosswalk in crosswalks:
        evidence = crosswalk.exact_equivalence_evidence(left, right)
        if evidence is not None:
            return evidence
    return None
