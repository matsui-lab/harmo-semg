"""Registered comparisons using revalidated catalog-planner curves."""
import argparse
import json
from pathlib import Path
import numpy as np
from harmo.registered_timing import run
from harmo.timing_export import export_pair
from harmo.timing_planner import sha


def main():
    parser=argparse.ArgumentParser()
    for key in ('root','replay','baseline','output'):parser.add_argument('--'+key,required=True,type=Path)
    args=parser.parse_args();root=args.root.resolve();out=args.output.resolve();base=args.baseline.resolve()
    out.mkdir(parents=True,exist_ok=False)
    sources=root/'downloads/additional_framework_v001_2026.09.12-r1'
    result=run(sources,sources/'Gait120_011_to_020.zip',out/'resolution',
               root/'paper/physiology_positive_control_protocol.yaml',base/'physiology.result.json',
               out/'resolution.result.json',planner_replay=args.replay.resolve())
    comparisons=[]
    for index in sorted((out/'resolution/repeat-1').glob('*/*/resolved/comparability.resolution.index.json')):
        resolved=index.parent
        baseline=base/'resolution/repeat-1'/resolved.relative_to(out/'resolution/repeat-1')
        actual=json.loads((resolved/'comparability.after.json').read_text())
        old=json.loads((baseline/'comparability.after.json').read_text())
        statuses={role:json.loads((resolved/role/'release.decision.json').read_text())['status'] for role in ('left','right')}
        baseline_statuses={role:json.loads((baseline/role/'release.decision.json').read_text())['status'] for role in ('left','right')}
        export_pair(resolved,resolved/'selected-signals')
        with np.load(resolved/'selected-signals/timing.npz') as new,np.load(baseline/'selected-signals/timing.npz') as prior:
            exact=set(new.files)==set(prior.files) and all(np.array_equal(new[k],prior[k]) for k in prior.files)
        comparisons.append(dict(pair=str(resolved.relative_to(out)),decision_equal=actual['status']==old['status'] and actual['conditions']==old['conditions'],
                                releases=statuses,release_equal=statuses==baseline_statuses,arrays_exact=exact))
    summary=dict(comparisons=comparisons,count=len(comparisons),
                 all_equal=len(comparisons)==20 and all(c['decision_equal'] and c['release_equal'] and c['arrays_exact'] for c in comparisons),
                 reproducible=result['recording_identities_and_decisions_reproducible'],
                 amplitude_not_established=result['all_amplitude_comparability_not_established'],
                 physiology_result_reused_sha256=sha(base/'physiology.result.json'),
                 scope='Registered replay parity; existing physiology endpoint reused, not a fresh scientific or independent validation.')
    (out/'summary.json').write_text(json.dumps(summary,indent=2))
    print(json.dumps({k:v for k,v in summary.items() if k!='comparisons'}))
    if not summary['all_equal']:raise ValueError('comparison_parity_failed')


if __name__=='__main__':main()
