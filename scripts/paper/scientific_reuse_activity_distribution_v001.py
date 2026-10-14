"""Describe phase-resolved envelope area in the frozen two-study sample."""
import argparse
from collections import defaultdict
import hashlib
import json
from pathlib import Path

import numpy as np

ROOT = Path(__file__).resolve().parents[2]
BASE = ROOT / "paper/results/scientific_reuse_aggregation_v001_2026.09.30-r1"
PLAN = ROOT / "paper/revision_plans/scientific_reuse_protocol_v006_2026.09.30-r2.md"
CONDITIONS = ("existing", "common_trial", "common_cycle")
MUSCLES = ("tibialis anterior", "medial gastrocnemius")
REGIONS = ((0, 20), (20, 60), (60, 100))
STUDIES = ("study_38", "study_120")


def digest(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def validate_curves(curves):
    x = np.asarray(curves, dtype=float)
    if (x.ndim != 2 or x.shape[1] != 101 or len(x) == 0
            or not np.isfinite(x).all() or (x < 0).any()
            or (x.sum(axis=1) <= 0).any()):
        raise ValueError("Expected finite nonnegative nonzero 101-point curves")
    return x


def areas(curves):
    x = validate_curves(curves)
    area = np.stack([((x[:, a:b] + x[:, a + 1:b + 1]) * .5).sum(axis=1)
                     for a, b in REGIONS], axis=1)
    total = ((x[:, :-1] + x[:, 1:]) * .5).sum(axis=1)
    result = area / total[:, None]
    np.testing.assert_allclose(result.sum(axis=1), 1, atol=1e-12, rtol=0)
    return result


def features(curves):
    x = validate_curves(curves)
    return np.column_stack(((x * np.arange(101)).sum(axis=1) / x.sum(axis=1),
                            x.argmax(axis=1)))


def paired_change(values, before, after, draws):
    delta = values[:, after] - values[:, before]
    estimate = np.median(delta, axis=0)
    ci = np.quantile(np.median(delta[draws], axis=1), [.025, .975],
                     axis=0, method="linear")
    return estimate, ci


def run(output):
    if output.exists():
        raise FileExistsError(output)
    previous = json.loads((BASE / "report.json").read_text())
    curves = json.loads((BASE / "cycles.json").read_text())
    protected = {p: digest(ROOT / p) for p in previous["protected_inputs"]}
    inputs = {str(p.relative_to(ROOT)): digest(p)
              for p in (PLAN, BASE / "report.json", BASE / "cycles.json")}
    common = ROOT / "paper/results/scientific_reuse_common_processing_v001_2026.09.28-r1/report.json"
    if digest(common) != previous["inputs"][str(common.relative_to(ROOT))]:
        raise ValueError("Common-processing report changed")
    inputs[str(common.relative_to(ROOT))] = digest(common)
    if len(curves) != 288 or tuple(previous["conditions"]) != CONDITIONS:
        raise ValueError("Frozen population or conditions changed")
    ref = {(r["study"], str(r["participant"]), r["muscle"], r["condition"]): r
           for r in previous["participants"]}
    if len(ref) != 120:
        raise ValueError("Unexpected participant reference table")
    grouped = defaultdict(list)
    seen = set()
    for row in curves:
        key = (row["study"], str(row["participant"]), row["muscle"], row["cycle"])
        if key in seen or set(row["curves"]) != set(CONDITIONS):
            raise ValueError("Duplicate cycle or missing condition")
        seen.add(key)
        grouped[key[:3]].append(row)
    for study, expected in zip(STUDIES, (50, 94)):
        identities = {(r["participant"], r["cycle"]) for r in curves if r["study"] == study}
        ids = {str(p) for p, _ in identities}
        expected_ids = {str(i) for i in range(1, 11)} if study == "study_38" else {f"S{i:03d}" for i in range(11, 21)}
        if len(identities) != expected or ids != expected_ids:
            raise ValueError("Changed participant/cycle set")
        for p, c in identities:
            if {r["muscle"] for r in curves if r["study"] == study and r["participant"] == p and r["cycle"] == c} != set(MUSCLES):
                raise ValueError("Muscles not matched")
    records, cycle_records = [], []
    max_old_diff = 0.
    for (study, participant, muscle), rows in grouped.items():
        for condition in CONDITIONS:
            x = validate_curves([r["curves"][condition] for r in rows])
            baseline = ref[(study, participant, muscle, condition)]
            if [r["cycle"] for r in rows] != baseline["cycle_ids"]:
                raise ValueError("Cycle order changed")
            if study == "study_38":
                for r in rows:
                    np.testing.assert_array_equal(r["curves"][condition], r["curves"]["existing"])
            fs = features(x)
            median_curve = np.median(x, axis=0)
            for a, b in ((fs, baseline["cycle_features"]),
                         (features(median_curve[None, :])[0], baseline["A"]),
                         (np.median(fs, axis=0), baseline["B"])):
                np.testing.assert_allclose(a, b, atol=1e-10, rtol=0)
                max_old_diff = max(max_old_diff, float(np.max(np.abs(np.asarray(a) - b))))
            shares = areas(x)
            quantiles = np.quantile(shares, [.25, .5, .75], axis=0, method="linear")
            peaks = fs[:, 1]
            counts = {"early": int((peaks <= 20).sum()),
                      "middle": int(((peaks > 20) & (peaks < 60)).sum()),
                      "late": int((peaks >= 60).sum()),
                      "endpoint": int(np.isin(peaks, [0, 100]).sum())}
            record = {"study": study, "participant": participant, "muscle": muscle,
                      "condition": condition, "n_cycles": len(rows),
                      "area_share_median": quantiles[1].tolist(),
                      "area_share_q25": quantiles[0].tolist(),
                      "area_share_q75": quantiles[2].tolist(),
                      "within_participant_iqr": (quantiles[2] - quantiles[0]).tolist(),
                      "representative_curve": median_curve.tolist(),
                      "representative_peak": float(np.argmax(median_curve)),
                      "cycle_peak_counts": counts,
                      "both_early_and_late_peaks": bool(counts["early"] and counts["late"])}
            records.append(record)
            for i, r in enumerate(rows):
                cycle_records.append({"study": study, "participant": participant, "muscle": muscle,
                                      "cycle": r["cycle"], "condition": condition,
                                      "area_shares": shares[i].tolist(), "peak": float(peaks[i])})
    summary = []
    for study in STUDIES:
        for muscle in MUSCLES:
            for condition in CONDITIONS:
                rs = [r for r in records if (r["study"], r["muscle"], r["condition"]) == (study, muscle, condition)]
                if len(rs) != 10:
                    raise ValueError("Expected ten participants per group")
                shares = np.array([r["area_share_median"] for r in rs])
                within = np.array([r["within_participant_iqr"] for r in rs])
                q = np.quantile(shares, [.25, .5, .75], axis=0, method="linear")
                in_window = [r for r in rs if 20 <= r["representative_peak"] <= 60]
                outside = [r for r in rs if not 20 <= r["representative_peak"] <= 60]
                summary.append({"study": study, "muscle": muscle, "condition": condition,
                                "n_participants": 10, "n_cycles": sum(r["n_cycles"] for r in rs),
                                "area_share_median": q[1].tolist(),
                                "area_share_q25": q[0].tolist(), "area_share_q75": q[2].tolist(),
                                "between_participant_iqr": (q[2] - q[0]).tolist(),
                                "median_within_participant_iqr": np.median(within, axis=0).tolist(),
                                "both_early_and_late_count": sum(r["both_early_and_late_peaks"] for r in rs),
                                "representative_peaks_in_20_60": len(in_window),
                                "in_window_observed_range": [min(r["representative_peak"] for r in in_window),
                                                             max(r["representative_peak"] for r in in_window)] if in_window else None,
                                "outside_20_60": [{"participant": r["participant"], "peak": r["representative_peak"]} for r in outside]})
    # Participant draws are shared across muscles, conditions and regions.
    draws = np.random.Generator(np.random.PCG64(20260930)).integers(0, 10, (100000, 10))
    changes = []
    for muscle in MUSCLES:
        by_key = {(r["participant"], r["condition"]): r for r in records
                  if r["study"] == "study_120" and r["muscle"] == muscle}
        ids = sorted({p for p, _ in by_key})
        values = np.array([[by_key[(p, c)]["area_share_median"] for c in CONDITIONS] for p in ids])
        for before, after in ((0, 1), (0, 2), (1, 2)):
            estimate, ci = paired_change(values, before, after, draws)
            changes.append({"muscle": muscle, "before": CONDITIONS[before], "after": CONDITIONS[after],
                            "median_participant_change_pp": (100 * estimate).tolist(),
                            "ci95_pp": (100 * ci).T.tolist()})
    checks = {"participants": 20, "participant_muscle_pairs": len(grouped),
              "unique_cycles": 144, "cycle_muscle_conditions": len(cycle_records),
              "participant_muscle_conditions": len(records), "prior_feature_max_difference": max_old_diff}
    if checks["participant_muscle_pairs"] != 40 or checks["cycle_muscle_conditions"] != 864:
        raise ValueError("Unexpected output population")
    if protected != {p: digest(ROOT / p) for p in protected} or inputs != {p: digest(ROOT / p) for p in inputs}:
        raise ValueError("Input changed during execution")
    result = {"status": "complete", "plan": str(PLAN.relative_to(ROOT)),
              "regions": REGIONS, "conditions": CONDITIONS, "muscles": MUSCLES,
              "participants": records, "cycles": cycle_records,
              "summary": summary, "gait120_changes": changes, "checks": checks,
              "inputs": inputs, "protected_inputs": protected,
              "bootstrap": {"iterations": 100000, "seed": 20260930, "unit": "participant"},
              "analysis_source": digest(Path(__file__)), "numpy": np.__version__,
              "limits": ["Exploratory reanalysis after inspection of prior peaks.",
                         "Area shares describe envelope distribution, not force, energy or activation duration.",
                         "Fixed cycles; no patient validation, normal range or event-equivalence claim.",
                         "Lencioni is unchanged across conditions; no new raw-source verification."]}
    output.mkdir(parents=True, exist_ok=False)
    (output / "report.json").write_text(json.dumps(result, indent=2, ensure_ascii=False, allow_nan=False) + "\n")
    write_report(output, result)
    print(json.dumps({"checks": checks, "summary": summary, "changes": changes}, indent=2))


def write_report(output, result):
    study = {"study_38": "Lencioni", "study_120": "Gait120"}
    muscle = {"tibialis anterior": "TA", "medial gastrocnemius": "MG"}
    condition = {"existing": "公開処理を含む既存比較", "common_trial": "共通試行処理", "common_cycle": "共通周期処理"}
    def vector(x):
        return " / ".join(f"{100*v:.1f}" for v in x)
    lines = ["# 公開歩行EMGの活動分布と変動", "",
             "計画v006に基づく探索的解析。20参加者・144周期・2筋を保持。区間は0–20 / 20–60 / 60–100%。面積比率は周期全体に対する包絡線の積分比であり、振幅の研究間比較・活動時間・筋力ではない。区間長は異なる。", "",
             "## 研究別の活動分布", "",
             "| 研究 | 筋 | 条件 | 周期別比率の参加者中央値をさらに研究内で取った値（%） | 参加者間IQR（pp） | 周期間IQRの参加者中央値（pp） |",
             "|---|---|---|---:|---:|---:|"]
    for r in result["summary"]:
        if r["study"] == "study_38" and r["condition"] != "existing":
            continue
        lines.append(f"| {study[r['study']]} | {muscle[r['muscle']]} | {condition[r['condition']]} | {vector(r['area_share_median'])} | {vector(r['between_participant_iqr'])} | {vector(r['median_within_participant_iqr'])} |")
    lines += ["", "各研究10参加者。区間ごとに中央値を取るので表示値は必ずしも100%に合計されない。IQRは記述的な四分位範囲で、信頼区間ではない。Lencioniは全条件同一のため反復表示を省略。", "",
              "## ピークの分布", "", "| 研究 | 筋 | 条件 | 代表波形ピークが20–60%にある人数 | その観測範囲 | 同一参加者で周期ピークが0–20%と60–100%の両方にある人数 |",
              "|---|---|---|---:|---|---:|"]
    for r in result["summary"]:
        if r["study"] == "study_38" and r["condition"] != "existing":
            continue
        lines.append(f"| {study[r['study']]} | {muscle[r['muscle']]} | {condition[r['condition']]} | {r['representative_peaks_in_20_60']}/10 | {r['in_window_observed_range']} | {r['both_early_and_late_count']}/10 |")
    lines += ["", "20–60%はMGの既存活動窓であり、TAの合否判定には用いない。二つの区間に周期ピークがあることは、各周期が二峰性であることや生理学的な遅延を示さない。範囲外の参加者と端点ピークはreport.jsonに保持。", "",
              "## Gait120の処理変更による面積比率の変化", "",
              "| 筋 | 変更前 | 変更後 | 区間 | 参加者内変化の中央値［95%区間］（面積比率pp） |", "|---|---|---|---|---:|"]
    for r in result["gait120_changes"]:
        for i, region in enumerate(REGIONS):
            lo, hi = r["ci95_pp"][i]
            lines.append(f"| {muscle[r['muscle']]} | {condition[r['before']]} | {condition[r['after']]} | {region[0]}–{region[1]}% | {r['median_participant_change_pp'][i]:+.2f} [{lo:+.2f}, {hi:+.2f}] |")
    lines += ["", "95%区間は固定した周期集合に対する参加者単位の探索的ブートストラップ。条件・筋・区間の対応を保持。区間の0包含を同等性と扱わない。", "",
              "## 再現性と適用範囲", "",
              f"旧指標の最大絶対差：{result['checks']['prior_feature_max_difference']:.3g}。全120参加者–筋–条件と864周期–筋–条件を照合。原稿・採用図・旧解析結果は変更していない。今回は保存済み周期曲線の再解析であり、原ファイルからの再読出しではない。", "",
              "臨床的な正常範囲・異常判定・治療効果は評価していない。固定百分率区間は実測の立脚期・遊脚期ではない。全対象の数値は[計算結果](report.json)、手順は[計算前計画](../../revision_plans/scientific_reuse_protocol_v006_2026.09.30-r2.md)に示す。"]
    (output / "REPORT.md").write_text("\n".join(lines) + "\n")


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    run(args.output if args.output.is_absolute() else ROOT / args.output)
