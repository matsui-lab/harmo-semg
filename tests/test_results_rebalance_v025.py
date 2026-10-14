"""Results rebalance promotes existing evidence without changing scientific inputs."""
import hashlib
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
VERSION = "v025_2026.09.16-r13"
OLD = "v024_2026.09.16-r12"
BASELINE = {
    "paper/versions/harmo-semg_manuscript_v024_2026.09.16-r12.md": "b3dd7861de3873f1172eaf20137a08b7a2ec40615f853cad0d9eacbffcaf7ff9",
    "paper/versions/harmo-semg_manuscript_v024_2026.09.16-r12.tex": "877ebce35a28d9b5ecc425d56860e42fa8d9d821c3f3133f148a0b78eefad772",
    "paper/versions/harmo-semg_manuscript_v024_2026.09.16-r12.pdf": "b67731e7a661605629c5e49bac5dd7e415c41490fb6b89cf0e9c7b7c2daec0ad",
    "paper/supplementary_methods_v024_2026.09.16-r12.md": "b73875eef69c32194102d4620b67ebfaa9b628a55b74a4f81036096e24a95e5b",
    "paper/supplementary_results_v024_2026.09.16-r12.md": "f4eea34a1ff2c84eedb28bb0a5b42f600c7163c4c85286a22b87cf5b63169353",
    "paper/results/registered_reuse_clean_v001_2026.09.13-r1/timing-001/summary.json": "e9f8b64a953a0b470a95f5c6166817a569caaf4742f13b043b67a5711a444cbe",
    "paper/results/document_field_join_v003_2026.09.15-r3/verification.json": "462bc46bf47957d3868dc38560f46e927e2a5b01590ace7e37b54c2918bccc22"
}
TIMING = "paper/results/registered_reuse_clean_v001_2026.09.13-r1/timing-001"


def read(path):
    return json.loads((ROOT / path).read_text())


def text(version):
    return (ROOT / f"paper/versions/harmo-semg_manuscript_{version}.md").read_text()


def section(document, heading, next_heading):
    return document.split(heading, 1)[1].split(next_heading, 1)[0]


def test_only_methods_and_results_are_revised():
    old, new = text(OLD), text(VERSION)
    assert new.split("# Methods\n", 1)[0] == old.split("# Methods\n", 1)[0]
    assert new.split("# Discussion\n", 1)[1] == old.split("# Discussion\n", 1)[1]
    assert section(new, "## Implementation\n", "## Evaluation\n") == section(old, "## Implementation\n", "## Evaluation\n")
    assert section(new, "Two measures described", "criterion. Amplitudes") == section(old, "Two measures described", "criterion. Amplitudes")
    before = len(section(old, "# Results\n", "# Discussion\n").split())
    after = len(section(new, "# Results\n", "# Discussion\n").split())
    assert 180 <= after - before <= 300
    assert len(section(new, "# Methods\n", "# Results\n").split()) <= len(section(old, "# Methods\n", "# Results\n").split())
    assert len(section(new, "# Results\n", "# Discussion\n").split("\n## ")) == 7


def test_registered_pipeline_is_reported_with_its_actual_scope():
    summary = read(f"{TIMING}/summary.json")
    binding = read(f"{TIMING}/catalog.binding.json")
    selected = read(f"{TIMING}/selection.json")
    assert summary["selected_catalog_cycles"] == len(binding["bindings"]) == 50
    assert summary["catalog_subject_curves_verified"] == len(binding["curves"]) == 20
    assert len(selected) == summary["comparisons"] == summary["released"] == 20
    assert all(row["status"] == "released" and row["signal_export"] is not None for row in selected)
    assert summary["amplitude_comparability"] == "not-established"
    results = section(text(VERSION), "# Results\n", "# Discussion\n")
    for phrase in ("Fifty selected Study 38 cycles", "20 participant--muscle curves",
                   "source-verified\ndocumentary attributes", "All 20 comparisons",
                   "Restart reused stored series", "not autonomous interpretation",
                   "additional independent physiological observations",
                   "In the original physiological evaluation"):
        assert phrase in results


def test_documentary_examples_change_interpretation_not_scientific_approval():
    source = read("paper/results/document_field_join_v003_2026.09.15-r3/verification.json")
    assert source["all_CSV_samples_equal"] and source["mendeley_sampling_rate_hz"] == 200
    assert source["mendeley_records"] == 2 and source["figshare_EMG_records"] == 2
    assert not source["scientific_approval"]
    results = section(text(VERSION), "# Results\n", "# Discussion\n")
    for phrase in ("5-ms acquisition interval", "seconds, rather than milliseconds",
                   "44,776", "hand-gesture records", "25 as ordinary walking",
                   "rather than event detection", "without itself establishing permission",
                   "All 210 controlled"):
        assert phrase in results
    control = read("paper/results/additional_framework_v001_2026.09.12-r1/run4/core_summary.json")
    assert control["scope_controls_passed"] == control["scope_controls"] == 210


def test_fault_and_gui_results_do_not_claim_unmeasured_human_performance():
    fault = read("paper/results/validation/fault_detection.json")
    assert fault["faults_detected"] == fault["faults_total"] == 8
    assert fault["clean_specificity"] == 1.0
    results = section(text(VERSION), "# Results\n", "# Discussion\n")
    for phrase in ("All eight prespecified", "clean control was not flagged",
                   "same two prepared Figshare trials", "saving 16 curves",
                   "eight diagnostic comparisons", "1,616 phase-curve rows",
                   "No human confirmations", "assessments remained held",
                   "tested with synthetic cases"):
        assert phrase in results
    assert "improved user accuracy" not in results
    assert "reduced review time" not in results


def test_prior_version_supplement_and_sources_unchanged():
    for relative, expected in BASELINE.items():
        assert hashlib.sha256((ROOT / relative).read_bytes()).hexdigest() == expected, relative
    new = text(VERSION)
    for value in ("38/40", "+0.52", "+1.17", "-4.33", "+4.97", "-1.13", "+6.02",
                  "0.02085", "1.19", "130", "1,109,104"):
        assert value in new


def test_snapshot_manifest_and_figure_references():
    if (ROOT / "paper/MANUSCRIPT_VERSION").read_text().strip() != VERSION:
        import pytest
        pytest.skip("Current manuscript has advanced.")
    for suffix, current in (("md", "paper/paper.md"), ("tex", "paper/cmpb/manuscript.tex"), ("pdf", "paper/cmpb/manuscript.pdf")):
        assert (ROOT / f"paper/versions/harmo-semg_manuscript_{VERSION}.{suffix}").read_bytes() == (ROOT / current).read_bytes()
    manifest = read("paper/cmpb/manifest.json")
    assert manifest["manuscript_version"] == VERSION
    for source in manifest["sources"]:
        assert hashlib.sha256((ROOT / source["path"]).read_bytes()).hexdigest() == source["sha256"]
    assert f"{TIMING}/summary.json" in [item["path"] for item in manifest["sources"]]
    tex = (ROOT / "paper/cmpb/manuscript.tex").read_text()
    for label in ("fig:1", "fig:2", "fig:3", "fig:S1", "fig:S2", "tab:additional-evaluations"):
        assert "\\label{" + label + "}" in tex
