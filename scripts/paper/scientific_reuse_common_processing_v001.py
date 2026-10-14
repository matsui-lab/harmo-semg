"""Paired common-processing contrasts, frozen before novel outcome calculation."""
import argparse
from collections import defaultdict
import io
import json
from pathlib import Path
import platform
import sys
import zipfile

import numpy as np
import scipy
from scipy import signal
from scipy.io import loadmat

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "src"))
sys.path.insert(0, str(Path(__file__).parent))
from check_scientific_reuse_readiness_v001 import (
    ASSETS, NUMERIC, DECISIONS, close, digests, protected_inputs, saved_curve,
)
from check_gait120_raw_alignment_v001 import sample_interval, field, integers, CODE_SHA
from harmo.physiology_control import (
    _hampel, _normalise_cycle, _subject_metric, _numeric_mat_value,
    canonical_gait120_channel, extract_study38_rows, preprocess_study38_cycle,
    load_physiology_protocol,
)
from harmo.ingest.plugins.mat_mcos_explicit import _expand_template

PLAN = Path("paper/revision_plans/scientific_reuse_protocol_v003_2026.09.28-r3.md")
ALIGNMENT = Path("paper/results/gait120_raw_alignment_v001_2026.09.28-r1")
CONDITIONS = ("existing", "common_trial", "common_cycle")
MUSCLES = ("tibialis anterior", "medial gastrocnemius")
CONTRASTS = {"cycle_minus_existing": (2, 0), "trial_minus_existing": (1, 0),
             "cycle_minus_trial": (2, 1)}
CENTROID = "activation_centroid_percent_gait_cycle"
PEAK = "peak_timing_percent_gait_cycle"


def common_envelope(values, fs, settings):
    """Existing Lencioni preprocessing, with normalization delayed until cutting."""
    raw = np.asarray(values, dtype=float)
    if raw.ndim != 1 or raw.size < 16 or not np.isfinite(raw).all():
        raise ValueError("invalid raw signal")
    low, high = settings["bandpass_hz"]
    if not 0 < low < high < fs / 2:
        raise ValueError("invalid bandpass")
    centered = raw - float(np.mean(raw)) if settings["mean_center"] else raw
    sos = signal.butter(int(settings["butterworth_order"]), [low, high],
                        btype="bandpass", fs=fs, output="sos")
    rectified = np.abs(signal.sosfiltfilt(sos, centered))
    width = max(3, int(round(settings["hampel_window_seconds"] * fs)))
    if width % 2 == 0:
        width += 1
    cleaned = _hampel(rectified, width, settings["hampel_sigma"])
    width = max(1, int(round(settings["rms_window_seconds"] * fs)))
    if width > len(cleaned):
        raise ValueError("signal shorter than RMS window")
    return np.sqrt(np.convolve(cleaned * cleaned, np.ones(width) / float(width), mode="same"))


def cut_normalize(envelope, start, stop):
    if not 0 <= start < stop <= len(envelope):
        raise ValueError("invalid slice")
    return _normalise_cycle(envelope[start:stop], 101)


