import importlib.util
from pathlib import Path

import pytest
import yaml

SPEC = importlib.util.spec_from_file_location("source_review", Path(__file__).parents[1] / "scripts/paper/evaluate_scientific_reuse_sources_v001.py")
M = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(M)


def review():
    return yaml.safe_load((M.ROOT / M.REVIEW).read_text())


def test_article_cannot_supply_file_repository_baseline():
    data = review()
    data["rows"][0]["P0_sources"].append("Larticle")
    with pytest.raises(ValueError, match="leaked"):
        M.validate_review(data)


def test_partial_never_counts_as_confirmed():
    data = review()
    row = next(r for r in data["rows"] if r["study"] == "Gait120" and r["attribute"] == "cycle_event")
    row["P1"] = "partial"
    result = next(x for x in M.gates(data) if x["level"] == "P1" and x["use"] == "relative_gait_timing")
    assert not result["requirements_met"]
    assert result["unresolved"] == [{"study": "Gait120", "attribute": "cycle_event", "state": "partial"}]


def test_units_alone_do_not_enable_amplitude_comparison():
    results = M.gates(review())
    lookup = {(r["level"], r["use"]): r for r in results}
    assert lookup[("P1", "voltage_conversion")]["requirements_met"]
    assert not lookup[("P1", "amplitude_comparison")]["requirements_met"]
    assert all(not r["automatic_release_granted"] and not r["biological_validity_established"] for r in results)


def test_duplicate_or_missing_requirements_fail_instead_of_silent_drop():
    data = review()
    data["rows"].append(data["rows"][0])
    with pytest.raises(ValueError, match="duplicate"):
        M.gates(data)
    data = review()
    data["uses"]["relative_gait_timing"]["requires"].append("unknown_attribute")
    with pytest.raises(KeyError):
        M.gates(data)


def test_repository_right_side_is_not_misattributed_to_article_gain():
    data = review()
    rows = M.validate_review(data)
    assert rows[("Gait120", "recording_side")]["P0"] == "confirmed"
    assert rows[("Lencioni", "recording_side")]["P0"] == "confirmed"


def test_all_selected_source_anchors_match_real_documents():
    data = review()
    for item in data["source_checks"]:
        content = M.source_text(M.ROOT, data["sources"][item["source"]], item.get("section"))
        assert M.normalized(item["contains"]) in content
