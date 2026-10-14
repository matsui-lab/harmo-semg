import json
from pathlib import Path

import pytest
import yaml

from harmo.semantic_gold_standard import (
    build_annotation_template,
    build_blinded_packet,
    evaluate_gold_standard,
)

ROOT = Path(__file__).resolve().parents[1]


def _fixture(tmp_path):
    protocol = {
        "protocol_id": "test-gold",
        "denominator": {"authority_questions": 1},
        "annotation_instructions": ["blind"],
        "decision_rules": {
            "minimum_rater_exact_agreement": 0.8,
            "minimum_system_accuracy": 0.95,
        },
    }
    plan = {
        "sources": [
            {
                "source_id": "article",
                "study_id": "study",
                "source_role": "authority",
                "source_type": "peer-reviewed-article",
                "identifier": "doi:1",
                "format": "jats-xml",
                "claims": [
                    {
                        "claim_id": "claim",
                        "property_path": "recording.emg.sampling_rate_hz",
                    }
                ],
            }
        ]
    }
    report = {
        "claims": [
            {
                "claim_id": "claim",
                "source_id": "article",
                "source_role": "authority",
                "source_sha256": "a" * 64,
                "extraction_status": "extracted",
                "value": 2000,
                "scope_level": "channel-group",
            }
        ],
        "decisions": [{"claim_id": "claim", "machine_action": "enrich"}],
    }
    paths = {}
    for name, value, suffix in (
        ("protocol", protocol, ".yaml"),
        ("plan", plan, ".yaml"),
        ("report", report, ".json"),
    ):
        path = tmp_path / (name + suffix)
        if suffix == ".yaml":
            path.write_text(yaml.safe_dump(value), encoding="utf-8")
        else:
            path.write_text(json.dumps(value), encoding="utf-8")
        paths[name] = path
    packet = build_blinded_packet(
        protocol_path=str(paths["protocol"]),
        plan_path=str(paths["plan"]),
        report_path=str(paths["report"]),
    )
    packet_path = tmp_path / "packet.json"
    packet_path.write_text(json.dumps(packet), encoding="utf-8")
    return paths, packet, packet_path


def _annotations(path, annotation_id, annotator):
    path.write_text(
        json.dumps(
            {
                "attestation": {
                    "annotator_id": annotator,
                    "involved_in_extractor_development": False,
                },
                "annotations": [
                    {
                        "annotation_id": annotation_id,
                        "support": "supported",
                        "value": 2000,
                        "scope_level": "channel-group",
                        "machine_action": "enrich",
                    }
                ],
            }
        ),
        encoding="utf-8",
    )


def test_packet_hides_system_answer_and_regex(tmp_path):
    _, packet, _ = _fixture(tmp_path)
    assert len(packet["rows"]) == 1
    assert "claim_id" not in packet["rows"][0]
    assert "expected" not in packet["rows"][0]
    assert "system_decision" not in packet["rows"][0]


def test_annotation_template_is_complete_and_still_blinded(tmp_path):
    _, packet, packet_path = _fixture(tmp_path)
    template = build_annotation_template(
        packet_path=str(packet_path),
        annotator_id="rater-a",
    )
    assert template["attestation"] == {
        "annotator_id": "rater-a",
        "involved_in_extractor_development": False,
    }
    assert len(template["annotations"]) == len(packet["rows"])
    row = template["annotations"][0]
    assert row["support"] is None
    assert row["machine_action"] is None
    assert "claim_id" not in row
    assert "regex" not in row
    assert "system_extracted_value" not in row


def test_two_independent_matching_annotations_pass(tmp_path):
    paths, packet, packet_path = _fixture(tmp_path)
    annotation_id = packet["rows"][0]["annotation_id"]
    left = tmp_path / "left.json"
    right = tmp_path / "right.json"
    _annotations(left, annotation_id, "rater-a")
    _annotations(right, annotation_id, "rater-b")
    result = evaluate_gold_standard(
        protocol_path=str(paths["protocol"]),
        packet_path=str(packet_path),
        report_path=str(paths["report"]),
        annotation_paths=[str(left), str(right)],
    )
    assert result["summary"]["pass"] is True
    assert result["summary"]["system_value_accuracy_supported"] == 1.0


def test_non_independent_or_unadjudicated_annotations_fail(tmp_path):
    paths, packet, packet_path = _fixture(tmp_path)
    annotation_id = packet["rows"][0]["annotation_id"]
    left = tmp_path / "left.json"
    right = tmp_path / "right.json"
    _annotations(left, annotation_id, "rater-a")
    _annotations(right, annotation_id, "rater-b")
    value = json.loads(right.read_text())
    value["annotations"][0]["value"] = 1000
    right.write_text(json.dumps(value), encoding="utf-8")
    with pytest.raises(ValueError, match="adjudication"):
        evaluate_gold_standard(
            protocol_path=str(paths["protocol"]),
            packet_path=str(packet_path),
            report_path=str(paths["report"]),
            annotation_paths=[str(left), str(right)],
        )


def test_committed_packet_is_frozen_and_blinded():
    packet = json.loads(
        (ROOT / "paper/semantic_gold_standard_packet.json").read_text(
            encoding="utf-8"
        )
    )
    assert packet["protocol_id"] == "g9-independent-semantic-gold-standard"
    assert len(packet["rows"]) == 43
    assert len({row["annotation_id"] for row in packet["rows"]}) == 43
    assert packet["packet_sha256"] == (
        "b1bc4cd0de8c464733bac67c92f2331b03f4cef51025e014308abedf00f54d0b"
    )
    hidden = set(packet["blinding"]["hidden_fields"])
    assert {
        "claim_id",
        "regex",
        "expected_value",
        "system_extracted_value",
        "system_decision",
    } <= hidden
    for row in packet["rows"]:
        assert not hidden.intersection(row)
