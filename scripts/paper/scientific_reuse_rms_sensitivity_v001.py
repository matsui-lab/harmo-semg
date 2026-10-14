"""Frozen, one-factor RMS boundary sensitivity; never edits formal results."""
from __future__ import annotations

import argparse
import json
from pathlib import Path
import platform
import sys

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
from harmo.physiology_control import (
    _hampel, _mat_string, _normalise_cycle, _subject_metric,
    load_physiology_protocol, preprocess_study38_cycle,
)

PLAN = Path("paper/revision_plans/scientific_reuse_protocol_v002_2026.09.28-r2.md")
CONDITIONS = ("fixed_window", "observed_samples")
CENTROID = "activation_centroid_percent_gait_cycle"
PEAK = "peak_timing_percent_gait_cycle"
MUSCLES = ("tibialis anterior", "medial gastrocnemius")
SEED, ITERATIONS = 20260928, 100000


def rms_pair(values, window):
    """Identical numerator, alignment and window; only denominator differs."""
    x = np.asarray(values, dtype=float)
    if x.ndim != 1 or not np.isfinite(x).all() or not 1 <= window <= x.size:
        raise ValueError("RMS requires finite 1D signal and window no longer than signal")
    kernel = np.ones(window, dtype=float) / float(window)
    power = np.convolve(x * x, kernel, mode="same")
    fraction = np.convolve(np.ones(x.size), kernel, mode="same")
    return np.sqrt(power), np.sqrt(power / fraction)


def cycle_pair(values, fs, settings):
    raw = np.asarray(values, dtype=float).reshape(-1)
    if raw.size < 16 or not np.isfinite(raw).all():
        raise ValueError("invalid raw cycle")
    low, high = settings["bandpass_hz"]
    if not 0 < low < high < fs / 2:
        raise ValueError("invalid bandpass")
    centered = raw - np.mean(raw) if settings["mean_center"] else raw
    sos = signal.butter(settings["butterworth_order"], [low, high],
                        btype="bandpass", fs=fs, output="sos")
    rectified = np.abs(signal.sosfiltfilt(sos, centered))
    hampel_window = max(3, int(round(settings["hampel_window_seconds"] * fs)))
    if hampel_window % 2 == 0:
        hampel_window += 1
    cleaned = _hampel(rectified, hampel_window, settings["hampel_sigma"])
    window = max(1, int(round(settings["rms_window_seconds"] * fs)))
    return tuple(_normalise_cycle(v, settings["normalized_grid_points"])
                 for v in rms_pair(cleaned, window))


def interval(samples):
    return np.quantile(samples, [0.025, 0.975], axis=0, method="linear").T.tolist()


def bootstrap(left, right, iterations=ITERATIONS, seed=SEED):
    """left: participant x condition x muscle; right: participant x muscle."""
    left, right = np.asarray(left, dtype=float), np.asarray(right, dtype=float)
    if (left.ndim != 3 or left.shape[1] != 2 or right.ndim != 2
            or left.shape[2] != right.shape[1] or min(len(left), len(right)) < 2
            or not np.isfinite(left).all() or not np.isfinite(right).all()):
        raise ValueError("invalid participant arrays")
    rng = np.random.Generator(np.random.PCG64(seed))
    li = rng.integers(0, len(left), (iterations, len(left)))
    ri = rng.integers(0, len(right), (iterations, len(right)))
    lmed = np.median(left[li], axis=1)
    rmed = np.median(right[ri], axis=1)
    differences = rmed[:, None, :] - lmed
    change = differences[:, 1, :] - differences[:, 0, :]
    participant_change = np.median((left[:, 1, :] - left[:, 0, :])[li], axis=1)
    point = np.median(right, axis=0)[None, :] - np.median(left, axis=0)
    return {
        "study_difference": point.tolist(),
        "study_difference_ci": [interval(differences[:, k, :]) for k in range(2)],
        "change_in_study_difference": (point[1] - point[0]).tolist(),
        "change_in_study_difference_ci": interval(change),
        "median_within_participant_change": np.median(left[:, 1] - left[:, 0], axis=0).tolist(),
        "median_within_participant_change_ci": interval(participant_change),
    }


