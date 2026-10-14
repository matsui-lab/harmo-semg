"""Adoption preserves approved Figure 3 and distinguishes the post hoc analysis."""
import hashlib
import json
import re
from pathlib import Path
from zipfile import ZipFile

ROOT = Path(__file__).resolve().parents[1]
VERSION = "v018_2026.09.16-r6"
STEM = "figure3_integrated_results_v008_2026.09.16-r8"

def test_approved_artwork_and_source_hashes():
    selection = json.loads((ROOT / "paper/figures/FIGURE3_SELECTION.json").read_text())
    assert selection["status"] == "selected_for_manuscript"
    assert selection["version"] == "v008_2026.09.16-r8"
    assert selection["manuscript_version"] == VERSION
    for item in [selection["source"], selection["caption"], *selection["exports"]]:
        data = (ROOT / item["path"]).read_bytes()
        assert len(data) == item["bytes"]
        assert hashlib.sha256(data).hexdigest() == item["sha256"]
    source = ROOT / "paper/figures/candidates" / STEM / f"{STEM}.pdf"
    assert source.read_bytes() == (ROOT / "paper/cmpb/Fig3.pdf").read_bytes()
    manifest = json.loads((ROOT / "paper/cmpb/manifest.json").read_text())
    for item in manifest["sources"]:
        assert hashlib.sha256((ROOT / item["path"]).read_bytes()).hexdigest() == item["sha256"]

def test_only_three_main_figures_and_no_stale_references():
    paper = (ROOT / "paper/paper.md").read_text()
    assert re.findall(r"!\[Figure (\d+)\.", paper) == ["1","2","3"]
    assert not re.search(r"Figures? [4-7]|Figures 3--7", paper)
    assert all(f"Figure 3{panel}" in paper for panel in "ABCDE")
    for name in ("figure2_caption.md", "README.md"):
        text = (ROOT / "paper/figures" / name).read_text()
        assert "Figure 6" not in text
    tex = (ROOT / "paper/cmpb/manuscript.tex").read_text()
    assert re.findall(r"\\label\{fig:(\d+)\}", tex) == ["1","2","3"]
    assert "Figure 3 (continued)" in tex
    with ZipFile(ROOT / "paper/cmpb/cmpb_submission.zip") as archive:
        assert sorted(n for n in archive.namelist() if re.fullmatch(r"Fig\d+\.pdf",n)) == ["Fig1.pdf","Fig2.pdf","Fig3.pdf"]
    assert not any((ROOT / f"paper/cmpb/Fig{n}.pdf").exists() for n in range(4,8))

def test_post_hoc_methods_results_and_limits():
    paper = (ROOT / "paper/paper.md").read_text()
    methods = paper.split("# Methods",1)[1].split("# Results",1)[0]
    results = paper.split("# Results",1)[1].split("# Discussion",1)[0]
    discussion = paper.split("# Discussion",1)[1].split("# Limitations",1)[0]
    for phrase in ("additional post hoc analysis","100,000","20260916","cycles were not resampled","not paired across studies"):
        assert phrase in methods
    for phrase in ("-4.33 to +4.97","-1.13 to +6.02","pointwise 95%","Both intervals included zero","0/330 were fully assessed","208 registered trials","130/130"):
        assert phrase in results
    assert "independently justified equivalence margin" in discussion
    assert "ten participants per study" in discussion
    assert "processing or systematic differences" in discussion
    assert "F)" not in (ROOT / "paper/figures/figure3_caption.md").read_text()

def test_history_protocol_and_snapshots():
    for ext,relative in (("md","paper/paper.md"),("tex","paper/cmpb/manuscript.tex"),("pdf","paper/cmpb/manuscript.pdf")):
        assert (ROOT / f"paper/versions/harmo-semg_manuscript_{VERSION}.{ext}").read_bytes() == (ROOT / relative).read_bytes()
        assert (ROOT / f"paper/versions/harmo-semg_manuscript_v017_2026.09.16-r5.{ext}").exists()
    assert hashlib.sha256((ROOT / "paper/physiology_positive_control_protocol.yaml").read_bytes()).hexdigest() == "9682b70ae5a1fec55e247ac74f9c4249e58a1249f764e3ba2dfb4ead57771267"
    assert hashlib.sha256((ROOT / "paper/results/validation/physiology_positive_control.json").read_bytes()).hexdigest() == "71df14ae5e5bf80005122dce27e5bc3b1492a822098d358b26832f5ac924d9a0"
    old = (ROOT / "paper/versions/harmo-semg_manuscript_v017_2026.09.16-r5.md").read_text()
    new = (ROOT / "paper/paper.md").read_text()
    assert old.split("# Methods")[0] == new.split("# Methods")[0]
