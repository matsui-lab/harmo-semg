"""Reader-facing names must not change evidence, participant IDs or results."""
from pathlib import Path
import csv
import hashlib
import json
import re
import zipfile
import xml.etree.ElementTree as ET

ROOT = Path(__file__).resolve().parents[1]
PAPER = ROOT / "paper"
F2 = "figure2_implementation_gui_v018_2026.09.26-r1"
F3 = "figure3_integrated_results_v009_2026.09.26-r1"
OLD3 = "figure3_integrated_results_v008_2026.09.16-r8"
JA = PAPER / "japanese/v004_2026.09.26-r3"
NAMES = json.loads((PAPER / "dataset_display_names_v001_2026.09.26-r3.json").read_text())["datasets"]

def read(path):
    return path.read_text()

def test_no_internal_study_numbers_in_current_prose():
    paths = [PAPER / "paper.md", *PAPER.glob("supplementary_*_v029*.md"),
             PAPER / "figures/README.md", *JA.glob("*.md")]
    for path in paths:
        assert not re.search(r"\bStud(?:y|ies)\s+\d", read(path)), path
        assert not re.search(r"\bstudy_?\d+\b", read(path)), path

def test_public_name_and_internal_id_mapping():
    rows = list(csv.DictReader((PAPER / "tables/table1_public_studies.csv").open()))
    assert {r["study_id"] for r in rows} == {r["internal_id"] for r in NAMES}
    assert next(r for r in NAMES if r["internal_id"] == "study_120")["public_label"] == "Gait120"
    for row in NAMES:
        for table in (PAPER / "tables/table_s1_public_studies.md", JA / "tables/table_s1.md"):
            assert row["figure_label"] in read(table)
            assert row["internal_id"] in read(table)

def test_figure3_scientific_data_unchanged():
    parent = PAPER / "figures/candidates"
    for suffix in ("_A_data.csv", "_B_data.csv", "_C_data.csv", "_D_data.csv",
                   "_E_data.csv", "_E_median_differences.csv", "_D_table.json"):
        assert (parent / OLD3 / (OLD3 + suffix)).read_bytes() == (parent / F3 / (F3 + suffix)).read_bytes()

def test_original_figure2_input_values_unchanged():
    old = "figure2_implementation_gui_v016_2026.09.16-r5"
    base = PAPER / "figures/candidates"
    old_evidence = json.loads(read(base / old / (old + "_evidence.json")))
    evidence = json.loads(read(base / F2 / (F2 + "_evidence.json")))
    assert old_evidence["output_table_values"][1:] == evidence["output_table_values"][1:]
    for row in old_evidence["sources"]:
        if row["path"].endswith(("waveforms.csv", "extracted_case.json")):
            assert hashlib.sha256((ROOT / row["path"]).read_bytes()).hexdigest() == row["sha256"]

def canonical_numbers(text):
    text = re.sub(r"!\[[^\]]*\]\([^\n]*\)", "", text)
    text = re.sub(r"Study 38\s*\(Lencioni et al\. \[@ferrarin2019walking\]\)", "", text)
    text = re.sub(r"Study 120\s*\(Gait120\s*\[@boo2025gait120\]\)", "Gait120", text)
    text = re.sub(r"Studies 28/33|Studies 28 and 33|Study 28/33|Study 28と33", "", text)
    text = re.sub(r"\bStudy (120|38|31|28|33|32|43|1)\b", "", text)
    for row in NAMES:
        if row["public_label"] != "Gait120":
            text = text.replace(row["public_label"], "")
    return re.findall(r"(?<![\w])[-+]?\d+(?:\.\d+)?", text)

def test_manuscript_numerical_results_and_citation_order_unchanged():
    old = read(PAPER / "versions/harmo-semg_manuscript_v028_2026.09.26-r2.md")
    new = read(PAPER / "paper.md")
    assert canonical_numbers(old) == canonical_numbers(new)
    assert re.findall(r"@[\w-]+", old) == re.findall(r"@[\w-]+", new)
    assert "S011" in new and "Gait120" in new

def test_editable_figures_have_new_labels_and_native_tables():
    ns = {"a": "http://schemas.openxmlformats.org/drawingml/2006/main"}
    for stem, count in ((F2, 2), (F3, 1)):
        file = PAPER / "figures/candidates" / stem / (stem + "_editable.pptx")
        with zipfile.ZipFile(file) as package:
            slide = ET.fromstring(package.read("ppt/slides/slide1.xml"))
        text = " ".join(n.text or "" for n in slide.findall(".//a:t", ns))
        assert "Lencioni 2019" in text and "Gait120" in text
        assert not re.search(r"\bStudy\s+\d+", text)
        assert len(slide.findall(".//a:tbl", ns)) == count

def test_old_manuscript_versions_preserved():
    for name in ("harmo-semg_manuscript_v028_2026.09.26-r2",
                 "harmo-semg_manuscript_ja_v003_2026.09.26-r2"):
        for extension in ("md", "tex", "pdf"):
            assert (PAPER / "versions" / (name + "." + extension)).is_file()
