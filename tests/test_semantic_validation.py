from pathlib import Path

from harmo.evidence.io import read_json
from harmo.semantic_validation import run_public_semantic_validation


ROOT = Path(__file__).resolve().parents[1]


def test_public_semantic_validation_uses_real_study_dictionaries():
    result = run_public_semantic_validation(str(ROOT))

    assert result["summary"] == {
        "positive_alias_controls_passed": 2,
        "positive_alias_controls_total": 2,
        "negative_controls_passed": 3,
        "negative_controls_total": 3,
        "pass": True,
    }
    aliases = {
        item["muscle"]: (
            item["left_source_label"],
            item["right_source_label"],
        )
        for item in result["positive_alias_controls"]
    }
    assert aliases["tibialis_anterior"] == (
        "RX_Tibialis Anterior",
        "TibialisAnterior",
    )
    assert aliases["gastrocnemius_medialis"] == (
        "RX_Gastrocnemius Medialis",
        "GastrocnemuisMedialis",
    )
    assert all(
        item["status"] == "conditionally_comparable"
        for item in result["positive_alias_controls"]
    )
    assert {
        item["control"]: item["observed"]
        for item in result["negative_controls"]
    } == {
        "level-versus-stairs": "not_comparable",
        "missing-task": "unassessed",
        "different-muscle": "not_comparable",
    }


def test_frozen_semantic_validation_matches_current_definitions():
    frozen = read_json(
        str(
            ROOT
            / "paper/results/validation/"
            "task_anatomy_semantic_validation.json"
        )
    )
    reproduced = run_public_semantic_validation(str(ROOT))

    assert frozen["implementation_commit"] == (
        "712cdfd287ef6145a09f914d431c858ca7f749a5"
    )
    assert frozen["code_dirty"] is False
    for value in (frozen, reproduced):
        value.pop("implementation_commit")
        value.pop("code_dirty")
    assert reproduced == frozen
