"""Acquire with PhysioIO, then replay the unchanged frozen conversion mappings."""
import argparse
import json
from pathlib import Path
from harmo.repository_bridge import download_asset, repository_request
from harmo.validation_corpus import load_validation_corpus, download_validation_corpus_assets, execute_validation_corpus
from harmo.validation_study import load_registry, write_json, write_rows


def compare_frozen(mappings, reference):
    """Compare only executed mappings; a subset is never a full-corpus pass."""
    expected = {row["mapping_id"]: row for row in reference}
    differences = []
    seen = set()
    fields = ("source_sha256", "normalized_sample_sha256", "selected_member", "outcome")
    for row in mappings:
        mid = row["mapping_id"]
        if mid in seen:
            differences.append(dict(mapping_id=mid, field="duplicate_mapping"))
        seen.add(mid)
        old = expected.get(mid)
        if old is None:
            differences.append(dict(mapping_id=mid, field="missing_reference"))
            continue
        for field in fields:
            if row.get(field) != old.get(field) or row.get(field) is None:
                differences.append(dict(mapping_id=mid, field=field,
                                        expected=old.get(field), actual=row.get(field)))
    return dict(compared_mappings=len(mappings), differences=differences,
                pass_for_executed_mappings=bool(mappings) and not differences)


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--root",default=str(Path(__file__).resolve().parents[2]))
    p.add_argument("--data-root",required=True)
    p.add_argument("--output",required=True)
    p.add_argument("--r-library",default="")
    p.add_argument("--plan-only",action="store_true")
    p.add_argument("--resume-downloads", action="store_true", help="Resume URL/size/checksum-bound HTTP ranges; leave name-only PhysioIO partial files unused and unchanged")
    p.add_argument("--asset", action="append", help="Optional study:asset validation subset; never reported as the full 78 mappings")
    p.add_argument("--max-download-bytes",type=int,default=1024**3,
                   help="Preflight cap on declared missing asset sizes, not a streaming limit")
    args = p.parse_args()
    root, data, out = Path(args.root).resolve(), Path(args.data_root).resolve(), Path(args.output).resolve()
    registry = load_registry(root/"paper/validation_registry.yaml")
    corpus = load_validation_corpus(root/"paper/validation_corpus.yaml",root,registry)
    if args.asset:
        selected = set(args.asset)
        available = {sid+":"+aid for sid,s in corpus["studies"].items() for aid in s["assets"]}
        if not selected <= available: raise ValueError("Unknown asset selectors: "+str(selected-available))
        import copy
        corpus=copy.deepcopy(corpus)
        corpus["studies"]={sid:dict(s,assets={aid:a for aid,a in s["assets"].items() if sid+":"+aid in selected})
                           for sid,s in corpus["studies"].items() if any(sid+":"+aid in selected for aid in s["assets"])}
        corpus["mapping_denominator"]=sum(len(a["expected_mappings"]) for s in corpus["studies"].values() for a in s["assets"].values())
    assets = [dict(asset,study_id=sid,asset_id=aid) for sid,study in corpus["studies"].items() for aid,asset in study["assets"].items()]
    missing = sum(a["size_bytes"] for a in assets if not (data/a["local_name"]).exists())
    plan = dict(scope="selected-asset-subset" if args.asset else "full-frozen-corpus",studies=len(corpus["studies"]),mappings=corpus["mapping_denominator"],missing_declared_bytes=missing,
                assets=[dict(study=a["study_id"],asset=a["asset_id"],cached=(data/a["local_name"]).exists(),
                             backend="checksum-bound-http-ranges" if args.resume_downloads else "PhysioIO" if repository_request(a) else "existing-checksum-bound-http") for a in assets])
    print(json.dumps(plan,indent=2),flush=True)
    if args.plan_only: return 0
    if args.max_download_bytes < 0 or missing > args.max_download_bytes:
        raise ValueError("Declared missing downloads exceed budget; inspect --plan-only then set --max-download-bytes")
    out.mkdir(parents=True,exist_ok=False)
    write_json(out/"plan.json",plan)
    acquisition = download_validation_corpus_assets(corpus,data,
        asset_downloader=lambda a,d:download_asset(a,d,args.r_library,resume_downloads=args.resume_downloads))
    write_json(out/"acquisition.json",acquisition)
    sources,mappings,summary = execute_validation_corpus(root=root,corpus=corpus,data_root=data,work_root=out/"work",allow_subset=bool(args.asset))
    summary["automation_scope"]=plan["scope"]
    write_rows(out/"source_inventory.json",sources)
    write_rows(out/"mapping_outcomes.json",mappings)
    comparison = compare_frozen(mappings, json.loads((root/"paper/results/validation/mapping_outcomes.json").read_text()))
    write_json(out/"frozen_comparison.json",comparison)
    summary["matches_frozen_executed_mappings"] = comparison["pass_for_executed_mappings"]
    write_json(out/"corpus_execution.json",summary)
    print(json.dumps(summary,indent=2))
    return 0 if summary["trust_ready"] and comparison["pass_for_executed_mappings"] else 2


if __name__ == "__main__":
    raise SystemExit(main())
