"""Bounded, deterministic source screening; never a scientific approval.

Only prose paragraphs are screened. Matches are source-reported candidates,
not verified channel mappings. Missing matches do not prove missing metadata.
"""
import hashlib
import re
from html.parser import HTMLParser
from pathlib import Path

VERSION = "source-screen/0.3.0"

# Explicit lexical conventions, not ontology equivalence or validated anatomy.
ANATOMY = {
    "tibialis anterior": ("muscle", ("TA", "TIB.A")),
    "gastrocnemius lateralis": ("muscle", ("GL", "LAT.G")),
    "rectus femoris": ("muscle", ("RF", "REC.F")),
    "vastus lateralis": ("muscle", ("VL", "LAT.V")),
    "hamstrings": ("muscle-group", ("HAM",)),
}

from harmo.lower_limb_terms import FULL_NAMES
for _name in FULL_NAMES:
    # No new short aliases are inferred. A full name still needs record scope.
    ANATOMY.setdefault(_name, ("muscle", ()))


class Paragraphs(HTMLParser):
    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.blocks = []
        self.parts = None
        self.ignored = 0

    def handle_starttag(self, tag, attrs):
        if tag in ("script", "style"):
            self.ignored += 1
        if tag == "p" and not self.ignored:
            self.parts = []

    def handle_endtag(self, tag):
        if tag in ("script", "style"):
            self.ignored = max(0, self.ignored - 1)
        if tag == "p" and self.parts is not None:
            text = re.sub(r"\s+", " ", "".join(self.parts)).strip()
            if text:
                self.blocks.append(text)
            self.parts = None

    def handle_data(self, data):
        if self.parts is not None and not self.ignored:
            self.parts.append(data)


def voltage_unit(value):
    return {"v": "V", "mv": "mV", "uv": "uV"}.get(
        value.strip().replace("μ", "u").replace("µ", "u").lower())


def extract_description(path, uri):
    raw = Path(path).read_bytes()
    parser = Paragraphs()
    parser.feed(raw.decode("utf-8"))
    digest = hashlib.sha256(raw).hexdigest()
    claims = []
    # Deliberately small public rule set, not a general natural-language parser.
    rules = (
        ("sensor", "needle", r"\bneedle electrode\b"),
        ("sensor", "surface", r"\bsurface electromyographic\b|\bsurface electrodes?\b"),
        ("task", "ground-walking", r"\b(?:ground walking|walk barefoot on level ground)\b"),
        ("task", "resisted-dorsiflexion", r"\bdorsiflex the foot gently against resistance\b"),
        ("synchronization", "source-reported", r"\b(?:signals|data) are synchronized\b"),
    )
    for index, paragraph in enumerate(parser.blocks):
        for name, (entity_type, aliases) in ANATOMY.items():
            for match in re.finditer(r"\b" + re.escape(name) + r"\b", paragraph, re.I):
                claims.append(dict(field="anatomy", value=name, entity_type=entity_type,
                                   paragraph=index, start=match.start(), end=match.end(),
                                   quote=paragraph, status="candidate", scope="dataset-description"))
        # Keep whole source text even for tentative/negative statements. Such
        # matches must not be promoted to assertions by downstream code.
        for field, value, pattern in rules:
            for match in re.finditer(pattern, paragraph, flags=re.I):
                claims.append(dict(field=field, value=value, paragraph=index,
                                   start=match.start(), end=match.end(), quote=paragraph,
                                   status="candidate", scope="dataset-description"))
        for match in re.finditer(r"\bs?EMG signals are expressed in\s+([μµu]V|mV|V)\b", paragraph, re.I):
            claims.append(dict(field="emg_unit", value=voltage_unit(match[1]), paragraph=index,
                               start=match.start(), end=match.end(), quote=paragraph,
                               status="candidate", scope="dataset-description"))
    for claim in claims:
        claim["source_ref"] = uri + ":sha256:" + digest
    return dict(version=VERSION, source_uri=uri, source_sha256=digest,
                source_path=str(Path(path).resolve()), claims=claims,
                coverage=dict(repository_description="screened_by_bounded_rules",
                              related_papers="not_examined", record_scope="not_verified",
                              anatomy="bounded_lexical_candidates", processing="not_implemented"),
                limitation="Candidates require scope and assertion review; no match is not evidence of absence.")


