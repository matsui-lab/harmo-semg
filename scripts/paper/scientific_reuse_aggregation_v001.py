"""Compare explicitly distinct timing estimands on the same frozen cycles."""
import argparse
from collections import defaultdict
import io
import json
from pathlib import Path
import platform
import sys
import zipfile

import numpy as np
from scipy.io import loadmat

sys.path.insert(0, str(Path(__file__).parent))
import scientific_reuse_common_processing_v001 as C
from harmo.physiology_control import _mat_string

ROOT = C.ROOT
PLAN = Path("paper/revision_plans/scientific_reuse_protocol_v005_2026.09.30-r1.md")
PREVIOUS = Path("paper/results/scientific_reuse_common_processing_v001_2026.09.28-r1/report.json")
FEATURES = ("centroid", "peak")
ORDERS = ("waveform_first", "feature_first")


def features(curves):
    x = np.asarray(curves, float)
    if (x.ndim != 2 or x.shape[1] != 101 or len(x) < 1
            or not np.isfinite(x).all() or (x < 0).any() or (x.sum(axis=1) <= 0).any()):
        raise ValueError("invalid nonnegative cycle curves")
    return np.stack([(x * np.arange(101)).sum(axis=1) / x.sum(axis=1),
                     np.argmax(x, axis=1)], axis=1)


def aggregate(curves):
    x = np.asarray(curves, float)
    cycle_features = features(x)
    waveform = np.median(x, axis=0)
    return np.stack([features(waveform[None, :])[0],
                     np.median(cycle_features, axis=0)]), waveform, cycle_features


def summarize(left, right, iterations=100000, seed=20260930):
    """Both arrays: participant x condition x order x muscle x feature."""
    left, right = np.asarray(left, float), np.asarray(right, float)
    if (left.ndim != 5 or right.ndim != 5 or left.shape[1:] != (3, 2, 2, 2)
            or right.shape[1:] != left.shape[1:] or min(len(left), len(right)) < 2
            or not np.isfinite(left).all() or not np.isfinite(right).all() or iterations < 1):
        raise ValueError("invalid participant arrays")
    rng = np.random.Generator(np.random.PCG64(seed))
    li = rng.integers(0, len(left), (iterations, len(left)))
    ri = rng.integers(0, len(right), (iterations, len(right)))
    estimate = lambda x, draws: {"estimate": float(x), "ci95": np.quantile(
        draws, [.025, .975], method="linear").tolist()}
    rows = []
    for c, condition in enumerate(C.CONDITIONS):
        for m, muscle in enumerate(C.MUSCLES):
            for f, feature in enumerate(FEATURES):
                l, r = left[:, c, :, m, f], right[:, c, :, m, f]
                lm, rm = np.median(l, axis=0), np.median(r, axis=0)
                d = rm - lm
                draws = np.median(r[ri], axis=1) - np.median(l[li], axis=1)
                rows.append({"condition": condition, "muscle": muscle, "feature": feature,
                             "lencioni_medians": lm.tolist(), "gait120_medians": rm.tolist(),
                             "study_difference_A": estimate(d[0], draws[:, 0]),
                             "study_difference_B": estimate(d[1], draws[:, 1]),
                             "change_B_minus_A": estimate(d[1] - d[0], draws[:, 1] - draws[:, 0]),
                             "lencioni_participant_change": estimate(np.median(l[:, 1] - l[:, 0]),
                                 np.median((l[:, 1] - l[:, 0])[li], axis=1)),
                             "gait120_participant_change": estimate(np.median(r[:, 1] - r[:, 0]),
                                 np.median((r[:, 1] - r[:, 0])[ri], axis=1))})
    return rows


