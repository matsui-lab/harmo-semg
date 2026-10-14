"""Figure 1 adoption checks, distinct from scientific validation."""
import json
import hashlib
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
OLD = ROOT / "paper/versions/harmo-semg_manuscript_v013_2026.09.16-r1.md"


def test_only_framework_and_methods_prose_changed():
    before, after = OLD.read_text(), (ROOT / "paper/paper.md").read_text()
    assert before.split("# Conceptual framework")[0] == after.split("# Conceptual framework")[0]
    assert before.split("# Results\n")[1] == after.split("# Results\n")[1]
    assert before.split("## Validation of the framework")[1] == after.split("## Validation of the framework")[1]
    for start, end in [
        ("### Source acquisition and common recording conversion", "### Documentary evidence linkage"),
        ("### Structured use requests and comparability rules", "### Linked storage and local operation"),
    ]:
        assert before.split(start)[1].split(end)[0] == after.split(start)[1].split(end)[0]


def test_approved_artwork_preserved_and_old_selection_archived():
    selection = json.loads((ROOT / "paper/figures/FIGURE1_SELECTION.json").read_text())
    assert selection["version"] == "v018_2026.09.16-r1"
    assert selection["manuscript_version"] == "v014_2026.09.16-r2"
    candidate = ROOT / ("paper/figures/candidates/figure1_revision_v018_2026.09.16-r1/"
                        "figure1_four_panel_v018_2026.09.16-r1_editable.pptx")
    assert candidate.read_bytes() == (ROOT / selection["source"]["path"]).read_bytes()
    archive = ROOT / "paper/figures/archive/figure1_v017_2026.09.09-r1"
    previous = json.loads((archive / "FIGURE1_SELECTION.json").read_text())
    assert previous["version"] == "v017_2026.09.09-r1"
    for item in [previous["source"], previous["caption"], *previous["exports"]]:
        path = archive / Path(item["path"]).name
        if not path.exists():
            path = ROOT / item["path"]
        assert hashlib.sha256(path.read_bytes()).hexdigest() == item["sha256"]


def test_caption_matches_new_panels_and_claim_boundaries():
    caption = (ROOT / "paper/figures/figure1_caption.md").read_text()
    normalized = " ".join(caption.split())
    for phrase in [
        "target channel and scope", "source-based review", "registered processing",
        "separate linked entities", "conditional timing decision and held amplitude decision",
        "storage confers no additional permission", "not measured outcomes",
        "not a single GUI",
    ]:
        assert phrase in normalized
    for obsolete in ["eight-study corpus", "Heel-strike markers", "lower branch accepts"]:
        assert obsolete not in caption
