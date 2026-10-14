"""Evidence and denominator checks for the reconstructed Figure 3 candidate."""
import csv
import hashlib
import json
from collections import Counter
from pathlib import Path
from xml.etree import ElementTree


ROOT = Path(__file__).resolve().parents[1]
VERSION = (ROOT / "paper/figures/candidates/FIGURE3_VERSION").read_text().strip()
STEM = f"figure3_three_panel_{VERSION}"
OUTPUT = ROOT / "paper/figures/candidates" / STEM


def rows(suffix):
    with (OUTPUT / f"{STEM}_{suffix}.csv").open(newline="") as stream:
        return list(csv.DictReader(stream))


def test_figure3_is_a_vector_candidate_with_unchanged_evidence():
    manifest = json.loads((OUTPUT / f"{STEM}_evidence.json").read_text())
    assert manifest["status"] == "candidate_not_adopted"
    assert manifest["graphic_engine"].startswith("ggplot2 ")
    assert manifest["minimum_text_pt"] >= 7.5
    for source in manifest["sources"]:
        assert hashlib.sha256((ROOT / source["path"]).read_bytes()).hexdigest() == source["sha256"]
    assert (OUTPUT / f"{STEM}.pdf").read_bytes().startswith(b"%PDF-")
    svg = (OUTPUT / f"{STEM}.svg").read_text()
    assert "<image" not in svg
    assert "<text" in svg and "<circle" in svg


def test_coverage_is_counted_from_registered_property_sets():
    evidence = json.loads((ROOT / "paper/results/validation/semantic_coverage_comparison.json").read_text())
    expected = {r["study_id"]: r for r in evidence["study_rows"]}
    data = rows("A_coverage")
    assert len(data) == 8
    for row in data:
        source = expected[row["study_id"]]
        assert int(row["file_only"]) == len(set(source["file_only_properties"]))
        assert int(row["paper_assisted"]) == len(set(source["paper_assisted_properties"]))
        assert int(row["added"]) == len(set(source["paper_assisted_properties"]) - set(source["file_only_properties"]))
    assert sum(int(r["file_only"]) for r in data) == 24
    assert sum(int(r["paper_assisted"]) for r in data) == 43


def test_controls_distinguish_equivalence_from_retained_absence():
    evidence = json.loads((ROOT / "paper/results/validation/format_fidelity.json").read_text())
    source_counts = Counter((r["candidate_format"] == "mat", r["observed_outcome"]) for r in evidence["comparisons"])
    data = rows("B_outcomes")
    for row in data:
        assert int(row["n"]) == source_counts[(row["group"] == "ASCII vs MAT", row["outcome"])]
    assert source_counts[(True, "equivalent")] == 30
    assert source_counts[(True, "source-absent-retained")] == 2
    controls = [r for r in evidence["comparisons"] if r["candidate_format"] == "mat"]
    assert len(controls) == 32 and all(r["pass"] for r in controls)
    caption = (OUTPUT / f"{STEM}_caption.md").read_text()
    assert "not be described as 32" in caption
    assert "not independent participant observations" in caption


def test_channel_instances_and_comparison_units_remain_separate():
    records = rows("C_channel_instances")
    uses = rows("C_use_conditions")
    assert len(records) == len({r["recording_id"] for r in records}) == 12
    assert sum(int(r["source"]) for r in records) == 160
    assert sum(int(r["supported"]) for r in records) == 143
    assert sum(int(r["excluded"]) for r in records) == 17
    assert all(int(r["source"]) == int(r["supported"]) + int(r["excluded"]) for r in records)
    assert all(r["sampled_timepoints"] == "100" for r in records)
    assert len(uses) == 6
    assert all(r["status"] == "conditionally_comparable" for r in uses)
    assert all("full recording before scientific inference" in r["conditions"] for r in uses)
    assert sum(int(r["conditional_releases"]) for r in uses) == 12


def test_pastel_revision_preserves_text_geometry_and_evidence_values():
    previous_stem = "figure3_three_panel_v001_2026.09.09-r1"
    previous = ROOT / "paper/figures/candidates" / previous_stem
    old = ElementTree.parse(previous / f"{previous_stem}.svg")
    new = ElementTree.parse(OUTPUT / f"{STEM}.svg")
    ns = "{http://www.w3.org/2000/svg}"
    assert [(r.text, r.get("x"), r.get("y")) for r in old.iter(ns + "text")] == [
        (r.text, r.get("x"), r.get("y")) for r in new.iter(ns + "text")]
    for path in previous.glob("*.csv"):
        suffix = path.name[len(previous_stem):]
        assert path.read_bytes() == (OUTPUT / f"{STEM}{suffix}").read_bytes()
    assert (previous / f"{previous_stem}_caption.md").read_bytes() == (OUTPUT / f"{STEM}_caption.md").read_bytes()
    palette = json.loads((OUTPUT / f"{STEM}_evidence.json").read_text())["palette"]
    assert palette["supported"] == "#A8CADB"
    assert palette["restricted"] == "#EDCAA5"
    assert palette["absent"] == "#D5D9DF"