def extract_cycles(protocol, alignment):
    """Reuse frozen readers/processing; retain each cycle before aggregation."""
    output, seen, trials = [], set(), set()
    settings = protocol["study_38_envelope"]
    study = protocol["required_studies"]["study_38"]
    for sid in study["subjects"]:
        name = f"Subject{sid}.mat"
        source = loadmat(ROOT / C.ASSETS / name, squeeze_me=True, struct_as_record=False)["s"]
        labels = [_mat_string(x) for x in np.atleast_1d(source.EmgVarName)]
        side = _mat_string(source.EMGSide)
        selected = [(i, item) for i, item in enumerate(np.atleast_1d(source.Data).ravel())
                    if _mat_string(item.Task) == study["activity"] and _mat_string(item.Foot) == side
                    ][:int(study["expected_cycles_per_subject"])]
        if len(selected) != 5 or len(labels) != len(set(labels)):
            raise ValueError("Lencioni cycle or channel population mismatch")
        for muscle in C.MUSCLES:
            ch = labels.index(protocol["muscles"][muscle]["study_38_label"])
            for index, item in selected:
                curve = C.preprocess_study38_cycle(np.asarray(item.EMG, float)[ch, :],
                                                   float(source.EMGFreq), settings)
                output.append({"study": "study_38", "participant": sid, "muscle": muscle,
                               "cycle": f"{name}:Data[{index}]", "source_index_base": 0,
                               "curves": {c: curve.tolist() for c in C.CONDITIONS}})
        print(f"Retained Lencioni {sid}: 5 cycles, 2 muscles", flush=True)
    mapping = {(r["participant"], r["cycle"]): r for r in alignment["cycles"]}
    if len(mapping) != 94 or len(mapping) != len(alignment["cycles"]):
        raise ValueError("unexpected alignment population")
    with zipfile.ZipFile(ROOT / C.ASSETS / "Gait120_011_to_020.zip") as z:
        for sid in protocol["required_studies"]["study_120"]["subjects"]:
            raw = loadmat(io.BytesIO(z.read(f"{sid}/EMG/RawData.mat")))
            processed = loadmat(io.BytesIO(z.read(f"{sid}/EMG/ProcessedData.mat")))
            fs, = C.integers(C.field(raw, "EMGs_info.fs"))
            marker_fs, = C.integers(C.field(raw, "Markers_info.fs"))
            tids = C.integers(C.field(raw, "LevelWalking.AvailableTrialIdx"))
            if not np.array_equal(tids, C.integers(C.field(processed, "LevelWalking.AvailableTrialIdx"))):
                raise ValueError("trial mismatch")
            for tid in tids:
                trial = f"LevelWalking.Trial{tid:02d}"
                trials.add((sid, trial))
                data, labels = C._numeric_mat_value(raw, trial + ".EMGs_raw")
                if labels is None or len(set(labels)) != 12 or data.shape[1] != 12 or not np.isfinite(data).all():
                    raise ValueError("invalid raw channels")
                canonical = [C.canonical_gait120_channel(x) for x in labels]
                if len(set(canonical)) != 12:
                    raise ValueError("ambiguous channels")
                channels = {m: canonical.index(m) for m in C.MUSCLES}
                env = {m: C.common_envelope(data[:, ch], fs, settings) for m, ch in channels.items()}
                total = C.integers(C.field(raw, trial + ".TotalFrame"))
                n, = C.integers(C.field(raw, trial + ".nSteps"))
                for step in range(1, n + 1):
                    prefix = trial + f".Step{step:02d}"
                    identity = (sid, prefix)
                    if identity in seen:
                        raise ValueError("duplicate cycle")
                    seen.add(identity)
                    start, stop = C.sample_interval(total, C.field(raw, prefix + ".TargetFrame"), fs, marker_fs, len(data))
                    ref = mapping[identity]
                    if ((start, stop, int(fs), int(marker_fs)) != (ref["python_start"], ref["python_stop_exclusive"], ref["fs"], ref["marker_fs"])
                            or labels != ref["source_labels"] or len(data) != ref["raw_trial_samples"]):
                        raise ValueError("changed source alignment")
                    published, pub_labels = C._numeric_mat_value(processed, prefix + ".EMGs_interpolated")
                    if pub_labels != labels or published.shape != (101, 12):
                        raise ValueError("published table mismatch")
                    for muscle, ch in channels.items():
                        curves = (C._normalise_cycle(published[:, ch]), C.cut_normalize(env[muscle], start, stop),
                                  C.preprocess_study38_cycle(data[start:stop, ch], fs, settings))
                        output.append({"study": "study_120", "participant": sid, "muscle": muscle,
                                       "cycle": prefix, "python_start": start, "python_stop_exclusive": stop,
                                       "curves": {c: v.tolist() for c, v in zip(C.CONDITIONS, curves)}})
            patterns = {"trial": r"Trial\d+", "step": r"Step\d+"}
            keys = {k.rsplit(".", 1)[0] for k, _ in C._expand_template(processed, "LevelWalking.{trial}.{step}.EMGs_interpolated", patterns)}
            rawkeys = {k.rsplit(".", 1)[0] for k, _ in C._expand_template(raw, "LevelWalking.{trial}.{step}.TargetFrame", patterns)}
            if keys != rawkeys or keys != {k for s, k in seen if s == sid}:
                raise ValueError("unmatched cycles")
            print(f"Retained {sid}: {len(keys)} cycles, 2 muscles, 3 conditions", flush=True)
    if seen != set(mapping) or len(trials) != 50 or len(output) != 288:
        raise ValueError("changed analysis population")
    return output


