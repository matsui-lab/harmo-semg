"""Re-execution and declared-window sensitivity, no physiological ground truth."""
import argparse
import json
from pathlib import Path
import numpy as np
from harmo.footswitch_timing import run, DEFAULT, sha, save


def main():
    p=argparse.ArgumentParser();p.add_argument('--summary',type=Path,required=True);p.add_argument('--baseline',type=Path,required=True);p.add_argument('--output',type=Path,required=True)
    a=p.parse_args();a.output.mkdir(parents=True,exist_ok=False)
    baseline=json.loads((a.baseline/'diagnostic.json').read_text())
    run(a.summary,a.output/'repeat')
    repeat=json.loads((a.output/'repeat/diagnostic.json').read_text())
    assert baseline==repeat
    checks=[]
    for curve in baseline['curves']:
        with np.load(a.baseline/(curve['key']+'.npz')) as left,np.load(a.output/'repeat'/(curve['key']+'.npz')) as right:
            for key in left.files:np.testing.assert_array_equal(left[key],right[key])
        checks.append(curve['key'])
    results=[]
    key=lambda c:(c['record'],c['side'],c['muscle'],c['anchor'])
    reference={key(c):c for c in baseline['curves']}
    for name,updates in [('debounce40ms',dict(minimum_state_seconds=.04)),('rms50ms',dict(rms_seconds=.05))]:
        settings=dict(DEFAULT,**updates);protocol=a.output/(name+'.json');save(protocol,settings)
        run(a.summary,a.output/name,protocol)
        data=json.loads((a.output/name/'diagnostic.json').read_text());deltas=[]
        for curve in data['curves']:
            old=reference[key(curve)];peak=lambda x:int(np.argmax(x[:-1]))
            deltas.append(dict(record=curve['record'],side=curve['side'],muscle=curve['muscle'],anchor=curve['anchor'],
                               cycles_before=old['cycles'],cycles_after=curve['cycles'],
                               peak_shift_percent=(peak(curve['median'])-peak(old['median'])+50)%100-50,
                               median_curve_max_abs_change=float(np.max(np.abs(np.asarray(curve['median'])-np.asarray(old['median']))))))
        results.append(dict(setting=name,deltas=deltas,comparison_count=len(data['comparisons']),
                            missing_curves=[list(k) for k in reference if k not in {key(c) for c in data['curves']}]))
    save(a.output/'validation.json',dict(repeat_report_identical=True,repeat_curves_exact=checks,
        sensitivity=results,baseline_sha256=sha(a.baseline/'diagnostic.json'),
        scientific_validation=False,event_ground_truth_available=False,independent_study=False))
    print(json.dumps(dict(exact_repeated_curves=len(checks),sensitivity_variants=len(results))))


if __name__=='__main__':main()
