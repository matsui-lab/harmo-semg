from pathlib import Path

import json
import pytest
import yaml

from harmo.full_record_benchmark import load_protocol


ROOT = Path(__file__).resolve().parents[1]


def test_committed_full_record_protocol_is_frozen():
    protocol = load_protocol(
        ROOT / "paper/full_record_materialization_protocol.yaml"
    )
    assert protocol["expected_record_count"] == 130
    assert protocol["expected_timepoint_count"] == 138638
    assert len(protocol["subjects"]) == 10
    assert len(protocol["channels"]) == 8
    assert protocol["selection"]["truncation"] == "none"
    assert protocol["selection"]["resampling"] == "none"


def test_protocol_rejects_duplicate_canonical_channels(tmp_path):
    protocol = yaml.safe_load(
        (ROOT / "paper/full_record_materialization_protocol.yaml").read_text()
    )
    protocol["channels"]["Soleus"] = "tibialis_anterior"
    path = tmp_path / "protocol.yaml"
    path.write_text(yaml.safe_dump(protocol), encoding="utf-8")
    with pytest.raises(ValueError, match="eight unique"):
        load_protocol(path)


def test_committed_full_record_result_passes_frozen_denominator():
    result = json.loads(
        (
            ROOT
            / "paper/results/validation/full_record_materialization_benchmark.json"
        ).read_text(encoding="utf-8")
    )
    summary = result["summary"]
    assert summary["pass"] is True
    assert summary["clean_code_runs"] == 2
    assert summary["subject_count"] == 10
    assert summary["record_count"] == 130
    assert summary["timepoint_count"] == 138638
    assert summary["signal_value_count"] == 1109104
    assert summary["channel_count_per_record"] == 8
    assert summary["comparison_count"] == 120
    assert summary["release_count"] == 130
    assert summary["conditional_release_count"] == 130
    assert summary["scientific_digest_identical"] is True
    assert summary["signal_and_record_tables_identical"] is True
    assert len(result["runs"]) == 2
    assert {run["code_commit"] for run in result["runs"]} == {
        "0a5aeb10fce2a3f790acc629a230635e8bcd8cd5"
    }
    assert all(run["code_dirty"] is False for run in result["runs"])
