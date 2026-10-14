"""Approved Results/Discussion revision preserves evidence and scope."""
import hashlib
import json
import re
from pathlib import Path
ROOT = Path(__file__).resolve().parents[1]
VERSION = "v022_2026.09.16-r10"
SUPPLEMENT = "paper/supplementary_results_v022_2026.09.16-r10.md"

def test_only_results_and_discussion_changed():
    old = (ROOT / "paper/versions/harmo-semg_manuscript_v021_2026.09.16-r9.md").read_text()
    new = (ROOT / "paper/paper.md").read_text()
    assert old.split("# Results")[0] == new.split("# Results")[0]
    assert old.split("# Limitations")[1] == new.split("# Limitations")[1]
    results = new.split("# Results")[1].split("# Discussion")[0]
    discussion = new.split("# Discussion")[1].split("# Limitations")[0]
    assert len(re.findall(r"^## ", results, re.M)) == 6
    assert len(re.findall(r"^## ", discussion, re.M)) == 3
    assert "Expected timing patterns were observed" in results
    assert "substantive advantage" not in discussion
    assert "do not establish superiority" in discussion
    assert "not validation\nagainst physiological event labels" in results

def test_main_effects_and_additional_results_retained():
    paper = (ROOT / "paper/paper.md").read_text()
    for term in ("1,321,948", "11,528", "44,776", "38/40", "+0.52", "+1.17",
                 "-4.33", "+4.97", "-1.13", "+6.02", "336", "330", "1,109,104", "1,616"):
        assert term in paper
    supplement = (ROOT / SUPPLEMENT).read_text()
    for term in ("143", "170", "1,211", "404", "77 targeted", "195 tests",
                 "202-test", "64 timing and 64", "2.30", "2.37", "0.01757",
                 "53 of 101", "33%", "2.13", "19/21", "57/67"):
        assert term in supplement
    assert "Both pattern failures remained" in paper
    assert "not external independent validation" in paper
    assert "source-absent" in supplement
    assert "not validation of the newer" in supplement

