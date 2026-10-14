"""Attribute-level promotion from explicit, record-bound source definitions.

Verified means verified against the supplied declaration, not independently
verified experimental truth. Lexical candidates never enter this interface.
"""
import copy
import hashlib
import json
from pathlib import Path

from harmo.evidence.models import (HarmoRecordingBundle, AnatomicalEntity,
    OntologyTerm, SemanticMapping, TaskDescriptor)
from harmo.source_evidence import voltage_unit
from harmo.schema_resources import schema_path

VERSION = "semantic-promotion/0.1.0"
FORMAT = "harmo-record-definitions/1.0"
FIELDS = {"anatomy", "side", "sensor_type", "unit", "task"}


def checked(value, definition):
    from jsonschema import Draft202012Validator
    path = schema_path("recording-bundle-1.2.0.json")
    schema = json.loads(path.read_text())
    errors = list(Draft202012Validator({"$defs":schema["$defs"], "$ref":"#/$defs/"+definition}).iter_errors(value.to_dict()))
    if errors:
        raise ValueError(errors[0].message)
    return value


def digest(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def read_definitions(path, uri):
    """Explicit JSON adapter. No free-text regex is an authoritative definition."""
    data = json.loads(Path(path).read_text())
    if set(data) != {"format", "dataset", "definitions"} or data["format"] != FORMAT:
        raise ValueError("unsupported definition document")
    if not isinstance(data["dataset"], str) or not isinstance(data["definitions"], list):
        raise ValueError("dataset string and definitions array required")
    for row in data["definitions"]:
        if set(row) != {"record", "header_sha256", "channel", "field", "value"}:
            raise ValueError("each definition requires an exact record, header hash, channel, field and value")
        if row["field"] not in FIELDS or not isinstance(row["record"], str) or not row["record"]:
            raise ValueError("invalid definition target")
        if not isinstance(row["channel"], str) or not isinstance(row["header_sha256"], str):
            raise ValueError("channel and header hash must be strings")
        if row["field"] == "task" and row["channel"] != "":
            raise ValueError("task is record-level; channel must be empty")
        if row["field"] != "task" and not row["channel"]:
            raise ValueError("channel-level fields require a channel")
    return dict(data=data, uri=uri, sha256=digest(path))


def promote(bundle, record_name, header_sha256, documents, screen=None):
    if bundle.task is not None or any(c.anatomy is not None or c.muscle not in (None, "unknown") or c.side not in (None, "unknown") for c in bundle.channels):
        raise ValueError("Reassess from the original unmapped bundle, not an existing semantic view")
    result = HarmoRecordingBundle.from_dict(copy.deepcopy(bundle.to_dict()))
    decisions, ignored = [], []
    selected = {}
    names = {c.name for c in bundle.channels}
    for document in documents:
        if document["data"]["dataset"] != bundle.study_id:
            ignored.append(dict(source=document["uri"], reason="different_dataset_release"))
            continue
        for index, row in enumerate(document["data"]["definitions"]):
            if row["record"] != record_name:
                continue
            ref = dict(uri=document["uri"], sha256=document["sha256"], pointer="/definitions/"+str(index))
            if index in document.get("origins", {}):
                ref.update(document["origins"][index])
            if row["header_sha256"] != header_sha256:
                ignored.append(dict(source=ref, reason="record_header_hash_mismatch"))
                continue
            if row["channel"] and row["channel"] not in names:
                ignored.append(dict(source=ref, reason="channel_not_in_record"))
                continue
            selected.setdefault((row["channel"], row["field"]), []).append((row["value"], ref))

    def resolve(channel, field):
        entries = selected.get((channel, field), [])
        decision = dict(channel=channel, field=field, status="unresolved", sources=[r for _, r in entries])
        decisions.append(decision)
        if not entries:
            decision["reason"] = "no_applicable_explicit_definition"
            return None, decision
        values = {json.dumps(v, sort_keys=True) for v, _ in entries}
        if len(values) != 1:
            decision.update(status="conflicting", reason="applicable_definitions_disagree")
            return None, decision
        decision["value"] = entries[0][0]
        return entries[0][0], decision

    def mapping(decision):
        refs = [s["uri"] + "#" + s["pointer"] + ":sha256:" + s["sha256"] for s in decision["sources"]]
        return SemanticMapping("explicit-record-definition", VERSION, "mapped", refs)

    task, td = resolve("", "task")
    if task is not None:
        try:
            if set(task) != {"activity", "ontology_terms", "facets", "compatibility_group"}:
                raise ValueError("task requires activity, ontology_terms, facets and compatibility_group")
            result.task = checked(TaskDescriptor(task["activity"], task["activity"],
                [OntologyTerm(**term) for term in task["ontology_terms"]], task["facets"], mapping(td), task["compatibility_group"]), "task")
            td["status"] = "source_verified"
        except (ValueError, TypeError, KeyError) as exc:
            td.update(status="invalid", reason=str(exc))
    for channel in result.channels:
        side, sd = resolve(channel.name, "side")
        if side is not None:
            if side in ("left", "right", "midline"):
                channel.side = side
                sd["status"] = "source_verified"
            else:
                sd.update(status="invalid", reason="unsupported_laterality")
        anatomy, ad = resolve(channel.name, "anatomy")
        if anatomy is not None:
            try:
                if set(anatomy) != {"entity_type", "canonical_label", "ontology_term"}:
                    raise ValueError("anatomy requires entity_type, canonical_label, ontology_term")
                channel.anatomy = checked(AnatomicalEntity(anatomy["entity_type"], anatomy["canonical_label"],
                    OntologyTerm(**anatomy["ontology_term"]), channel.side or "unknown", channel.name, mapping(ad)), "anatomicalEntity")
                channel.muscle = anatomy["canonical_label"] if anatomy["entity_type"] == "muscle" else None
                ad["status"] = "source_verified"
            except (ValueError, TypeError, KeyError) as exc:
                ad.update(status="invalid", reason=str(exc))
        sensor, se = resolve(channel.name, "sensor_type")
        if sensor is not None:
            if sensor in ("surface-emg", "needle-emg", "footswitch", "electrogoniometer"):
                channel.sensor_type = sensor
                se["status"] = "source_verified"
            else:
                se.update(status="invalid", reason="unsupported_sensor_type")
        unit, ud = resolve(channel.name, "unit")
        if unit is not None:
            if not isinstance(unit, str):
                ud.update(status="invalid", reason="unit_must_be_string")
            elif unit == channel.unit or (voltage_unit(unit) is not None and voltage_unit(unit) == voltage_unit(channel.unit)):
                ud["status"] = "source_verified"
            else:
                ud.update(status="conflicting", reason="definition_disagrees_with_header_unit")
        if any(i.get("channel") == channel.name for i in (screen or {}).get("issues", [])):
            ud.update(status="conflicting", reason="unresolved_description_header_unit_conflict")
    if result.task is not None or any(c.anatomy is not None for c in result.channels):
        result.schema_version = "1.2.0"
    result = HarmoRecordingBundle.from_dict(result.to_dict())
    if result.schema_version == "1.2.0":
        from jsonschema import validate
        schema = schema_path("recording-bundle-1.2.0.json")
        validate(result.to_dict(), json.loads(schema.read_text()))
    report = dict(version=VERSION, input_recording_id=bundle.recording_id, header_sha256=header_sha256,
                  decisions=decisions, ignored=ignored,
                  verification_scope="Exact declared record/version/channel, schema and consistency; not independent experimental verification.",
                  scientific_use="unassessed", signal_changes=[])
    return result, report


def promote_directory(directory, record_name, dataset_root, base_url):
    """Discover opted-in definition files among downloaded assets; preserve originals."""
    directory, dataset_root = Path(directory), Path(dataset_root)
    bundle = HarmoRecordingBundle.from_dict(json.loads((directory/"recording.bundle.json").read_text()))
    documents, errors = [], []
    description = dataset_root.parent / "description.html"
    if description.exists():
        from harmo.html_definitions import extract_html_definitions
        documents.append(extract_html_definitions(description,
            "https://physionet.org/content/"+bundle.study_id+"/", bundle, record_name, digest(dataset_root/record_name)))
    for path in sorted(dataset_root.rglob("*.record-definitions.json")):
        try:
            documents.append(read_definitions(path, base_url + path.relative_to(dataset_root).as_posix()))
        except (ValueError, TypeError, KeyError) as exc:
            errors.append(dict(path=str(path), error=str(exc)))
    screen_path = directory/"source.screen.json"
    screen = json.loads(screen_path.read_text()) if screen_path.exists() else None
    result, report = promote(bundle, record_name, digest(dataset_root/record_name), documents, screen)
    report["invalid_documents"] = errors
    if errors:
        # An unreadable declaration could contradict others: do not promote.
        result = bundle
        for decision in report["decisions"]:
            if decision["status"] == "source_verified":
                decision.update(status="unresolved", reason="invalid_definition_document_present")
    for name, value in (("recording.semantic.bundle.json", result.to_dict()), ("semantic.promotion.json", report)):
        with (directory/name).open("x") as f:
            json.dump(value, f, indent=2, allow_nan=False)
    return report
