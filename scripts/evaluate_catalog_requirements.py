"""Compare original versus source-enriched policy findings in a bounded sample."""
import argparse
import json
import sqlite3
from collections import Counter
from pathlib import Path
from harmo.public_export import load_record
from harmo.evidence.models import HarmoRecordingBundle
from harmo.evidence.policy import ComparabilityPolicyEngine, ComparabilityRequest
from harmo.purpose_requirements import summarize
from harmo.config.automap import save, require


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--store", type=Path, required=True)
    p.add_argument("--output", type=Path, required=True)
    p.add_argument("--max-pairs", type=int, default=20)
    args = p.parse_args()
    require(0 < args.max_pairs <= 100, "pair_limit")
    require(not args.output.exists(), "output_exists")
    db = sqlite3.connect("file:"+str(args.store.resolve()/"catalog.sqlite")+"?mode=ro", uri=True)
    rows = db.execute("SELECT id,dataset,name,location FROM records ORDER BY name,id").fetchall()
    db.close()
    references, pairs = {}, []
    engine = ComparabilityPolicyEngine()
    for row in rows:
        bundle, trust, directory = load_record(row)
        if bundle.task is None:
            continue
        channel = next((c for c in bundle.channels if c.name == "Tibialis Anterior"), None)
        if channel is None:
            continue
        key = (row[1], bundle.task.activity, channel.side)
        if key not in references:
            references[key] = (row,bundle,trust,channel)
            continue
        left_row, left, lt, lc = references[key]
        original_left = HarmoRecordingBundle.from_dict(json.loads((Path(left_row[3])/"recording.bundle.json").read_text()))
        original_right = HarmoRecordingBundle.from_dict(json.loads((directory/"recording.bundle.json").read_text()))
        item = dict(left=left_row[0], right=row[0], channel=channel.name, task=bundle.task.activity, purposes={})
        for purpose in ("activation-timing", "amplitude-pooling"):
            request = ComparabilityRequest("requirements-"+purpose,purpose,lc.channel_id,channel.channel_id)
            before = engine.evaluate_with_details(original_left,original_right,lt,trust,request)
            after = engine.evaluate_with_details(left,bundle,lt,trust,request)
            item["purposes"][purpose] = dict(before=summarize(before),after=summarize(after),
                before_details=before,after_details=after)
        pairs.append(item)
        if len(pairs) == args.max_pairs:
            break
    args.output.mkdir(parents=True)
    save(args.output / "pairs.json", pairs)
    requirements = sorted({r["action"] for pair in pairs for p in pair["purposes"].values() for r in p["after"]["requirements"]})
    summary = dict(pairs=len(pairs), channel="Tibialis Anterior", requirements=requirements,
        before_statuses=dict(Counter(p["before"]["status"] for pair in pairs for p in pair["purposes"].values())),
        after_statuses=dict(Counter(p["after"]["status"] for pair in pairs for p in pair["purposes"].values())),
        by_purpose={purpose:dict(Counter(pair["purposes"][purpose]["after"]["status"] for pair in pairs))
                    for purpose in ("activation-timing", "amplitude-pooling")},
        scope="Bounded same-dataset, same-task, same-measurement-side reference comparisons; not independent generalization evaluation.")
    save(args.output / "summary.json", summary)
    print(json.dumps(summary,indent=2))


if __name__ == "__main__":
    main()