def run(out):
    if out.exists():
        raise FileExistsError(out)
    before = C.protected_inputs(ROOT)
    previous = json.loads((ROOT / PREVIOUS).read_text())
    inputs = {str(p): C.digests(ROOT / p)["sha256"] for p in
              (PLAN, PREVIOUS, C.ALIGNMENT / "report.json", Path("paper/physiology_positive_control_protocol.yaml"))}
    for path, expected in previous["inputs"].items():
        actual = C.digests(ROOT / path)["sha256"]
        if actual != expected:
            raise ValueError(f"changed prior input: {path}")
        inputs[path] = actual
    for a in previous["source_assets"]:
        path = C.ASSETS / a["name"]
        identity = C.digests(ROOT / path)
        if any(identity[k] != a[k] for k in identity) or (ROOT / path).stat().st_size != a["bytes"]:
            raise ValueError("source asset changed")
        inputs[str(path)] = identity["sha256"]
    protocol = C.load_physiology_protocol(ROOT / "paper/physiology_positive_control_protocol.yaml")
    alignment = json.loads((ROOT / C.ALIGNMENT / "report.json").read_text())
    cycles = extract_cycles(protocol, alignment)
    grouped = defaultdict(list)
    for cycle in cycles:
        grouped[(cycle["study"], cycle["participant"], cycle["muscle"])].append(cycle)
    lref = {(r["subject_id"], r["muscle"]): r for r in previous["lencioni_curves"]}
    gref = {(r["participant"], r["muscle"]): r for r in previous["gait120_participants"]}
    table, records, diffs = {}, [], []
    for (study, sid, muscle), group in grouped.items():
        if len({r["cycle"] for r in group}) != len(group):
            raise ValueError("duplicate participant cycle")
        for c in C.CONDITIONS:
            values, waveform, per_cycle = aggregate([r["curves"][c] for r in group])
            ref = np.asarray(lref[(sid, muscle)]["values"] if study == "study_38" else gref[(sid, muscle)]["curves"][c])
            if not C.close(waveform, ref):
                raise ValueError("A curve differs from frozen common-processing result")
            if study == "study_120":
                baseline = gref[(sid, muscle)]["metrics"][c]
            else:
                baseline = C._subject_metric(lref[(sid, muscle)], protocol["muscles"][muscle])
            if not C.close(values[0], [baseline[C.CENTROID], baseline[C.PEAK]]):
                raise ValueError("A features differ from frozen result")
            diffs.append(float(np.max(np.abs(waveform - ref))))
            table[(study, sid, c, muscle)] = values
            records.append({"study": study, "participant": sid, "muscle": muscle, "condition": c,
                            "cycle_ids": [r["cycle"] for r in group], "n_cycles": len(group),
                            "A": values[0].tolist(), "B": values[1].tolist(),
                            "change_B_minus_A": (values[1] - values[0]).tolist(),
                            "cycle_features": per_cycle.tolist(),
                            "endpoint_peak_count": int(np.isin(per_cycle[:, 1], [0, 100]).sum())})
    arrays = {s: np.asarray([[[table[(s, sid, c, m)][o] for m in C.MUSCLES]
                              for c in C.CONDITIONS for o in range(2)]
                             for sid in protocol["required_studies"][s]["subjects"]]).reshape(10, 3, 2, 2, 2)
              for s in ("study_38", "study_120")}
    summary = summarize(arrays["study_38"], arrays["study_120"])
    for row in summary:
        if row["feature"] == "centroid":
            c, m = C.CONDITIONS.index(row["condition"]), C.MUSCLES.index(row["muscle"])
            if not C.close(row["study_difference_A"]["estimate"], previous["summary"]["study_differences"][c][m]):
                raise ValueError("A study contrast not reproduced")
    if before != C.protected_inputs(ROOT) or any(C.digests(ROOT / p)["sha256"] != h for p, h in inputs.items()):
        raise ValueError("protected input changed")
    sources = [Path(__file__), ROOT / "scripts/paper/scientific_reuse_common_processing_v001.py",
               ROOT / "scripts/paper/check_gait120_raw_alignment_v001.py", ROOT / "src/harmo/physiology_control.py"]
    result = {"status": "complete", "plan": str(PLAN), "conditions": C.CONDITIONS, "orders": ORDERS,
              "features": FEATURES, "muscles": C.MUSCLES, "summary": summary, "participants": records,
              "checks": {"participants": 20, "participant_muscle_pairs": len(grouped),
                         "lencioni_cycles": 50, "gait120_cycles": 94, "gait120_trials": 50,
                         "A_curve_checks_including_repeated_lencioni": len(diffs),
                         "A_curve_max_difference": max(diffs), "protected_inputs_unchanged": True},
              "bootstrap": {"iterations": 100000, "seed": 20260930, "rng": "PCG64", "unit": "participant"},
              "inputs": inputs, "protected_inputs": before,
              "analysis_sources": {str(p.relative_to(ROOT)): C.digests(p)["sha256"] for p in sources},
              "environment": {"python": platform.python_version(), "numpy": np.__version__, "scipy": C.scipy.__version__},
              "limits": ["Different estimands, neither is ground truth.", "Selected 20 participants; fixed cycles; exploratory intervals.",
                         "Linear peak medians may mix gait-boundary peaks or distinct lobes; not physiological delays.",
                         "No new full-pattern decisions for feature-first summaries."]}
    out.mkdir(parents=True, exist_ok=False)
    (out / "report.json").write_text(json.dumps(result, ensure_ascii=False, indent=2, allow_nan=False) + "\n")
    (out / "cycles.json").write_text(json.dumps(cycles, ensure_ascii=False, indent=2, allow_nan=False) + "\n")
    write_report(out, result)
    print(json.dumps({"checks": result["checks"], "centroid": [r for r in summary if r["feature"] == "centroid"]}, indent=2))


