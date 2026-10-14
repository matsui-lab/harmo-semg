"""Source identity and claim-boundary checks for the implementation diagram."""
import hashlib
import json
from pathlib import Path
from xml.etree import ElementTree as ET

ROOT = Path(__file__).resolve().parents[1]
VERSION = (ROOT / "paper/figures/candidates/IMPLEMENTATION_FIGURE_VERSION").read_text().strip()
STEM = f"implementation_four_panel_{VERSION}"
OUTPUT = ROOT / "paper/figures/candidates" / STEM


def test_implementation_sources_and_native_vector_text():
    evidence = json.loads((OUTPUT / f"{STEM}_evidence.json").read_text())
    assert evidence["status"] == "candidate_not_adopted"
    assert evidence["minimum_text_pt"] >= 7.5
    for item in evidence["sources"]:
        assert hashlib.sha256((ROOT / item["path"]).read_bytes()).hexdigest() == item["sha256"]
    svg = ET.parse(OUTPUT / f"{STEM}.svg")
    ns = "{http://www.w3.org/2000/svg}"
    assert len(list(svg.iter(ns + "text"))) == evidence["text_count"]
    assert not list(svg.iter(ns + "image"))
    assert (OUTPUT / f"{STEM}.pdf").read_bytes().startswith(b"%PDF-")


def test_input_and_output_values_are_identical_and_scope_is_retained():
    e = json.loads((OUTPUT / f"{STEM}_examples.json").read_text())
    assert len(e["long_rows"]) == 4
    for row in e["long_rows"]:
        i = next(i for i,t in enumerate(e["times"]) if t["time_seconds"] == row["time_seconds"])
        assert row["value"] == e["samples"][i][row["source_label"]]
    assert e["claim"]["value"] == [20,450]
    assert e["claim"]["scope_level"] == "dataset"
    assert e["claim"]["propagation"] == "none"
    assert e["claim_decision"]["status"] == "accepted"
    assert e["comparison"]["status"] == "conditionally_comparable"


def test_synthetic_cycles_use_the_actual_resolver_interpolation():
    import numpy as np
    from harmo.evidence.resolution import _process_values
    e = json.loads((OUTPUT / f"{STEM}_examples.json").read_text())
    for curve in e["synthetic_resolution"]:
        values = np.asarray(curve["source"])[:,None]
        expected, operations, scale = _process_values(values,None,{"amplitude_normalization":"none"},101)
        np.testing.assert_array_equal(expected[:,0],curve["derived"])
        assert expected.shape == (101,1)
        assert scale == "source-scale"
        assert [op.operation for op in operations] == ["linear-phase-resample"]
    caption = (OUTPUT / f"{STEM}_caption.md").read_text()
    assert "not empirical" in caption
    assert "already bounded cycles" in caption
    assert "not a direct one-to-one release command" in caption


def test_boxed_revision_preserves_words_and_example_data():
    previous_stem = "implementation_four_panel_v001_2026.09.11-r1"
    previous = ROOT / "paper/figures/candidates" / previous_stem
    ns = "{http://www.w3.org/2000/svg}"
    def words(path):
        return [element.text for element in ET.parse(path).iter(ns + "text")]
    assert words(OUTPUT / f"{STEM}.svg") == words(previous / f"{previous_stem}.svg")
    assert json.loads((OUTPUT / f"{STEM}_examples.json").read_text()) == json.loads(
        (previous / f"{previous_stem}_examples.json").read_text()
    )


def test_four_to_three_layout_retains_native_text_sizes():
    ns = "{http://www.w3.org/2000/svg}"
    current = ET.parse(OUTPUT / f"{STEM}.svg").getroot()
    previous_stem = "implementation_four_panel_v002_2026.09.11-r2"
    previous = ET.parse(ROOT / "paper/figures/candidates" / previous_stem / f"{previous_stem}.svg").getroot()
    assert current.attrib["viewBox"] == "0 0 190 142.5"
    before = list(previous.iter(ns + "text"))
    after = list(current.iter(ns + "text"))
    assert len(before) == len(after) == 95
    for a, b in zip(before, after):
        assert a.text == b.text
        for key in ("font-size", "font-family", "font-weight", "fill", "x"):
            assert a.attrib[key] == b.attrib[key]
        assert "transform" not in b.attrib
    assert not list(current.iter(ns + "g"))
