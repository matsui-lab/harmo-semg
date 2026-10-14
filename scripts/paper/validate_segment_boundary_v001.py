"""Paired numerical truncation experiment on preserved continuous EMG.

Not event ground truth or independent physiological validation. Conditions are
written before evaluation; no parameters are tuned to make the results pass.
"""
import argparse
import json
from pathlib import Path
import numpy as np
from harmo.footswitch_timing import envelope as legacy,peak
from harmo.segment_envelope import envelope,phase_support,features,POLICY
from harmo.physiology_control import _normalise_cycle
from harmo.walking_plan import sha,digest,save


def summary(values):
    values=np.asarray(values,dtype=float)
    return dict(n=len(values),median=float(np.median(values)),p95=float(np.quantile(values,.95)),maximum=float(np.max(values))) if len(values) else dict(n=0)


def main():
    parser=argparse.ArgumentParser();parser.add_argument('--plan',required=True);parser.add_argument('--output',required=True)
    args=parser.parse_args();path=Path(args.plan).resolve();out=Path(args.output).resolve();out.mkdir(parents=True,exist_ok=False)
    plan=json.loads(path.read_text());identity=dict(plan);assert identity.pop('plan_id')==digest(identity)
    protocol=dict(version='paired-truncation-evaluation/1',records=4,cycles_per_channel_anchor=5,
        selection='evenly spaced indices over stored accepted cycles; duplicate source windows removed',
        numerical_reference='same filter/RMS on full continuous record, then identical interval cropped',
        policies=POLICY,peak_error_reporting_threshold_percent=5,shape_RMSE_reporting_threshold=.1,
        thresholds_are_engineering_not_clinical=True,physiological_ground_truth=False,
        scope='Existing development records; correlated windows and repeat sessions are not independent subjects.')
    save(out/'protocol.json',protocol)
    rows=[];inputs={str(path):sha(path),str(Path(__file__).resolve()):sha(__file__)}
    from harmo.walking_plan import code_bindings,verify
    code_bindings(inputs)
    for record in plan['records'][:4]:
        source=Path(record['row'][3])/'normalized.npz'
        assert sha(source)==record['samples_sha256'];inputs[str(source)]=sha(source)
        with np.load(source,allow_pickle=False) as data:values=data['values']
        fs=record['sampling_rate_hz'];seen=set()
        for channel in record['channels']:
            x=values[:,channel['index']]
            reference=legacy(x,fs,plan['profile'])
            for anchor in record['anchors']:
                if anchor['side']!=channel['side'] or not anchor['cycles']:continue
                indices=np.unique(np.linspace(0,len(anchor['cycles'])-1,5).round().astype(int))
                for index in indices:
                    cycle=anchor['cycles'][index];start,end=cycle['start'],cycle['end']
                    key=(channel['index'],start,end)
                    if key in seen:continue
                    seen.add(key)
                    cropped=x[start:end+1];expected=_normalise_cycle(reference[start:end+1],101)
                    old=_normalise_cycle(legacy(cropped,fs,plan['profile']),101)
                    corrected,meta=envelope(cropped,fs,plan['profile']);new=_normalise_cycle(corrected,101)
                    supported=phase_support(meta,len(cropped));feat=features(new,supported)
                    row=dict(record=record['record'],channel=channel['channel'],anchor=anchor['anchor'],
                        start=start,end=end,fs=fs,duration_seconds=(end-start)/fs,
                        reference_peak_percent=peak(expected),corrected_features=feat,
                        supported_points=int(supported.sum()),methods={})
                    for method,y in (('legacy',old),('available_sample_RMS',new)):
                        err=abs((peak(y)-peak(expected)+50)%100-50)
                        mask=supported
                        interior=float(np.sqrt(np.mean((y[mask]/max(y[mask])-expected[mask]/max(expected[mask]))**2))) if mask.sum()>=3 else None
                        row['methods'][method]=dict(full_cycle_shape_RMSE=float(np.sqrt(np.mean((y-expected)**2))),
                            common_supported_shape_RMSE=interior,global_peak_error_percent=err)
                    provisional=feat['single_peak_percent']
                    row['reported_peak_error_percent']=None if provisional is None else abs((provisional-peak(expected)+50)%100-50)
                    rows.append(row)
    verify(inputs)
    result=dict(protocol=protocol,inputs=inputs,windows=len(rows),records=len({r['record'] for r in rows}),
        methods={method:{metric:summary([r['methods'][method][metric] for r in rows if r['methods'][method][metric] is not None])
            for metric in ('full_cycle_shape_RMSE','common_supported_shape_RMSE','global_peak_error_percent')}
            for method in ('legacy','available_sample_RMS')},
        provisional_peak_coverage=sum(r['reported_peak_error_percent'] is not None for r in rows)/len(rows),
        provisional_peak_errors=summary([r['reported_peak_error_percent'] for r in rows if r['reported_peak_error_percent'] is not None]),
        rows=rows,scientific_approval=False)
    result['provisional_peaks_above_5_percent']=sum(r['reported_peak_error_percent'] is not None and r['reported_peak_error_percent']>5 for r in rows)
    result['by_record']=[dict(record=name,windows=len(group),
        provisional_peaks=sum(r['reported_peak_error_percent'] is not None for r in group),
        errors=summary([r['reported_peak_error_percent'] for r in group if r['reported_peak_error_percent'] is not None]))
        for name in sorted({r['record'] for r in rows}) for group in [[r for r in rows if r['record']==name]]]
    save(out/'validation.json',result)
    print(json.dumps({k:v for k,v in result.items() if k not in ('rows','inputs','protocol')}))


if __name__=='__main__':main()