def map_channels(meta, evidence):
    """Join label tokens to source mentions, retaining ambiguity and scope gaps.

    No dataset identity or channel order is used to guess a muscle. In
    particular HAM is a group, never silently expanded into one muscle.
    """
    mapped = []
    for index, signal in enumerate(meta["signals"]):
        label = signal["label"]
        tokens = label.upper().split()
        if not any(token in ("EMG", "SEMG") for token in tokens):
            mapped.append(dict(channel_index=index, source_label=label,
                               status="not_resolved", reasons=["signal_type_not_recognized"]))
            continue
        candidates = []
        for name, (entity_type, aliases) in ANATOMY.items():
            full_name = re.search(r"\b" + re.escape(name) + r"\b", label, re.I)
            hits = [alias for alias in aliases if alias in tokens]
            if not full_name and not hits:
                continue
            refs = [c for c in evidence["claims"] if c["field"] == "anatomy" and c["value"] == name]
            candidates.append(dict(name=name, entity_type=entity_type,
                                   label_rule="full-name" if full_name else "lexical-alias/0.1.0",
                                   matched_tokens=hits, source_mentions=refs,
                                   source_support="mentioned" if refs else "not_found_by_rules"))
        sides = set()
        for word in tokens:
            if word in ("LT", "LEFT"):
                sides.add("left")
            if word in ("RT", "RIGHT"):
                sides.add("right")
        reasons = ["record_scope_not_verified", "label_conventions_not_independently_verified"]
        if len(candidates) != 1:
            reasons.append("anatomy_ambiguous" if candidates else "anatomy_not_resolved")
        elif not candidates[0]["source_mentions"]:
            reasons.append("anatomy_source_support_not_found_by_rules")
        elif candidates[0]["entity_type"] == "muscle-group":
            reasons.append("group_not_single_muscle")
        if len(sides) != 1:
            reasons.append("laterality_ambiguous" if sides else "laterality_not_resolved")
        conditions = [c for c in evidence["claims"] if c["field"] in ("sensor", "task", "synchronization")]
        mapped.append(dict(channel_index=index, source_label=label, status="candidate",
                           anatomy_candidates=candidates, side_candidates=sorted(sides),
                           dataset_condition_candidates=conditions,
                           header_sources=meta.get("source_sha256", {}), reasons=reasons))
    return mapped


def assess_record(meta, evidence):
    """Screen prerequisites separately from existing pairwise comparability."""
    units = {c["value"] for c in evidence["claims"] if c["field"] == "emg_unit"}
    issues = []
    for signal in meta["signals"]:
        if "emg" not in signal["label"].lower():
            continue
        unit = voltage_unit(signal["unit"])
        if units and (len(units) != 1 or unit not in units):
            issues.append(dict(code="possible_source_unit_conflict", channel=signal["label"],
                               header_unit=signal["unit"], description_units=sorted(units),
                               action="Verify statement scope before conversion; original values retained."))
    common = ["channel_anatomy_and_side_not_verified", "task_scope_not_verified",
              "sensor_scope_not_verified", "processing_not_verified"]
    purposes = {}
    for purpose in ("activation-timing", "amplitude-pooling"):
        reasons = common + (["event_reference_not_verified"] if purpose == "activation-timing"
                            else ["amplitude_normalization_not_verified"])
        if issues:
            reasons = reasons + ["possible_source_unit_conflict"]
        purposes[purpose] = dict(status="unassessed", reasons=reasons,
                                 scope="record-prerequisite-screen-not-pairwise-approval")
    return dict(version=VERSION, source_sha256=evidence["source_sha256"],
                channel_mappings=map_channels(meta, evidence),
                coverage=evidence["coverage"], issues=issues, purposes=purposes,
                automatic_signal_changes=[])


def screen_saved_stage(stage, destination):
    """Re-screen saved sources offline into a new directory, preserving originals."""
    import json
    stage, destination = Path(stage), Path(destination)
    destination.mkdir(parents=True, exist_ok=False)
    dataset = stage.parent.name + "/" + stage.name
    evidence = extract_description(stage / "description.html", "https://physionet.org/content/" + dataset + "/")
    (destination / "source.evidence.json").write_text(json.dumps(evidence, indent=2))
    results = []
    for path in sorted((stage / "converted").glob("*/conversion.json")):
        meta = json.loads(path.read_text())
        record_path = path.parent / "record.json"
        if record_path.exists():
            record = json.loads(record_path.read_text())
            meta["source_sha256"] = record.get("metadata", {}).get("source_sha256", {})
        result = assess_record(meta, evidence)
        result["record_path"] = str(path.parent.resolve())
        results.append(result)
    (destination / "record.screens.json").write_text(json.dumps(results, indent=2))
    return results


if __name__ == "__main__":
    import argparse
    parser = argparse.ArgumentParser(description="Screen saved PhysioNet source evidence without redownloading")
    parser.add_argument("stage")
    parser.add_argument("destination")
    args = parser.parse_args()
    screen_saved_stage(args.stage, args.destination)
