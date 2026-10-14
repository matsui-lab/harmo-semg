"""Clarity revision retains evidence, defines outcomes, and versions its supplement."""
import hashlib
import json
import re
from pathlib import Path
import pytest

ROOT = Path(__file__).resolve().parents[1]
VERSION = "v023_2026.09.16-r11"
OLD = "v022_2026.09.16-r10"
PAPER = ROOT / f"paper/versions/harmo-semg_manuscript_{VERSION}.md"
METHODS = ROOT / f"paper/supplementary_methods_{VERSION}.md"
RESULTS = ROOT / f"paper/supplementary_results_{VERSION}.md"


def section(text, name, next_name):
    return text.split("# " + name + "\n", 1)[1].split("# " + next_name + "\n", 1)[0]


def test_main_argument_shorter_without_removing_algorithms():
    old = (ROOT / f"paper/versions/harmo-semg_manuscript_{OLD}.md").read_text()
    new = PAPER.read_text()
    methods = section(new, "Methods", "Results")
    assert len(methods.split()) < 0.7 * len(section(old, "Methods", "Results").split())
    assert len(re.findall(r"^### ", methods.split("## Evaluation")[1].split("## Statistical")[0], re.M)) == 6
    for item in ("not comparable", "unassessed", "conditionally comparable", "comparable",
                 "fourth-order", "20--350 Hz", "125-ms", "101 points", "100i/(N-1)",
                 "Only accepted or corroborated", "amplitude normalization",
                 "separate use policy", "structured", "not interpretations of free-text"):
        assert item in methods
    assert new.split("# Data and code availability")[1] == old.split("# Data and code availability")[1]


def test_metrics_and_units_are_defined():
    paper = PAPER.read_text()
    for item in ("envelope-weighted mean phase", r"\sum_j p_j a_j / \sum_j a_j",
                 "0--20%", "60--100%", "20--60%", "70%", "positive median log-ratio",
                 "root-mean-square (RMS)", "Root-mean-square error (RMSE)",
                 "between-study median", "pointwise 95%", "not an equivalence",
                 "10,000", "100,000", "including both pattern failures",
                 "not external", "not independent", "not extraction-accuracy"):
        assert item in paper
    assert "Between-study differences in median activation centroid" in paper
    assert "conversion to\n a common format" not in paper  # no malformed inserted indentation
    assert "not amplitude normalization" in paper
    assert "1.1.0" in METHODS.read_text() and "20260723" in METHODS.read_text()
    assert "20260916" in METHODS.read_text() and "NumPy PCG64" in METHODS.read_text()


def test_primary_numbers_and_moved_details_remain():
    paper = PAPER.read_text()
    for item in ("78", "24 to 43", "30 equivalent", "34 discordant", "26 were equivalent",
                 "38/40", "+0.52", "+1.17", "-4.33", "+4.97", "-1.13", "+6.02",
                 "330", "336", "1,109,104", "0.02085", r"1.19 \times 10^{-12}"):
        assert item in paper
    supplement = METHODS.read_text() + RESULTS.read_text()
    for item in ("1,321,948", "11,528", "44,776", "1,616", "210", "143", "170",
                 "1,211", "404", "77 targeted", "195 tests", "202-test", "64 timing and 64",
                 "2.30", "2.37", "0.01757", "53 of 101", "33%", "2.13",
                 "19/21", "57/67", "20/21", "59/67", "21/21", "67 claims",
                 "25 repetitions", "0.95", "0.02 m", "0.04 m"):
        assert item in supplement
    assert not re.search(r"SHA.?256|MD5|checksum|digest", paper + supplement, re.I)


def test_numeric_evidence_rules_and_artwork_unchanged():
    expected = {
    "paper/figures/editable/figure1_four_panel_v018_2026.09.16-r1_editable.pptx": "8ede10ca4020f23398516c89f7e7c1d63c029fe0f5492f81d84af6facd55a9cf",
    "paper/figures/candidates/figure2_implementation_gui_v016_2026.09.16-r5/figure2_implementation_gui_v016_2026.09.16-r5_editable.pptx": "a3e06a01da956611a0c5563477f18ec426fed0feb7b02fe8c354dd7e4e2d8424",
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
        assert hashlib.sha256((ROOT / path).read_bytes()).hexdigest() == digest, path


def test_current_build_and_caption():
    if (ROOT / "paper/MANUSCRIPT_VERSION").read_text().strip() != VERSION:
        pytest.skip("Live manuscript has advanced; v023 snapshots remain covered above.")
    for extension, live in (("md", "paper/paper.md"), ("tex", "paper/cmpb/manuscript.tex"),
                            ("pdf", "paper/cmpb/manuscript.pdf")):
        assert (ROOT / f"paper/versions/harmo-semg_manuscript_{VERSION}.{extension}").read_bytes() == (ROOT / live).read_bytes()
    tex = (ROOT / "paper/cmpb/manuscript.tex").read_text()
    assert re.findall(r"\\label\{fig:(\d+)\}", tex) == ["1", "2", "3"]
    for section_name in ("supplementary-methods", "supplementary-results"):
        assert rf"\label{{sec:{section_name}}}" in tex
        assert rf"\pageref{{sec:{section_name}}}" in tex
    manifest = json.loads((ROOT / "paper/cmpb/manifest.json").read_text())
    assert manifest["manuscript_version"] == VERSION
    assert str(METHODS.relative_to(ROOT)) in [s["path"] for s in manifest["sources"]]
    assert str(RESULTS.relative_to(ROOT)) in [s["path"] for s in manifest["sources"]]
    for item in manifest["sources"]:
        assert hashlib.sha256((ROOT / item["path"]).read_bytes()).hexdigest() == item["sha256"]
    caption = (ROOT / "paper/figures/figure3_caption.md").read_text()
    assert "envelope-weighted mean gait phase" in caption
    assert 'baseline labelled "Files"' in caption
    assert "analyst-maintained definitions" in caption
    selection = json.loads((ROOT / "paper/figures/FIGURE3_SELECTION.json").read_text())
    assert selection["caption"]["sha256"] == hashlib.sha256(caption.encode()).hexdigest()
