"""Supplementary displays retain real observations and distinct denominators."""
import hashlib
import json
from collections import Counter
from pathlib import Path
from zipfile import ZipFile

import numpy as np
import pytest

ROOT = Path(__file__).resolve().parents[1]
VERSION = "v024_2026.09.16-r12"
DIRECTORY = ROOT / "paper/figures/supplementary_v001_2026.09.16-r12"


def read(relative):
    return json.loads((ROOT / relative).read_text())


def plotted():
    return json.loads((DIRECTORY / "plot_data_v001.json").read_text())


def test_participant_curves_and_all_original_decisions():
    data = plotted()
    original = read("paper/results/validation/physiology_positive_control.json")["metrics"]
    assert len(data["physiology_curves"]) == 4040
    assert len(data["physiology_metrics"]) == len(original) == 40
    for metric, frozen in zip(data["physiology_metrics"], original):
        assert metric["study"] == frozen["study_id"]
        assert metric["subject"] == frozen["subject_id"]
        assert metric["muscle"] == frozen["muscle"]
        for key in ("pattern_pass", "peak_window_pass", "phase_contrast_pass", "cycle_count",
                    "activation_centroid_percent_gait_cycle", "peak_timing_percent_gait_cycle",
                    "log2_phase_contrast", "active_phase_mean_within_subject_scale",
                    "reference_phase_mean_within_subject_scale"):
            assert metric[key] == frozen[key]
    assert Counter(r["subject"] for r in data["physiology_metrics"] if not r["pattern_pass"]) == {"S013": 1, "S020": 1}
    verification = data["verification"]
    assert verification["all_decisions_match"] and verification["pattern_positive"] == 38
    assert verification["largest_metric_difference"] < 2.14e-14
    for relative, digest in verification["sources"].items():
        assert hashlib.sha256((ROOT / relative).read_bytes()).hexdigest() == digest


def test_all_boundary_observations_are_frozen_values():
    data = plotted()["boundary_intervals"]
    frozen = read("paper/results/segment_boundary_v003_2026.09.15-r3/validation.json")
    assert len(data) == 160 * 4
    assert Counter(r["record"] for r in data if r["method"] == "legacy") == {
        "Dual-task/S0088_DT_V1.hea": 40, "Dual-task/S0088_DT_V2.hea": 40,
        "Dual-task/S0088_DT_V3.hea": 40, "Dual-task/S0172_DT_V1.hea": 40,
    }
    for row in data:
        source = frozen["rows"][row["interval"]]
        assert all(row[key] == source[key] for key in ("record", "channel", "start", "end"))
        for key, value in source["methods"][row["method"]].items():
            assert row[key] == value
        assert row["full_cycle_shape_RMSE"] > 0


def test_counterexample_is_explicit_and_matches_original():
    data = plotted()
    verify = data["verification"]
    assert verify["participants"] == 2 and verify["records"] == 4
    assert not verify["scientific_approval"]
    assert "not a random example" in verify["example_selection"]
    curves = data["boundary_example"]
    for method, phase in (("reference", 0), ("available_sample_RMS", 40), ("real_context_0.5s", 0)):
        rows = [r for r in curves if r["method"] == method]
        assert len(rows) == 101
        assert rows[int(np.argmax([r["value"] for r in rows]))]["phase"] == phase
    source, = [r for r in read("paper/results/segment_boundary_v003_2026.09.15-r3/validation.json")["rows"]
               if r["record"] == verify["example_identity"]["record"]
               and r["channel"] == verify["example_identity"]["channel"]
               and r["start"] == verify["example_identity"]["start"]
               and r["end"] == verify["example_identity"]["end"]]
    for method, metrics in verify["example_errors"].items():
        for key, value in metrics.items():
            np.testing.assert_allclose(value, source["methods"][method][key], rtol=1e-6, atol=1e-15)


