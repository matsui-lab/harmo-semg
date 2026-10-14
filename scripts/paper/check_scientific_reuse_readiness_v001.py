"""Read-only source inspection and baseline replay; no new treatment contrasts."""
from __future__ import annotations

import argparse
from collections import Counter
from copy import deepcopy
import hashlib
import json
from pathlib import Path
import platform
import sys
import zipfile

import numpy as np
import scipy
from scipy.io import loadmat

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "src"))
from harmo.physiology_control import (
    _mat_string, _subject_metric, extract_study38_rows,
    extract_study120_rows, load_physiology_protocol,
)

CURVES = Path("paper/results/registered_reuse_clean_v001_2026.09.13-r1/timing-001/resolution/source-contracts")
ASSETS = Path("downloads/additional_framework_v001_2026.09.12-r1")
TOLERANCE = 1e-10
NUMERIC = ("activation_centroid_percent_gait_cycle", "peak_timing_percent_gait_cycle",
           "log2_phase_contrast", "active_phase_mean_within_subject_scale",
           "reference_phase_mean_within_subject_scale")
DECISIONS = ("pattern_pass", "peak_window_pass", "phase_contrast_pass")


def read(path):
    return json.loads(path.read_text())


def digests(path):
    sha, md5 = hashlib.sha256(), hashlib.md5()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            sha.update(chunk)
            md5.update(chunk)
    return {"sha256": sha.hexdigest(), "md5": md5.hexdigest()}


def close(left, right):
    a, b = np.asarray(left, dtype=float), np.asarray(right, dtype=float)
    return bool(a.shape == b.shape and np.isfinite(a).all()
                and np.isfinite(b).all() and np.all(np.abs(a - b) <= TOLERANCE))


def source_category(source):
    # A local analyst repository is NOT the source public release.
    if source["source_type"] == "repository-config":
        return "analyst_configuration_origin_to_trace"
    if source["source_type"] == "peer-reviewed-article":
        return "external_publication_candidate_P1"
    return "unclassified_requires_review"


def claim_ledger(report):
    sources = {s["source_id"]: s for s in report["sources"]}
    if len(sources) != len(report["sources"]):
        raise ValueError("duplicate source IDs")
    claims = report["claims"]
    if len({c["claim_id"] for c in claims}) != len(claims):
        raise ValueError("duplicate claim IDs")
    ledger = []
    for claim in claims:
        source = sources[claim["source_id"]]
        ledger.append({
            "claim_id": claim["claim_id"], "study_id": claim["study_id"],
            "property": claim["property_path"], "value": claim.get("value"),
            "scope_level": claim.get("scope_level"),
            "scope_selector": claim.get("scope_selector"),
            "locator": claim.get("locator"), "source_id": source["source_id"],
            "uri": source["uri"], "source_type": source["source_type"],
            "category": source_category(source),
            "P0_eligibility": "not_established",
            "source_snapshot": source.get("content_sha256"),
            "current_document_revalidated": False,
            "reuse_permission_established": False,
        })
    return ledger


def protected_inputs(root):
    paths = [
        Path("paper/paper.md"), Path("paper/MANUSCRIPT_VERSION"),
        Path("paper/japanese/v012_2026.09.27-r4/manuscript.md"),
        Path("paper/physiology_positive_control_protocol.yaml"),
        Path("paper/literature_semantic_enrichment.yaml"),
    ]
    paths += [p.relative_to(root) for p in (root / "paper/results/validation").glob("*.json")]
    paths += [Path("paper/cmpb") / n for n in ("Fig1.pdf", "Fig2.pdf", "Fig3.pdf", "FigS1.pdf", "FigS2.pdf")]
    return {str(p): digests(root / p)["sha256"] for p in sorted(paths)}


def saved_curve(root, row):
    path = root / CURVES / row["study_id"] / row["subject_id"] / "source.subject-curve.csv"
    data = np.genfromtxt(path, delimiter=",", names=True)
    field = {"tibialis anterior": "TA", "medial gastrocnemius": "GM"}[row["muscle"]]
    values = data[field]
    if values.shape != (101,) or not np.isfinite(values).all():
        raise ValueError(f"invalid saved curve: {path}")
    return values, path


