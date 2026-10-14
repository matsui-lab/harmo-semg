"""Keep the author-selected Figure 1 and its manuscript references in sync."""
import hashlib
import json
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def test_selected_figure1_source_and_exports_match_recorded_identities():
    selection = json.loads((ROOT / "paper/figures/FIGURE1_SELECTION.json").read_text())
    assert selection["status"] == "selected_for_manuscript"
    assert selection["selected_slide"] == 1
    for item in [selection["source"], selection["caption"], *selection["exports"]]:
        data = (ROOT / item["path"]).read_bytes()
        assert len(data) == item["bytes"]
        assert hashlib.sha256(data).hexdigest() == item["sha256"]
    expected_pdf = next(x for x in selection["exports"] if x["path"].endswith(".pdf"))
    assert hashlib.sha256((ROOT / "paper/cmpb/Fig1.pdf").read_bytes()).hexdigest() == expected_pdf["sha256"]


def test_figure1_caption_and_panel_references_are_current():
    caption = (ROOT / "paper/figures/figure1_caption.md").read_text().strip()
    captions = (ROOT / "paper/figures/README.md").read_text()
    assert captions.split("## Figure 1.", 1)[1].split("## Figure 2.", 1)[0].strip() == caption.split("## Figure 1.", 1)[1].strip()
    paper = (ROOT / "paper/paper.md").read_text()
    framework = paper.split("# Conceptual framework", 1)[1].split("# Methods", 1)[0]
    for panel in "ABCD":
        assert f"Figure 1{panel}" in framework
    assert "first three steps in Figure 1" not in paper
    assert "not measured outcomes" in caption


def test_current_manuscript_has_matching_versioned_snapshots():
    version = (ROOT / "paper/MANUSCRIPT_VERSION").read_text().strip()
    for ext, source in [("md", "paper/paper.md"), ("tex", "paper/cmpb/manuscript.tex"), ("pdf", "paper/cmpb/manuscript.pdf")]:
        snapshot = ROOT / f"paper/versions/harmo-semg_manuscript_{version}.{ext}"
        assert snapshot.read_bytes() == (ROOT / source).read_bytes()
