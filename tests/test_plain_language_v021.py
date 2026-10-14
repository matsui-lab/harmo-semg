"""Plain-language manuscript edits do not alter scientific values or evidence."""
import hashlib
import json
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
VERSION = "v021_2026.09.16-r9"


def test_publication_omits_internal_file_verification_terms():
    files = ["paper/paper.md", "paper/tables/table_s2_validation_endpoints.md",
             "paper/tables/table3_purpose_specific_rules.md"]
    files += [f"paper/figures/figure{n}_caption.md" for n in (1, 2, 3)]
    for path in files:
        assert not re.search(r"\b(SHA-256|MD5|checksums?|digests?|hash(?:es)?|manifests?|ledger|canonical)\b",
                             (ROOT / path).read_text(), re.I)


def test_scientific_numbers_and_disclosure_preserved():
    old = (ROOT / "paper/versions/harmo-semg_manuscript_v020_2026.09.16-r8.md").read_text()
    new = (ROOT / "paper/paper.md").read_text()
    def numbers(text):
        text = re.sub(r"\b(?:SHA-256|MD5)\b", "", text)
        return re.findall(r"\d+(?:[.,]\d+)*", text)
    assert numbers(old) == numbers(new)
    assert old.split("# AI usage disclosure")[1] == new.split("# AI usage disclosure")[1]
    assert "not exact identity of output files across operating systems" in new
    assert "not an independent two-rater assessment" in new


def test_table_retains_original_endpoints_without_status_codes():
    table = (ROOT / "paper/tables/table_s2_validation_endpoints.md").read_text()
    rows = [line for line in table.splitlines() if line.startswith("|")]
    assert len(rows) == 10
    assert [row.split("|")[1].strip() for row in rows[2:]] == list("ABCDEFGH")
    assert all(len(row.strip("|").split("|")) == 5 for row in rows)
    assert "scientific-fail" not in table and "scientific digests" not in table
    for value in ("1,321,948", "96", "34 discordant", "330", "336", "208/208",
                  "38/40", "130", "138,638", "1,109,104"):
        assert value in table
    assert "does not establish equivalence" in table


def test_build_and_original_evidence_unchanged():
    for extension, path in (("md", "paper/paper.md"), ("tex", "paper/cmpb/manuscript.tex"), ("pdf", "paper/cmpb/manuscript.pdf")):
        assert (ROOT / f"paper/versions/harmo-semg_manuscript_{VERSION}.{extension}").read_bytes() == (ROOT / path).read_bytes()
    manifest = json.loads((ROOT / "paper/cmpb/manifest.json").read_text())
    for item in manifest["sources"]:
        assert hashlib.sha256((ROOT / item["path"]).read_bytes()).hexdigest() == item["sha256"]
    assert hashlib.sha256((ROOT / "paper/results/validation/physiology_positive_control.json").read_bytes()).hexdigest() == "71df14ae5e5bf80005122dce27e5bc3b1492a822098d358b26832f5ac924d9a0"
