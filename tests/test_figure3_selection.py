"""Keep the selected pastel Figure 4 synchronized with manuscript claims."""
import hashlib
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def test_selected_figure3_sources_and_exports_match():
    selection = json.loads((ROOT / "paper/figures/FIGURE4_SELECTION.json").read_text())
    assert selection["status"] == "selected_for_manuscript"
    assert selection["version"] == "v002_2026.09.09-r2"
    for item in [selection["source"], selection["caption"], *selection["exports"]]:
        data = (ROOT / item["path"]).read_bytes()
        assert len(data) == item["bytes"]
        assert hashlib.sha256(data).hexdigest() == item["sha256"]
    pdf = next(item for item in selection["exports"] if item["path"].endswith(".pdf"))
    assert (ROOT / "paper/cmpb/Fig4.pdf").read_bytes() == (ROOT / pdf["path"]).read_bytes()


def test_selected_caption_and_denominators_match_manuscript():
    caption = (ROOT / "paper/figures/figure4_caption.md").read_text().strip()
    combined = (ROOT / "paper/figures/README.md").read_text()
    assert combined.split("## Figure 4.", 1)[1].split("## Figure 5.", 1)[0].strip() == caption.split("## Figure 4.", 1)[1].strip()
    paper = (ROOT / "paper/paper.md").read_text()
    methods = paper.split("# Methods", 1)[1].split("# Results", 1)[0]
    results = paper.split("# Results", 1)[1].split("# Discussion", 1)[0]
    for section in (methods, results):
        assert all(f"Figure 4{panel}" in section for panel in "ABC")
    assert "30 pairs were numerically equivalent" in results
    assert "two retained declared source" in results
    assert "1.0 (Figure 4B)" not in paper
    assert "source-channel instances" in results
    assert "not independent participant observations" in caption
