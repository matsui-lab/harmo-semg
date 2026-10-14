"""Keep the main table focused on the method without losing source evidence."""
import csv
import hashlib
import json
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
VERSION = "v020_2026.09.16-r8"
PREVIOUS = "v019_2026.09.16-r7"


def test_only_rules_remain_a_main_table():
    paper = (ROOT / "paper/paper.md").read_text()
    assert re.findall(r"\[Table (\d+)\.", paper) == ["1"]
    assert not re.search(r"\bTable [23]\b", paper)
    assert "Supplementary Table S1" in paper
    assert "Supplementary Table S2" in paper
    tex = (ROOT / "paper/cmpb/manuscript.tex").read_text()
    main, supplement = tex.split(r"\section*{Supplementary tables}", 1)
    assert re.findall(r"\\label\{tab:([^}]+)\}", main) == ["purpose-specific-rules"]
    assert re.findall(r"\\label\{tab:([^}]+)\}", supplement) == ["public-studies", "validation-endpoints"]
    assert "tab:reporting-evidence" not in tex
    assert main.index(r"\bibliography{references}") > main.index(r"\label{tab:purpose-specific-rules}")


def test_complete_supplement_and_ledger_retained():
    for new, old in (
        ("table_s1_public_studies.md", "table1_public_studies.md"),
        ("table_s2_validation_endpoints.md", "table2_validation_endpoints.md"),
    ):
        assert (ROOT / "paper/tables" / old).read_text().strip() in (ROOT / "paper/tables" / new).read_text()
    with (ROOT / "paper/tables/table1_public_studies.csv").open() as stream:
        studies = list(csv.DictReader(stream))
    assert len(studies) == 8
    assert sum(int(row["enabled_mappings"]) for row in studies) == 78
    with (ROOT / "paper/tables/table2_validation_endpoints.csv").open() as stream:
        assert [row["id"] for row in csv.DictReader(stream)] == list("ABCDEFGH")
    ledger = ROOT / "paper/tables/table_s1_reporting_evidence.csv"
    with ledger.open() as stream:
        for row in csv.DictReader(stream):
            data = (ROOT / row["path"]).read_bytes()
            assert len(data) == int(row["bytes"])
            assert hashlib.sha256(data).hexdigest() == row["sha256"]


def test_rules_figures_and_claims_unchanged():
    old_rules = (ROOT / "paper/tables/archive/v019_2026.09.16-r7/table3_purpose_specific_rules.md").read_text()
    current_rules = (ROOT / "paper/tables/table3_purpose_specific_rules.md").read_text()
    assert old_rules.replace("# Table 3.", "# Table 1.", 1) == current_rules
    old = (ROOT / f"paper/versions/harmo-semg_manuscript_{PREVIOUS}.md").read_text()
    current = (ROOT / "paper/paper.md").read_text()
    assert old.split("# Methods")[0] == current.split("# Methods")[0]
    assert old.split("# Discussion")[1].split("# Data and code availability")[0] == current.split("# Discussion")[1].split("# Data and code availability")[0]
    for n in (1, 2, 3):
        selected = json.loads((ROOT / f"paper/figures/FIGURE{n}_SELECTION.json").read_text())
        for item in [selected["source"], selected["caption"], *selected["exports"]]:
            assert hashlib.sha256((ROOT / item["path"]).read_bytes()).hexdigest() == item["sha256"]


def test_versioned_build_sources_match():
    for extension, live in (("md", "paper/paper.md"), ("tex", "paper/cmpb/manuscript.tex"), ("pdf", "paper/cmpb/manuscript.pdf")):
        assert (ROOT / f"paper/versions/harmo-semg_manuscript_{VERSION}.{extension}").read_bytes() == (ROOT / live).read_bytes()
        assert (ROOT / f"paper/versions/harmo-semg_manuscript_{PREVIOUS}.{extension}").exists()
    manifest = json.loads((ROOT / "paper/cmpb/manifest.json").read_text())
    assert manifest["manuscript_version"] == VERSION
    for item in manifest["sources"]:
        assert hashlib.sha256((ROOT / item["path"]).read_bytes()).hexdigest() == item["sha256"]
