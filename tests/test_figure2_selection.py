"""Keep the selected Figure 3, caption, and manuscript panels synchronized."""
import hashlib
import json
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def test_selected_figure2_sources_and_exports():
    selection = json.loads((ROOT / "paper/figures/FIGURE3_SELECTION.json").read_text())
    assert selection["status"] == "selected_for_manuscript"
    assert len(selection["exports"]) == 3
    for item in [selection["source"], selection["caption"], *selection["exports"]]:
        data = (ROOT / item["path"]).read_bytes()
        assert len(data) == item["bytes"]
        assert hashlib.sha256(data).hexdigest() == item["sha256"]
    pdf = next(item for item in selection["exports"] if item["path"].endswith(".pdf"))
    assert (ROOT / "paper/cmpb/Fig3.pdf").read_bytes() == (ROOT / pdf["path"]).read_bytes()


def test_figure3_caption_and_panel_references():
    caption = (ROOT / "paper/figures/figure3_caption.md").read_text().strip()
    captions = (ROOT / "paper/figures/README.md").read_text()
    assert captions.split("## Figure 3.", 1)[1].split("## Figure 4.", 1)[0].strip() == caption.split("## Figure 3.", 1)[1].strip()
    paper = (ROOT / "paper/paper.md").read_text()
    methods = paper.split("# Methods", 1)[1].split("# Results", 1)[0]
    results = paper.split("# Results", 1)[1].split("# Discussion", 1)[0]
    for section in (methods, results):
        for panel in "ABC":
            assert f"Figure 3{panel}" in section
    assert "not participants" in caption
    assert "not external" in caption
    assert "11,528" in caption
