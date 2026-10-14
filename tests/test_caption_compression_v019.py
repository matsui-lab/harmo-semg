"""Compressed legends preserve reading conventions without changing evidence."""
import hashlib
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
VERSION = "v019_2026.09.16-r7"
PREVIOUS = "v018_2026.09.16-r6"
ARCHIVE = ROOT / "paper/figures/archive/captions_v018_2026.09.16-r6"

def test_captions_compressed_synchronized_and_artwork_unchanged():
    combined = (ROOT / "paper/figures/README.md").read_text()
    for number in (1, 2, 3):
        path = ROOT / f"paper/figures/figure{number}_caption.md"
        caption = path.read_text()
        before = (ARCHIVE / path.name).read_text()
        body = caption.split("\n", 2)[2]
        old_body = before.split("\n", 2)[2]
        assert len(body.split()) < 0.56 * len(old_body.split())
        assert caption.strip() in combined
        selected = json.loads((ROOT / f"paper/figures/FIGURE{number}_SELECTION.json").read_text())
        old = json.loads((ARCHIVE / f"FIGURE{number}_SELECTION.json").read_text())
        assert selected["source"] == old["source"]
        assert selected["exports"] == old["exports"]
        assert selected["caption_updated_in_manuscript"] == VERSION
        assert selected["caption"]["bytes"] == len(path.read_bytes())
        assert selected["caption"]["sha256"] == hashlib.sha256(path.read_bytes()).hexdigest()

def test_reading_conventions_and_limits_retained():
    captions = [(ROOT / f"paper/figures/figure{n}_caption.md").read_text() for n in (1, 2, 3)]
    for text, phrases in zip(captions, [
        ("schematic", "unresolved", "not themselves authorize", "R1 and D1"),
        ("separate Study 38 diagnostic run", "No human confirmations", "remained held", "Grey traces", "Dashed outlines"),
        ("separate evaluations", "Source-absent", "not independent participants", "20 Study 38-Gait120", "ten participants per study", "both remain", "pointwise 95%", "post hoc", "not processing error or biological equivalence"),
    ]):
        normalized = " ".join(text.split())
        for phrase in phrases:
            assert phrase in normalized

def test_current_build_and_history():
    main = (ROOT / "paper/paper.md").read_bytes()
    assert main == (ROOT / f"paper/versions/harmo-semg_manuscript_{PREVIOUS}.md").read_bytes()
    for ext, live in (("md", "paper/paper.md"), ("tex", "paper/cmpb/manuscript.tex"), ("pdf", "paper/cmpb/manuscript.pdf")):
        assert (ROOT / f"paper/versions/harmo-semg_manuscript_{VERSION}.{ext}").read_bytes() == (ROOT / live).read_bytes()
        assert (ROOT / f"paper/versions/harmo-semg_manuscript_{PREVIOUS}.{ext}").exists()
    manifest = json.loads((ROOT / "paper/cmpb/manifest.json").read_text())
    assert manifest["manuscript_version"] == VERSION
    for source in manifest["sources"]:
        assert hashlib.sha256((ROOT / source["path"]).read_bytes()).hexdigest() == source["sha256"]
