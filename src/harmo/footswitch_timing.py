"""Diagnostic EMG curves aligned to observed footswitch state changes.

Electrical-state anchors are not automatically heel strike or toe off. This
path never alters catalog trust or authorizes scientific/amplitude pooling.
"""
import argparse
import csv
import hashlib
from itertools import combinations
import json
from pathlib import Path
import re
import sqlite3

import numpy as np
import scipy
from scipy import signal

from harmo.automatic_workflow import collect_saved_documents
from harmo.document_contract import resolve, fingerprint
from harmo.physiology_control import _normalise_cycle
from harmo.public_export import load_record, checked_samples

DEFAULT = dict(version='footswitch-diagnostic/1', bandpass_hz=[20.0,350.0], filter_order=4,
               rms_seconds=0.125, minimum_state_seconds=0.02, cycle_seconds=[0.5,3.0],
               edge_margin_seconds=0.5, minimum_cycles=5, grid_points=101,
               additive_level_relative_tolerance=0.001,
               input_representation='unrectified-source-EMG-assumption',
               phase_reference='observed-electrical-state-rising-edge',
               amplitude_use='within-cycle-peak-scaling-for-shape-only')


def sha(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def save(path, value):
    with Path(path).open('x') as stream:
        json.dump(value,stream,indent=2,ensure_ascii=False,allow_nan=False)


def profile(path=None):
    value=json.loads(Path(path).read_text()) if path else json.loads(json.dumps(DEFAULT))
    if set(value)!=set(DEFAULT):raise ValueError('exact_profile_fields_required')
    for key in ('version','input_representation','phase_reference','amplitude_use','grid_points'):
        if value[key]!=DEFAULT[key]:raise ValueError('unsupported_profile_semantics:'+key)
    lo,hi=value['bandpass_hz'];minimum,maximum=value['cycle_seconds']
    numeric=[lo,hi,minimum,maximum,value['rms_seconds'],value['minimum_state_seconds'],value['edge_margin_seconds'],value['additive_level_relative_tolerance']]
    if not all(type(v) in (float,int) and np.isfinite(v) for v in numeric):raise ValueError('finite_profile_required')
    if not 0<lo<hi or not 0<minimum<maximum<=10:raise ValueError('invalid_frequency_or_cycle_bounds')
    if not .001<=value['rms_seconds']<=1 or not .001<=value['minimum_state_seconds']<=.2:raise ValueError('invalid_window')
    if not value['rms_seconds']<=value['edge_margin_seconds']<=5:raise ValueError('edge_margin_must_cover_RMS_window')
    if not 0<value['additive_level_relative_tolerance']<=.01:raise ValueError('invalid_level_tolerance')
    if type(value['filter_order']) is not int or not 1<=value['filter_order']<=8:raise ValueError('invalid_filter_order')
    if type(value['minimum_cycles']) is not int or not 3<=value['minimum_cycles']<=100:raise ValueError('invalid_minimum_cycles')
    return value


def state_masks(values, relative_tolerance=0.001):
    x=np.asarray(values,dtype=float)
    if x.ndim!=1 or not len(x) or not np.isfinite(x).all():raise ValueError('finite_1D_footswitch_required')
    levels=np.unique(x)
    if not 2<=len(levels)<=4:raise ValueError('requires_2_to_4_discrete_levels_not_continuous_force')
    shifted=levels-levels[0]
    if len(levels)==4 and abs(shifted[3]-shifted[1]-shifted[2])>relative_tolerance*shifted[3]:
        raise ValueError('four_levels_not_consistent_with_additive_states')
    masks={}
    if len(levels)==2:
        masks['upper-state']=(x==levels[1])
    else:
        for i,name in ((1,'lower-component'),(2,'upper-component')):
            masks[name]=(x==levels[i]) | ((x==levels[3]) if len(levels)==4 else False)
    return levels,masks


def detect_cycles(mask, fs, settings):
    mask=np.asarray(mask,dtype=bool)
    if mask.ndim!=1 or not len(mask) or not np.isfinite(fs) or fs<=0:raise ValueError('valid_mask_and_rate_required')
    changes=np.diff(np.r_[False,mask,False].astype(int))
    starts=np.flatnonzero(changes==1);ends=np.flatnonzero(changes==-1)
    minimum=int(np.ceil(settings['minimum_state_seconds']*fs))
    onsets=[];dropped=[];previous_end=0
    for start,end in zip(starts,ends):
        reason=None
        if start==0:reason='initial_state_has_no_observed_rising_edge'
        elif end-start<minimum:reason='short_active_run'
        elif start-previous_end<minimum:reason='short_inactive_run'
        if reason:dropped.append(dict(index=int(start),reason=reason))
        else:onsets.append(int(start))
        previous_end=int(end)
    cycles=[];rejected=[];margin=int(np.ceil(settings['edge_margin_seconds']*fs))
    for start,end in zip(onsets,onsets[1:]):
        duration=(end-start)/fs;reason=None
        if not settings['cycle_seconds'][0]<=duration<=settings['cycle_seconds'][1]:reason='duration_outside_declared_window'
        elif start<margin or end>=len(mask)-margin:reason='record_edge_margin'
        elif any(start<r['index']<end for r in dropped):reason='intervening_rejected_transition'
        item=dict(start=start,end=end,duration_seconds=duration)
        if reason:rejected.append(dict(item,reason=reason))
        else:cycles.append(item)
    return dict(onsets=onsets,cycles=cycles,rejected_cycles=rejected,rejected_transitions=dropped,
                status='sufficient_for_diagnostic' if len(cycles)>=settings['minimum_cycles'] else 'held',
                physiological_event_identity='unverified')


def envelope(values, fs, settings):
    x=np.asarray(values,dtype=float)
    if x.ndim!=1 or len(x)<32 or not np.isfinite(x).all():raise ValueError('finite_source_EMG_required')
    if settings['bandpass_hz'][1]>=fs/2:raise ValueError('bandpass_above_nyquist')
    sos=signal.butter(settings['filter_order'],settings['bandpass_hz'],btype='bandpass',fs=fs,output='sos')
    filtered=signal.sosfiltfilt(sos,x-x.mean())
    window=max(1,int(round(settings['rms_seconds']*fs)))
    if window%2==0:window+=1
    if window>=len(x):raise ValueError('RMS_window_exceeds_record')
    result=np.sqrt(np.maximum(0,np.convolve(filtered*filtered,np.ones(window)/window,mode='same')))
    if not np.isfinite(result).all():raise ValueError('nonfinite_envelope')
    return result


def foot_side(channel):
    if channel.sensor_type=='footswitch' and channel.side in ('left','right'):return channel.side
    match=re.fullmatch(r'(RT|LT|RIGHT|LEFT)[ ._-]*FOOT[ ._-]*SWITCH',channel.name,re.I)
    return {'RT':'right','RIGHT':'right','LT':'left','LEFT':'left'}[match[1].upper()] if match else None


def peak(curve):
    x=np.asarray(curve)[:-1]
    return int(np.argmax(x))


def compare(left,right):
    x=np.asarray(left['median'])[:-1];y=np.asarray(right['median'])[:-1]
    correlation=float(np.corrcoef(x,y)[0,1]) if x.std()>1e-12 and y.std()>1e-12 else None
    return dict(left=left['record'],right=right['record'],muscle=left['muscle'],side=left['side'],anchor=left['anchor'],
                left_peak_percent=peak(left['median']),right_peak_percent=peak(right['median']),
                circular_peak_difference_percent=(peak(right['median'])-peak(left['median'])+50)%100-50,
                zero_lag_correlation=correlation,shape_RMSE=float(np.sqrt(np.mean((x-y)**2))),
                scientific_approval=False,interpretation='descriptive-record-to-record-shape-only')


def run(summary_path, output, protocol=None):
    summary_path=Path(summary_path).resolve();settings=profile(protocol);out=Path(output).resolve()
    out.mkdir(parents=True,exist_ok=False)
    inputs={str(summary_path):sha(summary_path),str(Path(__file__).resolve()):sha(__file__)}
    import harmo.physiology_control as primitives
    inputs[str(Path(primitives.__file__).resolve())]=sha(primitives.__file__)
    if protocol:inputs[str(Path(protocol).resolve())]=sha(protocol)
    summary=json.loads(summary_path.read_text());acquired=summary['intake'];evidence=collect_saved_documents(acquired)
    if evidence['held']:raise ValueError('fresh_source_bindings_required')
    datasets={e['dataset'] for e in acquired['entries'] if 'dataset' in e}
    for entry in acquired['entries']:
        if entry.get('source_report'):
            stage=Path(entry['source_report'])
            for path in (stage/'description.html',stage/'source.evidence.json'):
                if path.exists():inputs[str(path.resolve())]=sha(path)
    with sqlite3.connect(Path(acquired['catalog']).resolve().as_uri()+'?mode=ro',uri=True) as db:
        rows=db.execute('SELECT id,dataset,name,location FROM records ORDER BY dataset,name,id').fetchall()
    if len(rows)>100:raise ValueError('diagnostic_record_budget')
    records=[];curves=[];waveforms=[]
    for row in rows:
        if row[1] not in datasets:continue
        item=dict(record_id=row[0],dataset=row[1],record=row[2],status='held',anchors=[],reasons=[])
        records.append(item)
        try:
            bundle,trust,directory=load_record(row);time,values=checked_samples(bundle,directory)
            if values.size>20_000_000:raise ValueError('record_cell_budget')
            for name in ('recording.bundle.json','recording.semantic.bundle.json','recording.semantic.v002.bundle.json','trust.report.json','normalized.npz','contract-revisions.sqlite'):
                path=directory/name
                if path.exists():inputs[str(path.resolve())]=sha(path)
            metadata=json.loads((directory/'record.json').read_text())['metadata']
            for relative,digest in metadata['source_sha256'].items():
                path=(directory.parent.parent/'raw'/relative).resolve()
                if sha(path)!=digest:raise ValueError('source_asset_changed')
                inputs[str(path)]=digest
            observations=[o for d in evidence['documents'] if d['dataset']==row[1] for o in d['result']['observations']]
            bundle,resolution=resolve(bundle,row[2],observations)
            if any(d['status']=='conflicting' for d in resolution['decisions']):raise ValueError('documentary_conflict')
            if bundle.task is None or bundle.task.activity.casefold()!='walking':raise ValueError('source_scoped_walking_required')
            fs=bundle.timebase.sampling_rate_hz
            item.update(source_contract_sha256=fingerprint(bundle),source_sample_sha256=bundle.samples.checksum,
                        task=bundle.task.to_dict(),attribute_resolution=resolution,sampling_rate_hz=fs,trust_unchanged=True)
            feet={}
            for index,ch in enumerate(bundle.channels):
                side=foot_side(ch)
                if side:
                    if side in feet:raise ValueError('ambiguous_footswitch_side')
                    feet[side]=index
            if not feet:raise ValueError('no_side_resolved_footswitch')
            for side,fi in sorted(feet.items()):
                levels,masks=state_masks(values[:,fi],settings['additive_level_relative_tolerance'])
                emg=[(i,ch) for i,ch in enumerate(bundle.channels) if ch.sensor_type=='surface-emg' and ch.side==side and ch.anatomy is not None]
                if not emg:item['reasons'].append('no_source_resolved_EMG_for_'+side);continue
                envelopes={i:envelope(values[:,i],fs,settings) for i,ch in emg}
                for anchor,mask in masks.items():
                    events=detect_cycles(mask,fs,settings)
                    item['anchors'].append(dict(side=side,channel=bundle.channels[fi].name,levels=levels.tolist(),anchor=anchor,**events))
                    if events['status']=='held':continue
                    for ci,ch in emg:
                        samples=np.vstack([_normalise_cycle(envelopes[ci][c['start']:c['end']+1],settings['grid_points']) for c in events['cycles']])
                        key=f'curve-{len(curves):04d}'
                        np.savez(out/(key+'.npz'),phase=np.linspace(0,100,101),cycles=samples)
                        curve=dict(key=key,record_id=row[0],dataset=row[1],record=row[2],side=side,muscle=ch.anatomy.canonical_label,
                                   channel=ch.name,anchor=anchor,cycles=len(samples),median=np.median(samples,axis=0).tolist(),
                                   q25=np.quantile(samples,.25,axis=0).tolist(),q75=np.quantile(samples,.75,axis=0).tolist())
                        curves.append(curve)
                # First 10 s of source signal, downsampled only for the display.
                for ci,ch in emg:
                    for j in range(0,min(len(time),int(10*fs)),max(1,int(fs/300))):
                        waveforms.append(dict(record=row[2],side=side,channel=ch.name,time=float(time[j]),
                            emg=float(values[j,ci]),envelope=float(envelopes[ci][j]),footswitch=float(values[j,fi])))
            item['status']='diagnostic_curves_created' if any(c['record_id']==row[0] for c in curves) else 'held'
        except (ValueError,OSError,KeyError,TypeError) as exc:item['reasons'].append(str(exc))
    comparisons=[];sensitivity=[]
    for a,b in combinations(curves,2):
        if (a['dataset'],a['muscle'],a['side'])!=(b['dataset'],b['muscle'],b['side']):continue
        if a['record_id']!=b['record_id'] and a['anchor']==b['anchor']:
            comparisons.append(compare(a,b))
        if a['record_id']==b['record_id'] and a['anchor']!=b['anchor']:
            sensitivity.append(dict(record=a['record'],muscle=a['muscle'],side=a['side'],
                anchor_a=a['anchor'],anchor_b=b['anchor'],peak_a=peak(a['median']),peak_b=peak(b['median']),
                circular_peak_difference_percent=(peak(b['median'])-peak(a['median'])+50)%100-50))
    # No stored approval is created. Source changes invalidate the whole run.
    if any(sha(path)!=digest for path,digest in inputs.items()):raise ValueError('inputs_changed_during_execution')
    report=dict(version='footswitch-diagnostic/1',profile=settings,inputs=inputs,numpy_version=np.__version__,scipy_version=scipy.__version__,
                records=records,curves=curves,comparisons=comparisons,anchor_sensitivity=sensitivity,
                scientific_approval=False,amplitude_pooling_allowed=False,heel_strike_alignment_verified=False,
                status='diagnostic_only' if curves else 'held',
                limitations=['electrical_state_to_physiological_event_mapping_unverified','source_representation_assumption_requires_review',
                             'no_population_inference','within_cycle_peak_scaling_is_not_amplitude_normalization_for_pooling',
                             'cycle_duration_and_debounce_filters_are_development_QC_not_physiological_validation'])
    save(out/'diagnostic.json',report)
    with (out/'curves.csv').open('x') as stream:
        writer=csv.DictWriter(stream,fieldnames=['record','side','muscle','anchor','cycles','phase','median','q25','q75']);writer.writeheader()
        for c in curves:
            for i in range(101):writer.writerow(dict({k:c[k] for k in ('record','side','muscle','anchor','cycles')},phase=i,median=c['median'][i],q25=c['q25'][i],q75=c['q75'][i]))
    if waveforms:
        with (out/'waveforms.csv').open('x') as stream:
            writer=csv.DictWriter(stream,fieldnames=list(waveforms[0]));writer.writeheader();writer.writerows(waveforms)
    return dict(output=str(out),records=len(records),curves=len(curves),comparisons=len(comparisons),scientific_approval=False)


def main():
    p=argparse.ArgumentParser(description=__doc__);p.add_argument('--summary',required=True);p.add_argument('--output',required=True);p.add_argument('--protocol')
    a=p.parse_args();print(json.dumps(run(a.summary,a.output,a.protocol),indent=2))


if __name__=='__main__':main()