def write_report(out, result):
    names = dict(zip(C.CONDITIONS, ("既存比較", "共通処理・試行単位", "共通処理・周期単位")))
    muscles = dict(zip(C.MUSCLES, ("前脛骨筋", "腓腹筋内側頭")))
    fmt = lambda r: f"{r['estimate']:+.3f} [{r['ci95'][0]:+.3f}, {r['ci95'][1]:+.3f}]"
    lines = ["# 集約順序によるタイミング指標の違い", "", "計画v005に従う探索的対照。A＝参加者の中央値波形から算出、B＝各周期の特徴の参加者内中央値。研究間差＝Gait120−Lencioni。単位は歩行周期パーセントポイント。角括弧は参加者ブートストラップ95%区間。", ""]
    for f, title in zip(FEATURES, ("活動重心（主指標）", "ピーク位相（補助診断）")):
        lines += [f"## {title}", "", "| 処理条件 | 筋 | Aの研究間差 | Bの研究間差 | 差の変化 B−A |", "|---|---|---:|---:|---:|"]
        for r in result["summary"]:
            if r["feature"] == f:
                lines.append(f"| {names[r['condition']]} | {muscles[r['muscle']]} | {fmt(r['study_difference_A'])} | {fmt(r['study_difference_B'])} | {fmt(r['change_B_minus_A'])} |")
        lines += ["", "| 処理条件 | 筋 | Lencioni参加者内変化の中央値 | Gait120参加者内変化の中央値 |", "|---|---|---:|---:|"]
        for r in result["summary"]:
            if r["feature"] == f:
                lines.append(f"| {names[r['condition']]} | {muscles[r['muscle']]} | {fmt(r['lencioni_participant_change'])} | {fmt(r['gait120_participant_change'])} |")
        lines.append("")
    lines += ["## 全参加者の値と周期ピーク", "", "Lencioniの同一値の反復は省略。0と100は同じ周期境界の両側なので、ピークの大きな直線位相差は遅延と解釈しない。偶数周期の中央値や多峰性にも注意する。", "", "| 研究・参加者 | 筋 | 条件 | 重心 A / B | ピーク A / B | 各周期ピーク |", "|---|---|---|---:|---:|---|"]
    for r in result["participants"]:
        if r["study"] == "study_38" and r["condition"] != "existing":
            continue
        peaks = ", ".join(str(int(x[1])) for x in r["cycle_features"])
        study = "Lencioni" if r["study"] == "study_38" else "Gait120"
        lines.append(f"| {study} {r['participant']} | {muscles[r['muscle']]} | {names[r['condition']]} | {r['A'][0]:.3f} / {r['B'][0]:.3f} | {r['A'][1]:.1f} / {r['B'][1]:.1f} | {peaks} |")
    lines += ["", "## 検証と解釈の範囲", "", f"20参加者、40参加者・筋、50＋94周期を保持。全処理条件のA波形120件（Lencioniの同一波形反復を含む）を旧結果と照合し、最大絶対差は{result['checks']['A_curve_max_difference']:.3g}。Aの重心・ピークおよび重心の研究間差も再現した。原入力、正式原稿、図、旧結果の不変性を確認した。", "", "ブートストラップは研究間で独立、同一研究内の条件・集約順序・筋は対応を保持した100,000回（seed=20260930）。固定した周期選択を条件とする区間であり、周期内の選択不確実性や研究間の背景差は評価していない。旧報告とは乱数seedが異なるため、Aの区間端点はわずかに変わり得る。", "", "AとBは異なる対象量であり、違いは一方の誤りを意味しない。どちらも生理学的真値ではない。ピークのBに波形用のパターン合否を流用していない。選択済み2研究の適用例であり、一般的な自動判定性能や振幅比較可能性を示すものではない。", "", "[計算結果と参加者指標](report.json)、[全周期の入力曲線](cycles.json)、[計算前の計画](../../revision_plans/scientific_reuse_protocol_v005_2026.09.30-r1.md)。"]
    (out / "REPORT.md").write_text("\n".join(lines) + "\n")


if __name__ == "__main__":
    p = argparse.ArgumentParser()
    p.add_argument("--output", type=Path, required=True)
    args = p.parse_args()
    run(args.output if args.output.is_absolute() else ROOT / args.output)