def run(root, out):
    if out.exists():
        raise FileExistsError(out)
    protected = protected_inputs(root)
    plan_hash = digests(root / PLAN)["sha256"]
    protocol = load_physiology_protocol(root / "paper/physiology_positive_control_protocol.yaml")
    old = json.loads((root / "paper/results/validation/physiology_positive_control.json").read_text())
    old_rows = {(x["study_id"], x["subject_id"], x["muscle"]): x for x in old["metrics"]}
    expected = {(study, str(sid), m) for study, spec in protocol["required_studies"].items()
                for sid in spec["subjects"] for m in MUSCLES}
    if len(old_rows) != len(old["metrics"]) or set(old_rows) != expected:
        raise ValueError("unexpected baseline population")
    registered = {x["name"]: x for x in json.loads((root / ASSETS / "source_verification.json").read_text())}
    assets, curve_inputs = [], {}
    for study, files in protocol["source_assets"].items():
        for name, spec in files.items():
            path = root / ASSETS / name
            observed = {"name": name, "study": study, "bytes": path.stat().st_size, **digests(path)}
            if (observed["bytes"] != spec["bytes"] or observed["md5"] != spec["md5"]
                    or observed["sha256"] != registered[name]["sha256"]):
                raise ValueError(f"source identity mismatch: {name}")
            assets.append(observed)
    for row in old_rows.values():
        curve, path = saved_curve(root, row)
        curve_inputs[str(path.relative_to(root))] = digests(path)["sha256"]
        check = _subject_metric({**row, "values": curve.tolist()}, protocol["muscles"][row["muscle"]])
        if not all(close(check[k], row[k]) for k in NUMERIC) or not all(check[k] == row[k] for k in DECISIONS):
            raise ValueError("saved baseline metric mismatch")

    records, cycle_checks, participant_checks = [], [], []
    settings = protocol["study_38_envelope"]
    for sid in protocol["required_studies"]["study_38"]["subjects"]:
        source = loadmat(root / ASSETS / f"Subject{sid}.mat", squeeze_me=True, struct_as_record=False)["s"]
        fs, side = float(source.EMGFreq), _mat_string(source.EMGSide)
        labels = [_mat_string(x) for x in np.atleast_1d(source.EmgVarName)]
        if len(labels) != len(set(labels)):
            raise ValueError("duplicate channel labels")
        selected = [(i, x) for i, x in enumerate(np.atleast_1d(source.Data).ravel())
                    if _mat_string(x.Task) == "Walking" and _mat_string(x.Foot) == side][:5]
        if len(selected) != 5:
            raise ValueError("missing same-side cycles")
        for muscle in MUSCLES:
            index = labels.index(protocol["muscles"][muscle]["study_38_label"])
            paired = []
            for i, cycle in selected:
                emg = np.asarray(cycle.EMG, dtype=float)
                if emg.ndim != 2 or emg.shape[0] != len(labels):
                    raise ValueError("invalid channel orientation")
                raw = emg[index]
                pair = cycle_pair(raw, fs, settings)
                baseline = preprocess_study38_cycle(raw, fs, settings)
                if not close(pair[0], baseline):
                    raise ValueError("cycle does not reproduce existing implementation")
                cycle_checks.append({"participant": str(sid), "muscle": muscle,
                                     "source_index": i, "samples": len(raw),
                                     "max_difference": float(np.max(np.abs(pair[0] - baseline)))})
                paired.append(pair)
            curves = np.median(np.array(paired), axis=0)
            original = old_rows[("study_38", str(sid), muscle)]
            expected_curve, _ = saved_curve(root, original)
            if not close(curves[0], expected_curve):
                raise ValueError("participant baseline does not reproduce saved curve")
            participant_checks.append(float(np.max(np.abs(curves[0] - expected_curve))))
            metrics = [_subject_metric({**original, "values": curve.tolist()}, protocol["muscles"][muscle])
                       for curve in curves]
            if any(m["outcome"] != "assessed" for m in metrics):
                raise ValueError("unassessed condition")
            records.append({
                "participant": str(sid), "muscle": muscle, "side": side, "fs": fs,
                "cycle_indices": [i for i, _ in selected], "source_label": labels[index],
                "metrics": dict(zip(CONDITIONS, metrics)),
                "curves": dict(zip(CONDITIONS, curves.tolist())),
                "centroid_change_pp": metrics[1][CENTROID] - metrics[0][CENTROID],
                "peak_change_pp": metrics[1][PEAK] - metrics[0][PEAK],
                "curve_rmse": float(np.sqrt(np.mean((curves[1] - curves[0]) ** 2))),
                "pattern_changed": metrics[1]["pattern_pass"] != metrics[0]["pattern_pass"],
            })
        print(f"Validated participant {sid}: both muscles, five fixed cycles", flush=True)
    table = {(r["participant"], r["muscle"]): r for r in records}
    left = np.array([[[table[(str(sid), m)]["metrics"][c][CENTROID] for m in MUSCLES]
                      for c in CONDITIONS] for sid in protocol["required_studies"]["study_38"]["subjects"]])
    right = np.array([[old_rows[("study_120", str(sid), m)][CENTROID] for m in MUSCLES]
                      for sid in protocol["required_studies"]["study_120"]["subjects"]])
    summary = bootstrap(left, right)
    unchanged = (protected == protected_inputs(root) and plan_hash == digests(root / PLAN)["sha256"]
                 and all(digests(root / p)["sha256"] == h for p, h in curve_inputs.items()))
    if not unchanged or len(cycle_checks) != 100 or len(records) != 20:
        raise ValueError("population or preservation check failed")
    sources = [Path(__file__), root / "src/harmo/physiology_control.py",
               root / "scripts/paper/check_scientific_reuse_readiness_v001.py"]
    result = {
        "analysis": "scientific_reuse_rms_sensitivity_v001", "status": "complete",
        "design": "retrospective exploratory paired one-factor sensitivity, Gait120 reference unchanged",
        "plan": str(PLAN), "plan_sha256": plan_hash,
        "conditions": CONDITIONS, "muscles": MUSCLES,
        "participants_per_study": 10, "lencioni_cycles": 50, "cycle_muscle_traces": 100,
        "bootstrap": {"iterations": ITERATIONS, "seed": SEED, "rng": "PCG64",
                      "unit": "participant", "ci": "percentile 95%, linear quantile"},
        "environment": {"python": platform.python_version(), "numpy": np.__version__, "scipy": scipy.__version__},
        "summary": summary, "participants": records,
        "baseline_checks": {"cycle_count": len(cycle_checks),
                            "cycle_max_abs_difference": max(x["max_difference"] for x in cycle_checks),
                            "participant_curve_count": len(participant_checks),
                            "participant_max_abs_difference": max(participant_checks),
                            "saved_metric_count": len(old_rows), "protected_inputs_unchanged": unchanged},
        "cycle_checks": cycle_checks, "source_assets": assets,
        "protected_inputs": protected, "curve_inputs": curve_inputs,
        "analysis_sources": {str(p.relative_to(root)): digests(p)["sha256"] for p in sources},
        "limitations": ["No biological ground truth or independent cohort.",
                        "Only RMS denominator changed; absent surrounding signal is not recovered.",
                        "Gait120 processed curves were held fixed; preprocessing is not fully matched across studies.",
                        "No P0/P1 information-addition effect or amplitude comparison tested.",
                        "Exploratory percentile intervals; no equivalence, causal or population-wide inference."],
    }
    lines = ["# 端点処理に対する研究間タイミング差の感度", "",
             "正式原稿は変更せず、計画v002に従って実施した探索的解析。単位は歩行周期パーセントポイント。", "",
             "| 筋 | 既存条件の研究間差［95%区間］ | 代替条件の研究間差［95%区間］ | 差の変化［95%区間］ |",
             "|---|---:|---:|---:|"]
    def fmt(value, ci):
        return f"{value:+.3f} [{ci[0]:+.3f}, {ci[1]:+.3f}]"
    for k, label in enumerate(("前脛骨筋", "腓腹筋内側頭")):
        a = fmt(summary["study_difference"][0][k], summary["study_difference_ci"][0][k])
        b = fmt(summary["study_difference"][1][k], summary["study_difference_ci"][1][k])
        delta = fmt(summary["change_in_study_difference"][k], summary["change_in_study_difference_ci"][k])
        lines.append(f"| {label} | {a} | {b} | {delta} |")
    lines += ["", "研究間差＝Gait120−Lencioni。代替条件ではLencioniのRMS端点分母のみを実測標本数に変更。研究内では両条件・両筋を対応させ、研究間は独立に参加者を再抽出した。95%区間は100,000回の参加者ブートストラップによる。", "",
              "## 参加者内の変化", "", "| 筋 | 重心変化の中央値［95%区間］ | 最大絶対重心変化 | 波形RMSE中央値 | パターン判定変更 |", "|---|---:|---:|---:|---:|"]
    for k, (muscle, label) in enumerate(zip(MUSCLES, ("前脛骨筋", "腓腹筋内側頭"))):
        rows = [r for r in records if r["muscle"] == muscle]
        shift = fmt(summary["median_within_participant_change"][k], summary["median_within_participant_change_ci"][k])
        lines.append(f"| {label} | {shift} | {max(abs(r['centroid_change_pp']) for r in rows):.3f} | {np.median([r['curve_rmse'] for r in rows]):.4f} | {sum(r['pattern_changed'] for r in rows)}/10 |")
    lines += ["", "## 検証範囲と限界", "",
              "Lencioni 10参加者・50周期・2筋を原信号から再計算した。既存条件は100周期・筋すべてで従来実装と一致し、20参加者・筋曲線は保存曲線と絶対差1e-10以内で一致した。Gait120の10参加者・2筋は保存済みの公開処理後曲線を固定した。", "",
              "これは同じ記録に対する一要素の処理感度であり、真値への改善ではない。フィルタ端点の影響、前後信号の欠落、研究間の処理・参加者背景の違いは残る。差の縮小を成功とせず、差の符号が変わっても生物学的な順序の逆転とは解釈しない。公開EMG全体の再利用率や、文献情報追加の有用性は本試験では未評価。", "",
              "正式原稿・図・既存検証結果・入力曲線が変更されていないことを確認した。詳細な参加者別結果と再現情報はreport.jsonに保存。"]
    out.mkdir(parents=True, exist_ok=False)
    (out / "report.json").write_text(json.dumps(result, ensure_ascii=False, indent=2, allow_nan=False) + "\n")
    (out / "REPORT.md").write_text("\n".join(lines) + "\n")
    print(json.dumps({"checks": result["baseline_checks"], "summary": summary}, indent=2))


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    run(ROOT, args.output if args.output.is_absolute() else ROOT / args.output)
