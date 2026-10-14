"""Adoption consistency; not a new scientific experiment."""
import hashlib
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def test_selected_figure2_and_archived_previous_assets():
    sel = json.loads((ROOT / "paper/figures/FIGURE2_SELECTION.json").read_text())
    assert sel["version"] == "v012_2026.09.16-r1"
    assert sel["source"]["path"].endswith("_editable.pptx")
    for item in [sel["source"], sel["caption"], *sel["exports"]]:
        data = (ROOT / item["path"]).read_bytes()
        assert len(data) == item["bytes"]
        assert hashlib.sha256(data).hexdigest() == item["sha256"]
    pdf = next(x for x in sel["exports"] if x["path"].endswith(".pdf"))
    assert (ROOT / pdf["path"]).read_bytes() == (ROOT / "paper/cmpb/Fig2.pdf").read_bytes()
    archive = ROOT / "paper/figures/archive/figure2_v011_2026.09.12-r8"
    old = json.loads((archive / "FIGURE2_SELECTION.json").read_text())
    for item in [old["source"], old["caption"], *old["exports"]]:
        path = archive / Path(item["path"]).name
        if not path.exists():
            path = ROOT / item["path"]
        assert hashlib.sha256(path.read_bytes()).hexdigest() == item["sha256"]


def test_caption_and_scientific_scope():
    caption = (ROOT / "paper/figures/figure2_caption.md").read_text().strip()
    registry = (ROOT / "paper/figures/README.md").read_text()
    assert registry.split("## Figure 2.",1)[1].split("## Figure 3.",1)[0].strip() == caption.split("## Figure 2.",1)[1].strip()
    caption = " ".join(caption.split())
    for phrase in ["already share the phase grid", "not another alignment",
                   "Amplitude comparability remains unestablished", "distinct from the newer GUI",
                   "not an automatic trigger", "not new independent validation"]:
        assert phrase in caption


def test_result_values_and_scientific_rules_unchanged():
    before = (ROOT / "paper/versions/harmo-semg_manuscript_v014_2026.09.16-r2.md").read_text()
    after = (ROOT / "paper/paper.md").read_text()
    assert before.split("# Methods")[0] == after.split("# Methods")[0]
    assert before.split("## Validation of the framework")[1] == after.split("## Validation of the framework")[1]
    start = "The evaluator first checked"
    end = "### Processing selection and reassessment"
    assert before.split(start)[1].split(end)[0] == after.split(start)[1].split(end)[0]
