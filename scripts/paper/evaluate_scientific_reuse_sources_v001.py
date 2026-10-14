"""Validate analyst-reviewed source attribution and evaluate explicit information gates."""
import argparse
from collections import Counter
import json
from pathlib import Path
import sys
import xml.etree.ElementTree as ET
import zipfile

import yaml

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(Path(__file__).parent))
from check_scientific_reuse_readiness_v001 import ASSETS, digests, protected_inputs

PLAN = Path("paper/revision_plans/scientific_reuse_protocol_v004_2026.09.28-r4.md")
REVIEW = Path("paper/revision_plans/scientific_reuse_source_adjudication_v001_2026.09.28-r1.yaml")
SNAPSHOTS = Path("paper/results/scientific_reuse_source_snapshots_v001_2026.09.28-r1")
COMMON = Path("paper/results/scientific_reuse_common_processing_v001_2026.09.28-r1/report.json")


def normalized(text):
    return " ".join(str(text).split())


def source_text(root, spec, section=None):
    path = root / spec["path"]
    if "member" in spec:
        with zipfile.ZipFile(path) as z:
            return normalized(z.read(spec["member"]).decode("utf-8-sig"))
    if spec["kind"] == "article":
        tree = ET.parse(path).getroot()
        dois = [x.text for x in tree.findall(".//article-meta/article-id") if x.get("pub-id-type") == "doi"]
        if spec["doi"] not in dois:
            raise ValueError("article identity mismatch")
        if section:
            sections = [s for s in tree.findall(".//sec")
                        if s.find("title") is not None
                        if normalized(" ".join(s.find("title").itertext())) == section]
            if len(sections) != 1:
                raise ValueError(f"ambiguous or missing article section {section}")
            return normalized(" ".join(sections[0].itertext()))
        return normalized(" ".join(tree.itertext()))
    if "json_key" in spec:
        value = json.loads(path.read_text())[spec["json_key"]]
        return normalized(value if isinstance(value, str) else json.dumps(value, ensure_ascii=False))
    return normalized(path.read_text(encoding="utf-8-sig"))


def validate_review(review):
    keys = [(r["study"], r["attribute"]) for r in review["rows"]]
    if len(keys) != len(set(keys)):
        raise ValueError("duplicate study-attribute rows")
    for r in review["rows"]:
        for level in ("P0", "P1"):
            if r[level] not in review["states"]:
                raise ValueError("invalid state")
            for s in r[level + "_sources"]:
                if s not in review["sources"]:
                    raise ValueError("unknown evidence source")
                if level == "P0" and review["sources"][s]["kind"] == "article":
                    raise ValueError("article leaked into repository baseline")
            if r[level] == "confirmed" and not r[level + "_sources"]:
                raise ValueError("confirmed without evidence")
    return {(r["study"], r["attribute"]): r for r in review["rows"]}


def gates(review):
    rows = validate_review(review)
    studies = sorted({r["study"] for r in review["rows"]})
    results = []
    for level in ("P0", "P1"):
        for name, use in review["uses"].items():
            missing = [{"study": s, "attribute": a, "state": rows[(s, a)][level]}
                       for s in studies for a in use["requires"] if rows[(s, a)][level] != "confirmed"]
            results.append({"level": level, "use": name, "label": use["label"],
                            "requirements_met": not missing, "unresolved": missing,
                            "status": use["supported_label"] if not missing else "根拠要件が未充足",
                            "automatic_release_granted": False,
                            "biological_validity_established": False})
    return results


