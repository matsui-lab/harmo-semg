"""Figure 3 displays signed median contrasts and post hoc bootstrap intervals."""
import csv
import hashlib
import importlib.util
import json
from pathlib import Path
from statistics import median
from zipfile import ZipFile
import xml.etree.ElementTree as ET
import numpy as np

ROOT = Path(__file__).resolve().parents[1]
BASE = ROOT / "paper/figures/candidates"
OLD = "figure3_integrated_results_v007_2026.09.16-r7"
NEW = "figure3_integrated_results_v008_2026.09.16-r8"
SUMMARY = ROOT / "paper/results/centroid_bootstrap_v001_2026.09.16-r1/summary.json"

def test_bootstrap_reproducibility_and_source():
    spec = importlib.util.spec_from_file_location("centroid_bootstrap", ROOT / "scripts/paper/bootstrap_centroid_difference_v001.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    source = json.loads(module.SOURCE.read_text())
    summary = json.loads(SUMMARY.read_text())
    reproduced = module.calculate(source)
    assert reproduced["estimates"] == summary["estimates"]
    assert reproduced["bootstrap_replicates_sha256"] == summary["bootstrap_replicates_sha256"]
    assert summary["independent_study_resampling"] and summary["shared_subject_draws_across_muscles"]
    assert summary["resamples"] == 100000 and summary["seed"] == 20260916
    assert summary["pattern_failures_retained"] == 2
    for item in summary["sources"]:
        assert hashlib.sha256((ROOT / item["path"]).read_bytes()).hexdigest() == item["sha256"]
    with (BASE / NEW / f"{NEW}_E_median_differences.csv").open() as stream:
        plotted = list(csv.DictReader(stream))
    for j, (result, row) in enumerate(zip(summary["estimates"], plotted)):
        values = [[r["activation_centroid_percent_gait_cycle"] for r in source["metrics"]
                   if r["muscle"] == result["muscle"] and r["study_id"] == study]
                  for study in ("study_38", "study_120")]
        assert all(len(v)==10 for v in values)
        assert result["signed_median_difference_pp"] == median(values[1]) - median(values[0])
        assert round(result["signed_median_difference_pp"],2) == (0.52,1.17)[j]
        assert tuple(round(result[k],2) for k in ("ci_lower_pp","ci_upper_pp")) == ((-4.33,4.97),(-1.13,6.02))[j]
        for k in ("signed_median_difference_pp","ci_lower_pp","ci_upper_pp"):
            assert abs(float(row[k]) - result[k]) < 1e-10

def test_original_observations_and_panels_unchanged():
    for suffix in ("A_data.csv","B_data.csv","C_data.csv","D_data.csv","D_table.json","E_data.csv"):
        assert (BASE / NEW / f"{NEW}_{suffix}").read_bytes() == (BASE / OLD / f"{OLD}_{suffix}").read_bytes()
    assert hashlib.sha256((ROOT / "paper/physiology_positive_control_protocol.yaml").read_bytes()).hexdigest() == "9682b70ae5a1fec55e247ac74f9c4249e58a1249f764e3ba2dfb4ead57771267"
    evidence = json.loads((BASE / NEW / f"{NEW}_evidence.json").read_text())
    assert evidence["boundaries"]["additional_post_hoc_analysis"]
    assert not evidence["boundaries"]["manuscript_changed"]
    assert evidence["checks"]["panels"] == list("ABCDE")
    for item in evidence["sources"]:
        assert hashlib.sha256((ROOT / item["path"]).read_bytes()).hexdigest() == item["sha256"]

def test_editable_plot_no_threshold_and_caption():
    ns={"p":"http://schemas.openxmlformats.org/presentationml/2006/main",
        "a":"http://schemas.openxmlformats.org/drawingml/2006/main"}
    with ZipFile(BASE / NEW / f"{NEW}_editable.pptx") as archive:
        slide=ET.fromstring(archive.read("ppt/slides/slide1.xml"))
    texts=[t.text or "" for t in slide.findall(".//a:t",ns)]
    joined=" ".join(texts)
    assert "95% bootstrap CI" in joined
    assert "Gait120 - Study 38" in joined
    assert "+0.52" in joined and "+1.17" in joined
    assert "threshold" not in joined.lower() and "Absolute difference" not in joined
    assert "F" not in texts
    assert len(slide.findall(".//a:tbl",ns))==1 and not slide.findall(".//p:pic",ns)
    caption=(BASE / NEW / f"{NEW}_caption.md").read_text()
    assert "post hoc analysis" in caption and "100,000" in caption
    assert "Intervals\ncontaining zero do not establish equivalence" in caption
