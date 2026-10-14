"""Evidence and scope checks for the ggplot2 Figure 2 candidate."""
import csv
import hashlib
import json
from collections import Counter
from pathlib import Path
from xml.etree import ElementTree
from zipfile import ZipFile


ROOT = Path(__file__).resolve().parents[1]
VERSION = (ROOT / "paper/figures/candidates/FIGURE2_VERSION").read_text().strip()
STEM = f"figure2_three_panel_{VERSION}"
OUTPUT = ROOT / "paper/figures/candidates" / STEM


def rows(path):
    with path.open(newline="") as stream:
        return list(csv.DictReader(stream))


def test_candidate_sources_are_unchanged_and_graphics_use_ggplot2():
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


def test_format_matrix_counts_actual_selected_members():
    mappings = rows(ROOT / "paper/results/validation/mapping_outcomes.csv")
    expected = Counter((r["study_id"], Path(r["logical_source_member"]).suffix[1:].lower()) for r in mappings)
    coverage = rows(OUTPUT / f"{STEM}_A_format_coverage.csv")
    assert len(coverage) == 48
    assert sum(int(r["n"]) for r in coverage) == 78
    for row in coverage:
        n = expected[(row["study_id"], row["format"])]
        assert int(row["n"]) == n
        assert (row["tested"] == "TRUE") == (n > 0)
    totals = rows(OUTPUT / f"{STEM}_A_study_totals.csv")
    for row in totals:
        n = sum(r["study_id"] == row["study_id"] for r in mappings)
        assert int(row["registered"]) == int(row["converted"]) == n


def test_reproduction_and_missingness_keep_separate_denominators():
    runs = rows(OUTPUT / f"{STEM}_B_run_concordance.csv")
    assert len(runs) == 6
    assert sorted(Counter(r["environment"] for r in runs).values()) == [3, 3]
    assert all(r["converted"] == r["registered"] == "78" for r in runs)
    assert all(r["source_identity_match"] == r["record_values_match"] == "TRUE" for r in runs)
    retained = rows(OUTPUT / f"{STEM}_C_retention.csv")
    assert {r["measure"]: int(r["input"]) for r in retained} == {
        "Participants": 13, "Trials": 208, "Missing cells": 11528,
    }
    assert all(r["input"] == r["retained"] for r in retained)
    modalities = rows(OUTPUT / f"{STEM}_C_missingness_by_modality.csv")
    assert sum(int(r["source_missing"]) for r in modalities) == 11528
    assert all(r["source_missing"] == r["retained_missing"] for r in modalities)


def test_powerpoint_text_is_native_and_matches_ggplot2():
    svg = ElementTree.parse(OUTPUT / f"{STEM}.svg")
    source_text = [node.text for node in svg.iter("{http://www.w3.org/2000/svg}text")]
    with ZipFile(ROOT / "paper/figures/editable" / f"{STEM}_editable.pptx") as package:
        slide = ElementTree.fromstring(package.read("ppt/slides/slide1.xml"))
        native_text = [node.text for node in slide.iter("{http://schemas.openxmlformats.org/drawingml/2006/main}t")]
        assert native_text == source_text
        assert len(native_text) == 83
        assert not list(slide.iter("{http://schemas.openxmlformats.org/presentationml/2006/main}pic"))