def test_evidence_and_figure_sources_unchanged():
    expected = {
    "paper/figures/README.md": "0031e83477d64cf631b72d69a106808079316c8d87cb5f30e0e6b3bceab35ee0",
    "paper/figures/figure1_caption.md": "6c32df45ec17a944d3daa39ddf5e7b50f51de531c138d1bbccbb30a8e9ef3b77",
    "paper/figures/FIGURE1_SELECTION.json": "7e4a211294217ba4c6c83726e673e4be4c3e9839cdd473869181671ec4aa52bd",
    "paper/figures/editable/figure1_four_panel_v018_2026.09.16-r1_editable.pptx": "8ede10ca4020f23398516c89f7e7c1d63c029fe0f5492f81d84af6facd55a9cf",
    "paper/figures/figure2_caption.md": "e2784d8658d807f415c8af29e7f2e4e34a93af20145e5754d1553681a7e79a38",
    "paper/figures/FIGURE2_SELECTION.json": "e6627779495a0ea040024a6282822f397b8dfb0bc2efa3dcbab2e8b69f9ec4f1",
    "paper/figures/candidates/figure2_implementation_gui_v016_2026.09.16-r5/figure2_implementation_gui_v016_2026.09.16-r5_editable.pptx": "a3e06a01da956611a0c5563477f18ec426fed0feb7b02fe8c354dd7e4e2d8424",
    "paper/figures/figure3_caption.md": "510a7387536cff7d68ed8a1005b5ac9a52e488a2aa43a0e2e07eca41922e8bf4",
    "paper/figures/FIGURE3_SELECTION.json": "c198c162a347a138b48443de084ac65910773cbb2dba8402565dff7fe92ec5cd",
    "paper/figures/candidates/figure3_integrated_results_v008_2026.09.16-r8/figure3_integrated_results_v008_2026.09.16-r8_editable.pptx": "dc2104390c78854d68e8758f96526357cc1e3a4d97d43e363d88cc67c74db93d",
    "paper/figures/candidates/figure3_integrated_results_v008_2026.09.16-r8/figure3_integrated_results_v008_2026.09.16-r8_evidence.json": "6dc75e4457b5470257056f0ba3e39d3acd009eef149f79a4c040b900d460e429",
    "paper/results/centroid_bootstrap_v001_2026.09.16-r1/summary.json": "59085c0c869a48d50a5a5223c359ff15380da7d3fac0f2d98ea365b4deca1af4",
    "paper/results/validation/physiology_positive_control.json": "71df14ae5e5bf80005122dce27e5bc3b1492a822098d358b26832f5ac924d9a0",
    "paper/tables/table1_public_studies.csv": "3652da34f0308d95a5fdf59820cd8b805aca1d63f8dc2aec3a31db97a4c9a2c9",
    "paper/tables/table2_validation_endpoints.csv": "23ea6ca24035a77f268321d7e2acfeb1e362ac1841c86cef2d0c454c08d5dc9e",
    "paper/tables/table3_purpose_specific_rules.csv": "a6939da14149236d67043d9def903d0afba5770277ddcbf62cbd8c8b9d63ebb8",
    "paper/tables/table_s1_reporting_evidence.csv": "3b3816f14cd47fbd6f4df43d4f2b9fb9a59d0575a65d78afb730599405bb70ab",
    "paper/tables/table_s1_public_studies.md": "3cfabcf4d04a6ae8fc4ec1ccf99a3277bd209e5cfeee51de5f1cf6e77642c976",
    "paper/tables/table_s2_validation_endpoints.md": "5d2a96800bbe10ba1d4dee1dcf041c48bb25272a8e77b80a0a1f5c41f5c3ff4c",
    "paper/tables/table3_purpose_specific_rules.md": "f4fe83183ed9b2e663d180595d7470626b49ba267b48b1f605981b2c927fa816",
    "paper/figures/editable/figure1_four_panel_v018_2026.09.16-r1.pdf": "1f02a0bdbe3a75daa962e42b961dc1057924f05063514d560dea38e9e19573f0",
    "paper/figures/candidates/figure2_implementation_gui_v016_2026.09.16-r5/figure2_implementation_gui_v016_2026.09.16-r5.pdf": "95de29389d73e8a250af40b2e44fea277b03d2da787ae1763cf440b97039ddd5",
    "paper/figures/candidates/figure3_integrated_results_v008_2026.09.16-r8/figure3_integrated_results_v008_2026.09.16-r8.pdf": "66e00e2e7b0841794f9e928003c5f86fde967aab16ced16d1fa69e742fd5a09c"
}
    for path, digest in expected.items():
        assert hashlib.sha256((ROOT / path).read_bytes()).hexdigest() == digest

def test_versioned_build_contains_supplement():
    for extension, live in (("md","paper/paper.md"),("tex","paper/cmpb/manuscript.tex"),("pdf","paper/cmpb/manuscript.pdf")):
        assert (ROOT / f"paper/versions/harmo-semg_manuscript_{VERSION}.{extension}").read_bytes() == (ROOT / live).read_bytes()
    tex = (ROOT / "paper/cmpb/manuscript.tex").read_text()
    assert r"\label{sec:supplementary-results}" in tex
    assert r"\pageref{sec:supplementary-results}" in tex
    assert re.findall(r"\\label\{fig:(\d+)\}", tex) == ["1","2","3"]
    manifest = json.loads((ROOT / "paper/cmpb/manifest.json").read_text())
    assert manifest["manuscript_version"] == VERSION
    assert SUPPLEMENT in [x["path"] for x in manifest["sources"]]
    for item in manifest["sources"]:
        assert hashlib.sha256((ROOT / item["path"]).read_bytes()).hexdigest() == item["sha256"]