def summarize(left, right, iterations=100000, seed=20260928):
    """Left n x muscles; right n x 3 conditions x muscles; paired within study."""
    left, right = np.asarray(left, float), np.asarray(right, float)
    if (left.ndim != 2 or right.ndim != 3 or right.shape[1:] != (3, left.shape[1])
            or min(len(left), len(right)) < 2 or not np.isfinite(left).all()
            or not np.isfinite(right).all()):
        raise ValueError("invalid participant arrays")
    rng = np.random.Generator(np.random.PCG64(seed))
    li = rng.integers(0, len(left), (iterations, len(left)))
    ri = rng.integers(0, len(right), (iterations, len(right)))
    lmed, rmed = np.median(left[li], axis=1), np.median(right[ri], axis=1)
    draws = rmed - lmed[:, None, :]
    d = np.median(right, axis=0) - np.median(left, axis=0)[None, :]
    ci = lambda x: np.quantile(x, [0.025, 0.975], axis=0, method="linear").T.tolist()
    result = {
        "lencioni_centroid_medians": np.median(left, axis=0).tolist(),
        "gait120_centroid_medians": np.median(right, axis=0).tolist(),
        "study_differences": d.tolist(),
        "study_difference_ci": [ci(draws[:, k, :]) for k in range(3)], "contrasts": {},
    }
    for name, (a, b) in CONTRASTS.items():
        paired = right[:, a] - right[:, b]
        result["contrasts"][name] = {
            "change_in_study_difference": (d[a] - d[b]).tolist(),
            "change_ci": ci(draws[:, a] - draws[:, b]),
            "median_participant_change": np.median(paired, axis=0).tolist(),
            "median_participant_change_ci": ci(np.median(paired[ri], axis=1)),
        }
    return result