def run(root, out):
    out.mkdir(parents=True, exist_ok=False)
    before = protected_inputs(root)
    protocol = load_physiology_protocol(root / "paper/physiology_positive_control_protocol.yaml")
    original = read(root / "paper/results/validation/physiology_positive_control.json")
    source_manifest = read(root / ASSETS / "source_verification.json")
    registered = {row["name"]: row for row in source_manifest}
    assets = []
    for study, entries in protocol["source_assets"].items():
        for name, expected in entries.items():
            path = root / ASSETS / name
            row = {"study_id": study, "name": name, "exists": path.is_file()}
            if row["exists"]:
                row.update(bytes=path.stat().st_size, **digests(path))
                row["matches"] = (
                    row["bytes"] == expected["bytes"]
                    and row["md5"] == expected["md5"]
                    and row["sha256"] == registered[name]["sha256"])
            else:
                row["matches"] = False
            assets.append(row)

    metrics, curve_inputs = [], {}
    seen = set()
    for row in original["metrics"]:
        identity = (row["study_id"], row["subject_id"], row["muscle"])
        if identity in seen:
            raise ValueError("duplicate participant-muscle identity")
        seen.add(identity)
        values, path = saved_curve(root, row)
        curve_inputs[str(path.relative_to(root))] = digests(path)["sha256"]
        calc = _subject_metric({**row, "values": values.tolist()}, protocol["muscles"][row["muscle"]])
        differences = {key: abs(calc[key] - row[key]) for key in NUMERIC}
        metrics.append({
            "study": identity[0], "participant": identity[1], "muscle": identity[2],
            "max_absolute_difference": max(differences.values()),
            "numeric_match": all(close(calc[key], row[key]) for key in NUMERIC),
            "decisions_match": all(calc[key] == row[key] for key in DECISIONS),
            "pattern_pass": calc["pattern_pass"],
        })
    expected_identities = {
        (study, str(participant), muscle)
        for study, spec in protocol["required_studies"].items()
        for participant in spec["subjects"] for muscle in protocol["muscles"]}

    metadata = []
    for participant in protocol["required_studies"]["study_38"]["subjects"]:
        source = loadmat(root / ASSETS / f"Subject{participant}.mat",
                         squeeze_me=True, struct_as_record=False)["s"]
        side = _mat_string(source.EMGSide)
        records = np.atleast_1d(source.Data).ravel()
        walking = [(i, item) for i, item in enumerate(records)
                   if _mat_string(item.Task) == "Walking"]
        same_side = [(i, item) for i, item in walking if _mat_string(item.Foot) == side]
        metadata.append({
            "participant": str(participant), "side": side,
            "sampling_rate_hz": float(source.EMGFreq),
            "declared_labels": [_mat_string(x) for x in np.atleast_1d(source.EmgVarName)],
            "walking_records": len(walking), "same_side_walking_records": len(same_side),
            "selected_source_indices_zero_based": [i for i, _ in same_side[:5]],
            "selected_sample_counts": [int(np.asarray(item.EMG).shape[1]) for _, item in same_side[:5]],
            "time_representation": "source-delimited raw stride; no continuous context inferred",
        })
    archive_path = root / ASSETS / "Gait120_011_to_020.zip"
    with zipfile.ZipFile(archive_path) as archive:
        members = [n for n in archive.namelist()
                   if "/EMG/" in n and not n.endswith("/")]
    structure = {
        "emg_member_count": len(members),
        "emg_member_basenames": dict(Counter(Path(n).name for n in members)),
        "members": members,
        "note": "Names only: raw-file presence does not establish raw-to-processed alignment.",
    }

    pilot = []
    if all(row["matches"] for row in assets):
        small = deepcopy(protocol)
        for study in small["required_studies"]:
            small["required_studies"][study]["subjects"] = protocol["required_studies"][study]["subjects"][:1]
        left, _ = extract_study38_rows(root / ASSETS, small)
        right, _ = extract_study120_rows(archive_path, small)
        for row in left + right:
            expected, _ = saved_curve(root, row)
            pilot.append({
                "study": row["study_id"], "participant": row["subject_id"],
                "muscle": row["muscle"], "cycles": row["cycle_count"],
                "max_absolute_curve_difference": float(np.max(np.abs(expected - row["values"]))),
                "curve_matches": close(expected, row["values"]),
                "source_channel_label": row.get("source_channel_label"),
                "source_member": row.get("source_member"),
            })

    ledger = claim_ledger(read(root / "paper/results/validation/literature_semantic_enrichment.json"))
    after = protected_inputs(root)
    curve_inputs_unchanged = all(digests(root / p)["sha256"] == value
                                 for p, value in curve_inputs.items())
    checks = {
        "all_11_assets_match": len(assets) == 11 and all(a["matches"] for a in assets),
        "exact_expected_curve_population": seen == expected_identities and len(metrics) == 40,
        "saved_curve_metrics_match": all(m["numeric_match"] and m["decisions_match"] for m in metrics),
        "four_source_pilot_curves_match": len(pilot) == 4 and all(p["curve_matches"] for p in pilot),
        "protected_inputs_unchanged": before == after and curve_inputs_unchanged,
    }
    result = {
        "analysis": "scientific_reuse_readiness_v001",
        "stage": "input_readiness_and_existing_result_replay",
        "new_harmonization_effect_tested": False,
        "all_checks_pass": all(checks.values()), "checks": checks,
        "environment": {"python": platform.python_version(), "numpy": np.__version__, "scipy": scipy.__version__},
        "tolerance_absolute": TOLERANCE,
        "assets": assets, "saved_curve_metrics": metrics, "source_pilot": pilot,
        "lencioni_source_metadata": metadata, "gait120_archive_structure": structure,
        "claim_source_counts": dict(Counter(row["category"] for row in ledger)),
        "n_claims": len(ledger), "n_sources": len({r["source_id"] for r in ledger}),
        "P0_reconstruction_complete": False,
        "protected_inputs_before": before, "protected_inputs_after": after,
        "curve_inputs": curve_inputs,
        "analysis_sources": {
            str(Path(__file__).relative_to(ROOT)): digests(Path(__file__))["sha256"],
            "src/harmo/physiology_control.py": digests(root / "src/harmo/physiology_control.py")["sha256"],
        },
        "limits": [
            "40 curves are replayed from saved participant curves, not 40 new raw-source extractions.",
            "Source-level extraction pilot is restricted to the first participant per study and two muscles.",
            "Current remote documents are not fetched or revalidated.",
            "Source categories are not completed P0/P1 adjudications or use permissions.",
            "No new treatment contrast, participant, biological truth or independent validation.",
        ],
    }
    (out / "report.json").write_text(json.dumps(result, ensure_ascii=False, indent=2) + "\n")
    (out / "claim_source_ledger.json").write_text(json.dumps(ledger, ensure_ascii=False, indent=2) + "\n")
    report = f"""# 追加検証の入力確認・基準再現

新しいハーモナイズ効果はまだ評価していない。正式原稿と既存結果は保持した。

- 登録取得物の一致：{sum(a['matches'] for a in assets)}/{len(assets)}
- 保存済み参加者・筋曲線の指標・判定一致：{sum(m['numeric_match'] and m['decisions_match'] for m in metrics)}/{len(metrics)}
- 原ファイルからの試行抽出の曲線一致：{sum(p['curve_matches'] for p in pilot)}/{len(pilot)}（各研究の最初の1参加者、2筋）
- 保存曲線中のパターン不適合：{sum(not m['pattern_pass'] for m in metrics)}件。除外していない。
- 正式原稿・旧結果・入力曲線の保持：{checks['protected_inputs_unchanged']}
- 出典を整理した主張：{len(ledger)}件、{result['n_sources']}情報源。
- 公開情報のみのP0対照の再構成：未完了。

## 次の作業

解析者設定の値を元の公開資料へ遡り、P0/P1を判定する。Gait120アーカイブ内の原信号候補について、イベント・チャンネル・処理後波形との対応を確認する。Lencioni側では同じ周期の端点RMS分母のみを変える対照の実施条件を固定し、処理全体の変更と混同しない。

## 解釈の限界

この結果は新規の有用性実証ではなく、追加検証の開始点が再現可能かを調べたものである。40保存曲線の指標再計算と、4曲線の原ファイル抽出は区別する。資料の現行版との再照合や、下肢EMG全体の再利用率の評価は行っていない。
"""
    (out / "REPORT.md").write_text(report)
    print(json.dumps({k: result[k] for k in ("stage", "all_checks_pass", "checks", "claim_source_counts")}, indent=2))
    return result


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", required=True, type=Path)
    args = parser.parse_args()
    result = run(ROOT, args.output.resolve())
    raise SystemExit(0 if result["all_checks_pass"] else 1)