def test_table_denominators_and_boundaries():
    table = (ROOT / "paper/tables/table_s3_additional_evaluations_v001_2026.09.16-r12.md").read_text()
    assert len([line for line in table.splitlines() if line.startswith("|")]) == 9
    core = read("paper/results/additional_framework_v001_2026.09.12-r1/run4/core_summary.json")
    t3 = read("paper/results/additional_framework_v001_2026.09.12-r1/run4/T3_summary.json")
    assert core["coverage"]["file_only_study_property_pairs"] == 24
    assert core["coverage"]["paper_assisted_study_property_pairs"] == 43
    assert core["scope_controls_passed"] == 210 and core["purpose_evaluations"] == 12
    assert t3["evaluations"] == 120 and t3["exact_restorations"] == 40
    doc = read("paper/results/document_field_join_v003_2026.09.15-r3/verification.json")
    assert doc["curves"] == 16 and doc["comparisons"] == 8 and doc["human_reviews"] == 0
    assert doc["mendeley_records"] == 2 and doc["mendeley_channels_each"] == 8
    assert doc["all_CSV_samples_equal"] and not doc["scientific_approval"]
    for phrase in ("not independent datasets", "Same two Figshare trials", "two participants",
                   "not independent extraction accuracy", "not full-waveform physiology",
                   "1,616", "64 timing and 64 amplitude", "repeated trials do not add participants"):
        assert phrase in table


def test_artwork_and_captions_show_failures_and_limits():
    methods = (ROOT / f"paper/supplementary_methods_{VERSION}.md").read_text()
    assert r"$2.14 \times 10^{-14}$" in methods
    assert r"\\times" not in methods
    for stem in ("figure_s1_participant_envelopes_v001", "figure_s2_boundary_processing_v001"):
        for suffix in ("pdf", "svg", "png"):
            assert (DIRECTORY / f"{stem}.{suffix}").stat().st_size > 1000
    svg = (DIRECTORY / "figure_s1_participant_envelopes_v001.svg").read_text()
    assert "S013" in svg and "S020" in svg
    assert "exploratory threshold" not in svg
    captions = (DIRECTORY / "captions.md").read_text()
    for phrase in ("38/40", "not new observations", "precludes between-study amplitude",
                   "Previously identified counterexample", "not 160 independent samples",
                   "1%-interval grid", "universal surrounding-signal duration"):
        assert phrase in captions


def test_main_manuscript_only_adds_supplementary_references():
    current = (ROOT / f"paper/versions/harmo-semg_manuscript_{VERSION}.md").read_text()
    old = (ROOT / "paper/versions/harmo-semg_manuscript_v023_2026.09.16-r11.md").read_text()
    current = current.replace("\nSupplementary Table S3 separates the additional evaluations, their units of\nobservation, and their interpretation limits.", "")
    current = current.replace("overall (Figure 3E). Supplementary Figure S1 shows all participant envelopes,\nincluding the two pattern failures, against the predefined active intervals.", "overall (Figure 3E).")
    current = current.replace("relative to continuous-signal processing (Supplementary Figure S2).", "relative to continuous-signal processing.")
    assert current == old


def test_current_build_references_snapshots_and_sources():
    if (ROOT / "paper/MANUSCRIPT_VERSION").read_text().strip() != VERSION:
        pytest.skip("Current manuscript has advanced; immutable v024 outputs remain covered.")
    for suffix, current in (("md", "paper/paper.md"), ("tex", "paper/cmpb/manuscript.tex"), ("pdf", "paper/cmpb/manuscript.pdf")):
        assert (ROOT / f"paper/versions/harmo-semg_manuscript_{VERSION}.{suffix}").read_bytes() == (ROOT / current).read_bytes()
    tex = (ROOT / "paper/cmpb/manuscript.tex").read_text()
    for label in ("fig:S1", "fig:S2", "tab:additional-evaluations"):
        assert rf"\label{{{label}}}" in tex and rf"\ref{{{label}}}" in tex
    manifest = read("paper/cmpb/manifest.json")
    for source in manifest["sources"]:
        assert hashlib.sha256((ROOT / source["path"]).read_bytes()).hexdigest() == source["sha256"]
    with ZipFile(ROOT / "paper/cmpb/cmpb_submission.zip") as archive:
        for name in ("FigS1.pdf", "FigS2.pdf"):
            assert archive.read(name) == (ROOT / "paper/cmpb" / name).read_bytes()