def run(out):
    if out.exists():
        raise FileExistsError(out)
    before = protected_inputs(ROOT)
    inputs = {str(PLAN): digests(ROOT / PLAN)["sha256"]}
    for name in ("report.json", "Matlab_Codes_for_Processing_source.zip"):
        inputs[str(ALIGNMENT / name)] = digests(ROOT / ALIGNMENT / name)["sha256"]
    if inputs[str(ALIGNMENT / "Matlab_Codes_for_Processing_source.zip")] != CODE_SHA:
        raise ValueError("source code snapshot mismatch")
    alignment = json.loads((ROOT / ALIGNMENT / "report.json").read_text())
    mapping = {(r["participant"], r["cycle"]): r for r in alignment["cycles"]}
    if len(mapping) != 94 or len(mapping) != len(alignment["cycles"]):
        raise ValueError("unexpected mapping population")
    protocol = load_physiology_protocol(ROOT / "paper/physiology_positive_control_protocol.yaml")
    settings = protocol["study_38_envelope"]
    original = json.loads((ROOT / "paper/results/validation/physiology_positive_control.json").read_text())
    old = {(r["study_id"], r["subject_id"], r["muscle"]): r for r in original["metrics"]}
    expected = {(study, str(sid), m) for study, spec in protocol["required_studies"].items()
                for sid in spec["subjects"] for m in MUSCLES}
    if len(old) != 40 or len(original["metrics"]) != 40 or set(old) != expected:
        raise ValueError("unexpected baseline population")
    for row in old.values():
        values, path = saved_curve(ROOT, row)
        inputs[str(path.relative_to(ROOT))] = digests(path)["sha256"]
        check = _subject_metric({**row, "values": values}, protocol["muscles"][row["muscle"]])
        if not all(close(check[k], row[k]) for k in NUMERIC) or not all(check[k] == row[k] for k in DECISIONS):
            raise ValueError("baseline metrics mismatch")
    manifest = {x["name"]: x for x in json.loads((ROOT / ASSETS / "source_verification.json").read_text())}
    assets = []
    for study, files in protocol["source_assets"].items():
        for name, spec in files.items():
            path = ROOT / ASSETS / name
            identity = {"name": name, "bytes": path.stat().st_size, **digests(path)}
            if any(identity[k] != spec[k] for k in ("bytes", "md5")) or identity["sha256"] != manifest[name]["sha256"]:
                raise ValueError("source identity mismatch")
            assets.append(identity)
    left_rows, _ = extract_study38_rows(ROOT / ASSETS, protocol)
    baseline_checks = []
    for row in left_rows:
        saved, _ = saved_curve(ROOT, row)
        if not close(row["values"], saved):
            raise ValueError("Lencioni source baseline mismatch")
        baseline_checks.append(float(np.max(np.abs(row["values"] - saved))))
    print("Reproduced 20 Lencioni participant-muscle curves from raw sources", flush=True)
    records, cycle_checks, seen, trial_ids = [], [], set(), set()
    with zipfile.ZipFile(ROOT / ASSETS / "Gait120_011_to_020.zip") as z:
        for sid in protocol["required_studies"]["study_120"]["subjects"]:
            raw = loadmat(io.BytesIO(z.read(f"{sid}/EMG/RawData.mat")))
            processed = loadmat(io.BytesIO(z.read(f"{sid}/EMG/ProcessedData.mat")))
            fs, = integers(field(raw, "EMGs_info.fs"))
            marker_fs, = integers(field(raw, "Markers_info.fs"))
            trial_list = integers(field(raw, "LevelWalking.AvailableTrialIdx"))
            if not np.array_equal(trial_list, integers(field(processed, "LevelWalking.AvailableTrialIdx"))):
                raise ValueError("trial set mismatch")
            curves = defaultdict(list)
            for tid in trial_list:
                trial = f"LevelWalking.Trial{tid:02d}"
                trial_ids.add((sid, trial))
                data, labels = _numeric_mat_value(raw, trial + ".EMGs_raw")
                if labels is None or len(set(labels)) != 12 or data.shape[1] != 12 or not np.isfinite(data).all():
                    raise ValueError("invalid raw channel table")
                canonical = [canonical_gait120_channel(x) for x in labels]
                if len(set(canonical)) != 12:
                    raise ValueError("ambiguous canonical labels")
                channels = {m: canonical.index(m) for m in MUSCLES}
                trial_env = {m: common_envelope(data[:, ch], fs, settings) for m, ch in channels.items()}
                total = integers(field(raw, trial + ".TotalFrame"))
                n_steps, = integers(field(raw, trial + ".nSteps"))
                for step in range(1, n_steps + 1):
                    prefix = trial + f".Step{step:02d}"
                    identity = (sid, prefix)
                    if identity in seen:
                        raise ValueError("duplicate cycle")
                    seen.add(identity)
                    target = integers(field(raw, prefix + ".TargetFrame"))
                    start, stop = sample_interval(total, target, fs, marker_fs, len(data))
                    ref = mapping[identity]
                    if ((start, stop, int(fs), int(marker_fs)) != (ref["python_start"], ref["python_stop_exclusive"], ref["fs"], ref["marker_fs"])
                            or labels != ref["source_labels"] or len(data) != ref["raw_trial_samples"]):
                        raise ValueError("mapping changed")
                    published, pub_labels = _numeric_mat_value(processed, prefix + ".EMGs_interpolated")
                    if pub_labels != labels or published.shape != (101, 12):
                        raise ValueError("published channel mismatch")
                    for muscle, ch in channels.items():
                        segment = data[start:stop, ch]
                        segment_curve = _normalise_cycle(common_envelope(segment, fs, settings), 101)
                        legacy = preprocess_study38_cycle(segment, fs, settings)
                        if not close(segment_curve, legacy):
                            raise ValueError("common cycle differs from frozen function")
                        values = (_normalise_cycle(published[:, ch]),
                                  cut_normalize(trial_env[muscle], start, stop), segment_curve)
                        for condition, curve in zip(CONDITIONS, values):
                            curves[(muscle, condition)].append(curve)
                        cycle_checks.append({"participant": sid, "cycle": prefix, "muscle": muscle,
                                             "max_implementation_difference": float(np.max(np.abs(segment_curve - legacy))),
                                             "left_context_seconds": start / float(fs),
                                             "right_context_seconds": (len(data) - stop) / float(fs)})
            patterns = {"trial": r"Trial\d+", "step": r"Step\d+"}
            keys = {k.rsplit(".", 1)[0] for k, _ in _expand_template(processed, "LevelWalking.{trial}.{step}.EMGs_interpolated", patterns)}
            raw_keys = {k.rsplit(".", 1)[0] for k, _ in _expand_template(raw, "LevelWalking.{trial}.{step}.TargetFrame", patterns)}
            if keys != raw_keys or keys != {k for s, k in seen if s == sid}:
                raise ValueError("unmatched cycle population")
            for muscle in MUSCLES:
                base = old[("study_120", sid, muscle)]
                subject_curves = {c: np.median(curves[(muscle, c)], axis=0) for c in CONDITIONS}
                saved, _ = saved_curve(ROOT, base)
                if not close(subject_curves["existing"], saved):
                    raise ValueError("Gait120 published baseline mismatch")
                baseline_checks.append(float(np.max(np.abs(subject_curves["existing"] - saved))))
                metrics = {c: _subject_metric({**base, "values": subject_curves[c]}, protocol["muscles"][muscle]) for c in CONDITIONS}
                if any(m["outcome"] != "assessed" for m in metrics.values()):
                    raise ValueError("unassessed participant")
                contrasts = {}
                for name, (a, b) in CONTRASTS.items():
                    ca, cb = CONDITIONS[a], CONDITIONS[b]
                    contrasts[name] = {
                        "centroid_change_pp": metrics[ca][CENTROID] - metrics[cb][CENTROID],
                        "peak_change_pp": metrics[ca][PEAK] - metrics[cb][PEAK],
                        "curve_rmse": float(np.sqrt(np.mean((subject_curves[ca] - subject_curves[cb]) ** 2))),
                        "pattern_changed": metrics[ca]["pattern_pass"] != metrics[cb]["pattern_pass"],
                    }
                records.append({"participant": sid, "muscle": muscle, "cycles": len(keys),
                                "curves": {c: v.tolist() for c, v in subject_curves.items()},
                                "metrics": metrics, "contrasts": contrasts})
            print(f"Validated {sid}: {len(keys)} cycles, three conditions, both muscles", flush=True)
    if seen != set(mapping) or len(trial_ids) != 50 or len(cycle_checks) != 188 or len(records) != 20:
        raise ValueError("analysis population changed")
    left_table = {(r["subject_id"], r["muscle"]): _subject_metric(r, protocol["muscles"][r["muscle"]]) for r in left_rows}
    table = {(r["participant"], r["muscle"]): r for r in records}
    left = [[left_table[(str(sid), m)][CENTROID] for m in MUSCLES] for sid in protocol["required_studies"]["study_38"]["subjects"]]
    right = [[[table[(sid, m)]["metrics"][c][CENTROID] for m in MUSCLES] for c in CONDITIONS]
             for sid in protocol["required_studies"]["study_120"]["subjects"]]
    summary = summarize(left, right)
    if before != protected_inputs(ROOT) or any(digests(ROOT / p)["sha256"] != h for p, h in inputs.items()):
        raise ValueError("protected input changed")
    result = {
        "status": "complete", "analysis": "scientific_reuse_common_processing_v001",
        "conditions": CONDITIONS, "muscles": MUSCLES, "summary": summary,
        "gait120_participants": records, "lencioni_curves": left_rows,
        "checks": {"baseline_curves": len(baseline_checks), "baseline_max_difference": max(baseline_checks),
                   "gait120_cycles": len(seen), "gait120_trials": len(trial_ids),
                   "gait120_cycle_muscle_implementation_checks": len(cycle_checks),
                   "implementation_max_difference": max(x["max_implementation_difference"] for x in cycle_checks),
                   "protected_inputs_unchanged": True},
        "cycle_checks": cycle_checks, "source_assets": assets,
        "inputs": inputs, "protected_inputs": before,
        "bootstrap": {"iterations": 100000, "seed": 20260928, "rng": "PCG64", "unit": "participant"},
        "environment": {"python": platform.python_version(), "numpy": np.__version__, "scipy": scipy.__version__},
        "analysis_sources": {str(p.relative_to(ROOT)): digests(p)["sha256"] for p in
                             [Path(__file__), ROOT / "src/harmo/physiology_control.py",
                              ROOT / "scripts/paper/check_gait120_raw_alignment_v001.py",
                              ROOT / "scripts/paper/check_scientific_reuse_readiness_v001.py"]},
        "limits": ["Exploratory selected cohort, not independent biological ground truth.",
                   "Common-cycle comparison changes preprocessing and scope jointly; intermediate condition separates contrasts, not individual filters.",
                   "Lencioni continuous context is unavailable; recording conditions and populations are not matched.",
                   "No amplitude comparability, equivalence or P0/P1 information effect established."],
    }
    names = ("既存比較", "共通処理・試行単位", "共通処理・周期単位")
    fmt = lambda x, ci: f"{x:+.3f} [{ci[0]:+.3f}, {ci[1]:+.3f}]"
    lines = ["# 共通処理・処理単位による研究間タイミング差", "", "計画v003に従う探索的対照。正式原稿は保持。研究間差＝Gait120−Lencioni、単位は歩行周期パーセントポイント。", "", "| 条件 | 前脛骨筋の研究間差［95%区間］ | 腓腹筋内側頭の研究間差［95%区間］ | Gait120パターン適合（前脛骨筋／腓腹筋） |", "|---|---:|---:|---:|"]
    for k, c in enumerate(CONDITIONS):
        cells = [fmt(summary["study_differences"][k][j], summary["study_difference_ci"][k][j]) for j in range(2)]
        passes = [sum(r["metrics"][c]["pattern_pass"] for r in records if r["muscle"] == m) for m in MUSCLES]
        lines.append(f"| {names[k]} | {cells[0]} | {cells[1]} | {passes[0]}/10 ／ {passes[1]}/10 |")
    lines += ["", "Lencioni側は全条件で同じ50周期・2筋の原信号処理を使用。Gait120側は同じ94周期・2筋を使用。ブートストラップは研究別の独立な参加者再抽出100,000回とし、条件間・筋間の対応を保持した。", "", "| 条件の変更 | 前脛骨筋・研究間差の変化［95%区間］ | 腓腹筋・研究間差の変化［95%区間］ |", "|---|---:|---:|"]
    for key, (a, b) in CONTRASTS.items():
        x = summary["contrasts"][key]
        cells = [fmt(x["change_in_study_difference"][j], x["change_ci"][j]) for j in range(2)]
        lines.append(f"| {names[b]} → {names[a]} | {cells[0]} | {cells[1]} |")
    lines += ["", "## 全参加者の重心・ピークと判定", "", "| 参加者 | 筋 | 重心（既存／試行／周期） | ピーク（既存／試行／周期） | パターン適合（既存／試行／周期） |", "|---|---|---|---|---|"]
    for r in records:
        vals = lambda key, fmt: " / ".join(format(r["metrics"][c][key], fmt) for c in CONDITIONS)
        passes = " / ".join("適合" if r["metrics"][c]["pattern_pass"] else "不適合" for c in CONDITIONS)
        lines.append(f"| {r['participant']} | {r['muscle']} | {vals(CENTROID, '.2f')} | {vals(PEAK, '.0f')} | {passes} |")
    lines += ["", "## 解釈の範囲", "", "共通周期処理と既存比較の差は、処理方法と処理単位の両方の変更を含む。試行単位条件との対照で、同じ処理が参照する範囲による影響を区別する。どの処理を真値ともせず、差が小さくなることを成功としない。提供されていないLencioniの前後信号、研究間の参加者背景・記録条件の相違、振幅の比較可能性は解決していない。", "", "40保存曲線は原入力から再集約して再現した。Gait120の188周期・筋では共通周期処理が既存関数と絶対差1e-10以内で一致した。正式原稿、図、旧結果、入力曲線は変更していない。"]
    out.mkdir(parents=True, exist_ok=False)
    (out / "report.json").write_text(json.dumps(result, ensure_ascii=False, indent=2, allow_nan=False) + "\n")
    (out / "REPORT.md").write_text("\n".join(lines) + "\n")
    print(json.dumps({"checks": result["checks"], "summary": summary}, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    run(args.output if args.output.is_absolute() else ROOT / args.output)
