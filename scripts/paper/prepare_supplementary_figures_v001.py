"""Recover plotted observations without changing frozen experiments or rules."""
from __future__ import annotations

import hashlib
import json
from pathlib import Path

import numpy as np

from harmo.footswitch_timing import envelope as continuous_envelope, peak
from harmo.physiology_control import (
    _normalise_cycle, _subject_metric, load_physiology_protocol,
)
from harmo.segment_envelope import envelope, features, phase_support

ROOT = Path(__file__).resolve().parents[2]
OUT = ROOT / "paper/figures/supplementary_v001_2026.09.16-r12"
METHODS = ("legacy", "available_sample_RMS", "real_context_0.125s", "real_context_0.5s")


def read(relative):
    return json.loads((ROOT / relative).read_text())


def digest(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def prepare():
    sources = {}

    def register(path):
        sources[str(path.relative_to(ROOT))] = digest(path)

    original_path = ROOT / "paper/results/validation/physiology_positive_control.json"
    original = json.loads(original_path.read_text())
    protocol_path = ROOT / "paper/physiology_positive_control_protocol.yaml"
    protocol = load_physiology_protocol(protocol_path)
    register(original_path)
    register(protocol_path)
    curves, metrics = [], []
    largest_difference = 0.0
    numeric = ("activation_centroid_percent_gait_cycle", "peak_timing_percent_gait_cycle",
               "log2_phase_contrast", "active_phase_mean_within_subject_scale",
               "reference_phase_mean_within_subject_scale")
    decisions = ("pattern_pass", "peak_window_pass", "phase_contrast_pass")
    base = ROOT / "paper/results/registered_reuse_clean_v001_2026.09.13-r1/timing-001/resolution/source-contracts"
    for metric in original["metrics"]:
        study, subject, muscle = (metric[k] for k in ("study_id", "subject_id", "muscle"))
        path = base / study / subject / "source.subject-curve.csv"
        register(path)
        table = np.genfromtxt(path, delimiter=",", names=True)
        values = table["TA" if muscle == "tibialis anterior" else "GM"]
        assert values.shape == (101,) and np.isfinite(values).all()
        check = _subject_metric({**metric, "values": values.tolist()}, protocol["muscles"][muscle])
        for key in numeric:
            delta = abs(check[key] - metric[key])
            largest_difference = max(largest_difference, delta)
            assert delta < 1e-10, (study, subject, muscle, key, delta)
        assert all(check[key] == metric[key] for key in decisions)
        identity = dict(study=study, subject=subject, muscle=muscle)
        metrics.append({**identity, **{key: metric[key] for key in (*numeric, *decisions, "cycle_count")}})
        curves.extend({**identity, "phase": i, "value": float(value),
                       "pattern_pass": metric["pattern_pass"]}
                      for i, value in enumerate(values))
    assert len(metrics) == 40 and sum(m["pattern_pass"] for m in metrics) == 38

    boundary_path = ROOT / "paper/results/segment_boundary_v003_2026.09.15-r3/validation.json"
    boundary = json.loads(boundary_path.read_text())
    register(boundary_path)
    rows = boundary["rows"]
    assert len(rows) == 160 and len({r["record"] for r in rows}) == 4
    # Explicitly choose the previously reported counterexample, not a best-fitting trace.
    example, = [r for r in rows if r["record"] == "Dual-task/S0172_DT_V1.hea"
                and r["channel"] == "RT LAT. GASTRO." and r["start"] == 98941
                and r["end"] == 100725]
    plan_path = ROOT / "paper/results/general_batch_fresh_v001_2026.09.15-r1/job-000/attempt-003/plan.json"
    assert digest(plan_path) == boundary["inputs"][str(plan_path)]
    register(plan_path)
    plan = json.loads(plan_path.read_text())
    record, = [r for r in plan["records"][:4] if r["record"] == example["record"]]
    source = Path(record["row"][3]) / "normalized.npz"
    assert digest(source) == record["samples_sha256"] == boundary["inputs"][str(source)]
    register(source)
    channel, = [c for c in record["channels"] if c["channel"] == example["channel"]]
    with np.load(source, allow_pickle=False) as data:
        x = data["values"][:, channel["index"]]
    fs, start, end = record["sampling_rate_hz"], example["start"], example["end"]
    expected = _normalise_cycle(continuous_envelope(x, fs, plan["profile"])[start:end + 1], 101)
    cropped = x[start:end + 1]
    corrected, metadata = envelope(cropped, fs, plan["profile"])
    supported = phase_support(metadata, len(cropped))
    variants = {
        "legacy": _normalise_cycle(continuous_envelope(cropped, fs, plan["profile"]), 101),
        "available_sample_RMS": _normalise_cycle(corrected, 101),
    }
    for seconds in (.125, .5):
        pad = int(round(seconds * fs))
        assert start >= pad and end + pad < len(x)
        surrounding = continuous_envelope(x[start - pad:end + pad + 1], fs, plan["profile"])[pad:-pad]
        variants[f"real_context_{seconds}s"] = _normalise_cycle(surrounding, 101)
    example_errors = {}
    for method, y in variants.items():
        measured = dict(full_cycle_shape_RMSE=float(np.sqrt(np.mean((y - expected) ** 2))),
                        global_peak_error_percent=abs((peak(y) - peak(expected) + 50) % 100 - 50),
                        common_supported_shape_RMSE=float(np.sqrt(np.mean(
                            (y[supported] / max(y[supported]) - expected[supported] / max(expected[supported])) ** 2))))
        for key, value in measured.items():
            # Relative comparison also tests the near-machine-precision context result.
            np.testing.assert_allclose(value, example["methods"][method][key], rtol=1e-6, atol=1e-15)
        example_errors[method] = measured
    reconstructed = features(variants["available_sample_RMS"], supported)
    assert peak(expected) == example["reference_peak_percent"] == 0
    assert reconstructed["single_peak_percent"] == example["corrected_features"]["single_peak_percent"] == 40
    variants["reference"] = expected
    waveform = [dict(method=method, phase=i, value=float(value))
                for method, values in variants.items() for i, value in enumerate(values)]
    intervals = []
    for index, row in enumerate(rows):
        for method in METHODS:
            intervals.append(dict(interval=index, record=row["record"], channel=row["channel"],
                                  start=row["start"], end=row["end"], method=method,
                                  **row["methods"][method]))
    for method in METHODS:
        values = [r["full_cycle_shape_RMSE"] for r in intervals if r["method"] == method]
        assert min(values) > 0  # The logarithmic axis needs no substitution or floor.
        assert np.median(values) == boundary["methods"][method]["full_cycle_shape_RMSE"]["median"]
    result = dict(
        physiology_curves=curves, physiology_metrics=metrics,
        active_windows={key: value["active_windows_percent"] for key, value in protocol["muscles"].items()},
        boundary_intervals=intervals, boundary_example=waveform,
        verification=dict(physiology_curves=40, pattern_positive=38, all_decisions_match=True,
            largest_metric_difference=largest_difference, boundary_intervals=160, records=4, participants=2,
            example_selection="Previously reported interior-peak counterexample; not a random example.",
            example_identity={k: example[k] for k in ("record", "channel", "start", "end", "fs")},
            example_errors=example_errors, reference_peak_percent=0, provisional_peak_percent=40,
            scientific_approval=False, sources=sources),
    )
    OUT.mkdir(parents=True, exist_ok=True)
    (OUT / "plot_data_v001.json").write_text(json.dumps(result, indent=2) + "\n")
    (OUT / "verification_v001.json").write_text(json.dumps(result["verification"], indent=2) + "\n")
    print(json.dumps({k: v for k, v in result["verification"].items() if k != "sources"}, indent=2))


if __name__ == "__main__":
    prepare()
