"""Check actual batch artifacts, old numerical results, and a fresh execution."""
import argparse
from pathlib import Path
import numpy as np
from harmo.walking_plan import read, save, verify, sha
from harmo.walking_execute import execute
from harmo.general_workflow import check_artifacts


def main():
    p=argparse.ArgumentParser()
    p.add_argument('--batch',type=Path,required=True)
    p.add_argument('--fresh',type=Path,required=True)
    p.add_argument('--baseline',type=Path,required=True)
    p.add_argument('--output',type=Path,required=True)
    a=p.parse_args();a.output.mkdir(parents=True,exist_ok=False)
    batch=read(a.batch);fresh=read(a.fresh)
    manifests=[]
    for entry in batch['entries']+fresh['entries']:
        folder=Path(entry['output']);receipt=read(folder/'completion.json')
        verify(receipt['inputs']);check_artifacts(folder,receipt['artifacts'])
        manifests.append(dict(path=str(folder/'completion.json'),sha256=sha(folder/'completion.json')))
    folder=Path(batch['entries'][0]['output'])
    result=read(folder/'processing/diagnostic.json');old=read(a.baseline/'diagnostic.json')
    key=lambda c:(c['record'],c['channel'],c['side'],c['anchor'])
    baseline={key(c):c for c in old['curves']};new={key(c):c for c in result['curves']}
    assert set(baseline)==set(new)
    for k,c in new.items():
        previous=baseline[k]
        for field in ('median','q25','q75','cycles'):
            assert c[field]==previous[field]
        with np.load(a.baseline/(previous['key']+'.npz')) as left,np.load(folder/'processing'/(c['key']+'.npz')) as right:
            np.testing.assert_array_equal(left['cycles'],right['cycles'])
            np.testing.assert_array_equal(left['phase'],right['phase'])
    repeated=execute(read(folder/'plan.json'),a.output/'repeat')
    assert result==repeated
    for c in result['curves']:
        with np.load(folder/'processing'/(c['key']+'.npz')) as left,np.load(a.output/'repeat'/(c['key']+'.npz')) as right:
            for field in left.files:np.testing.assert_array_equal(left[field],right[field])
    fresh_folder=Path(fresh['entries'][0]['output']);fr=read(fresh_folder/'processing/diagnostic.json')
    acquired_records=sorted({c['record'] for c in fr['curves']})
    old_records={c['record'] for c in result['curves']}
    for c in fr['curves']:
        if key(c) in new:
            assert c['median']==new[key(c)]['median']
    result=dict(version='general-batch-validation/1',
        unchanged_baseline_curves=len(new), exact_repeated_curves=len(repeated['curves']),
        verified_job_manifests=manifests, fresh_records=acquired_records,
        additional_record_names=sorted(set(acquired_records)-old_records),
        fresh_curves=len(fr['curves']),fresh_comparisons=len(fr['comparisons']),
        minimum_cycles=min(c['cycles'] for c in fr['curves']),maximum_cycles=max(c['cycles'] for c in fr['curves']),
        source_bindings_current=True, independent_study_validation=False,
        physiological_event_ground_truth=False, LLM_accuracy_measured=False,
        human_time_saving_measured=False,scientific_approval=False,
        script_sha256=sha(__file__))
    save(a.output/'validation.json',result)
    print({k:v for k,v in result.items() if k!='verified_job_manifests'})


if __name__=='__main__':main()