def run(out):
    if out.exists():
        raise FileExistsError(out)
    before = protected_inputs(ROOT)
    review = yaml.safe_load((ROOT / REVIEW).read_text())
    validate_review(review)
    inputs = {str(p): digests(ROOT / p)["sha256"] for p in (PLAN, REVIEW, COMMON)}
    manifest = json.loads((ROOT / SNAPSHOTS / "manifest.json").read_text())
    for item in manifest:
        path = SNAPSHOTS / item["path"]
        observed = digests(ROOT / path)["sha256"]
        if observed != item["sha256"]:
            raise ValueError("repository snapshot changed")
        inputs[str(path)] = observed
    for spec in review["sources"].values():
        path = Path(spec["path"])
        inputs[str(path)] = digests(ROOT / path)["sha256"]
        source_text(ROOT, spec)
    anchor_checks = []
    for item in review["source_checks"]:
        content = source_text(ROOT, review["sources"][item["source"]], item.get("section"))
        if normalized(item["contains"]) not in content:
            raise ValueError(f"source anchor not found: {item}")
        anchor_checks.append({"source": item["source"], "section": item.get("section"), "matched": True})
    common = json.loads((ROOT / COMMON).read_text())
    if common["status"] != "complete" or common["checks"]["baseline_curves"] != 40:
        raise ValueError("prior raw validation not complete")
    for path, expected in common["analysis_sources"].items():
        if digests(ROOT / path)["sha256"] != expected:
            raise ValueError("raw validation source code changed")
    for asset in common["source_assets"]:
        path = ROOT / ASSETS / asset["name"]
        if path.stat().st_size != asset["bytes"] or digests(path)["sha256"] != asset["sha256"]:
            raise ValueError("raw input identity mismatch")
    left = [r for r in common["lencioni_curves"] if r["muscle"] == "tibialis anterior"]
    if len(left) != 10 or any(r["side"] != "RX" for r in left):
        raise ValueError("selected-side scope differs from reviewed scope")
    population = {"studies": 2, "participants": 20, "muscles": 2,
                  "lencioni_cycles": 50, "gait120_cycles": common["checks"]["gait120_cycles"],
                  "new_independent_participants": 0}
    use_results = gates(review)
    gained = [{"study": r["study"], "attribute": r["attribute"], "label": r["label"]}
              for r in review["rows"] if r["P0"] != "confirmed" and r["P1"] == "confirmed"]
    retained = [r for r in review["rows"] if r["P0"] == "confirmed" and r["P1"] == "confirmed"]
    if before != protected_inputs(ROOT) or any(digests(ROOT / p)["sha256"] != h for p, h in inputs.items()):
        raise ValueError("protected input changed")
    result = {
        "analysis": "scientific_reuse_source_attribution_v001", "status": "completed_bounded_review",
        "review_type": review["review_type"], "population": population,
        "sources": review["sources"], "rows": review["rows"], "use_results": use_results,
        "newly_confirmed_in_reviewed_materials": gained,
        "already_confirmed_P0_count": len(retained),
        "source_anchor_checks": anchor_checks, "inputs": inputs,
        "protected_inputs": before, "protected_inputs_unchanged": True,
        "code_sha256": digests(Path(__file__))["sha256"],
        "not_reviewed": ["Demographic/anthropometric spreadsheet contents", "other participants, tasks or releases", "all hidden MAT object metadata", "current replacement of cached article XML"],
        "limits": ["Manual source review with automated consistency/gate calculation; no LLM or blind extraction accuracy test.",
                   "Unconfirmed means not established in inspected materials, not globally absent.",
                   "Necessary information gates do not prove biological equivalence or execute GUI permissions.",
                   "No new waveform, treatment effect or independent cohort generated.",
                   "Attribute counts depend on this review schema; do not interpret as dataset reuse rates.",
                   "Article snapshots are cached; repository metadata snapshots were retrieved in this run."],
    }
    translate = {"confirmed": "確認", "partial": "一部確認", "unconfirmed": "未確認", "conflict": "矛盾"}
    lines = ["# 文献情報を追加すると、何が比較可能になるか", "",
             "2研究・各10参加者の選択済みEMGを対象に、公開ファイル・リポジトリ・付属コード（P0）と、関連原著を加えた情報（P1）を出典に基づいて点検した。内容判断は解析者による精査を含む。ソフトウェアの無人解釈性能や公共EMG全体の再利用率の評価ではない。", "",
             "## 用途別の変化", "", "| 用途 | P0 | P1 |", "|---|---|---|"]
    for name, use in review["uses"].items():
        pair = [next(x for x in use_results if x["use"] == name and x["level"] == level) for level in ("P0", "P1")]
        lines.append(f"| {use['label']} | {pair[0]['status']} | {pair[1]['status']} |")
    lines += ["", "タイミング解析のP0未充足項目は、Lencioniの表面EMGという仕様・基準脚の役割・周期境界の意味、Gait120の基準脚と周期境界の意味。P1で根拠が揃っても、処理依存性や研究背景の違いは残る。振幅は単位換算式（Lencioni mV→Vは×0.001）を定義できるが、共通の生理学的基準は確認できず直接比較を認めない。単位換算を新たに全波形へ実行した結果ではない。", "",
              "## 出典対応表", "", "| 研究 | 情報 | P0 | P1 | 値・意味 | 資料内位置 |", "|---|---|---|---|---|---|"]
    for r in review["rows"]:
        lines.append(f"| {r['study']} | {r['label']} | {translate[r['P0']]} | {translate[r['P1']]} | {r['value']} | {r['locator']} |")
    lines += ["", "## 文献追加として数えない情報", "",
              "Gait120の表面EMG・右下肢は公開リポジトリの説明に明記される。Lencioniの利き脚はコレクションに記載され、選択した10人全員のRXは公開作図コードのRightとの対応でも確認できる。Gait120の切り出し式と詳細処理は付属コードにある。これらは文献にも記載されるが、P0ですでに確認可能なため文献追加の成果ではない。", "",
              "旧24→43属性の集計は解析者設定を含み、今回のP0/P1効果には読み替えない。今回も項目の分け方に依存する件数を再利用率として使わない。", "",
              "## 残る限界", "",
              "資料は周期や単位の意味を補うが、共通の振幅基準や取得されていない前後信号を作り出さない。前段階の共通処理比較では前脛骨筋の重心差が処理に依存したため、根拠要件の充足だけで科学的比較が完成したとはしない。", "",
              "今回の未確認は精査した資料の範囲に限る。人口統計・身体寸法の表計算ファイル、全MATオブジェクト内のメタデータ、他の参加者・課題・版は網羅精査していない。原著は保存済みXMLで、リポジトリ情報は今回取得したスナップショット。文書の確認に解析者の判断を含み、自動化率やLLM精度を示さない。", "",
              "正式本文・図・旧結果を保持した。具体的な出典、適用範囲、確認状態の注記はreport.jsonと判定用YAMLに保存。", "",
              "## 原資料へのリンク", "",
              "- [Lencioni公開コレクション](https://doi.org/10.6084/m9.figshare.c.4494755.v1)",
              "- [Lencioni原著](https://doi.org/10.1038/s41597-019-0323-z)",
              "- [Gait120公開リリース](https://doi.org/10.6084/m9.figshare.27677016.v1)",
              "- [Gait120原著](https://doi.org/10.1038/s41597-025-05391-0)"]
    out.mkdir(parents=True, exist_ok=False)
    (out / "report.json").write_text(json.dumps(result, ensure_ascii=False, indent=2) + "\n")
    (out / "REPORT.md").write_text("\n".join(lines) + "\n")
    print(json.dumps({"population": population, "gained": gained, "uses": use_results}, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    run(args.output if args.output.is_absolute() else ROOT / args.output)
