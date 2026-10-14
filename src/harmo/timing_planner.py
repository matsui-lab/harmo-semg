"""Bounded state-space planning with frozen, executable EMG operations.

Confirmed state is a caller-supplied assertion, not inferred scientific truth.
This module never grants release permission or updates catalog trust.
"""
from collections import deque
import hashlib
import json
from pathlib import Path
import numpy as np
import scipy
from scipy import signal
from harmo.physiology_control import _hampel, _normalise_cycle
from harmo.timing_workflow import checked_profile

OPERATIONS=(
    ('mean-center','raw','centered'),
    ('bandpass','centered','filtered'),
    ('rectify','filtered','rectified'),
    ('hampel','rectified','cleaned'),
    ('rms','cleaned','envelope'),
    ('phase-normalize','envelope','phase-envelope'),
)
VERSION='bounded-timing-planner/1'


def sha(path):return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def plan(spec,protocol_path,max_depth=6):
    if type(max_depth) is not int or not 1<=max_depth<=6:raise ValueError('depth_must_be_1_to_6')
    protocol=checked_profile(protocol_path)
    reasons=[]
    if 'catalog_origin' in spec:
        from harmo.catalog_timing_plan import validate_origin
        try:
            validate_origin(spec)
        except (ValueError, OSError, KeyError, TypeError) as exc:
            reasons.append('invalid_catalog_origin:'+str(exc))
    expected=dict(task='walking',cycle_boundary='same-side-heel-strike-to-heel-strike',
                  processing_protocol='registered-study38-gait120',purpose='activation-timing')
    for key,value in expected.items():
        fact=spec.get('facts',{}).get(key,{})
        if fact.get('status')!='confirmed' or fact.get('value')!=value:
            reasons.append('confirmed_'+key+'_required')
    for key in ('muscle','side','representation'):
        fact=spec.get('facts',{}).get(key,{})
        if fact.get('status')!='confirmed' or not fact.get('value'):
            reasons.append('confirmed_'+key+'_required')
    # Verify evidence bytes; a confirmed flag alone is not enough to plan.
    for key,fact in spec.get('facts',{}).items():
        try:
            proof=fact['evidence']
            if not proof.get('scope_reason') or sha(proof['path'])!=proof['sha256']:
                raise ValueError('evidence')
        except (KeyError,OSError,ValueError):reasons.append('missing_or_changed_evidence:'+key)
    values=spec.get('facts',{})
    if values.get('side',{}).get('value')!='right':reasons.append('registered_profile_requires_right_side')
    if values.get('muscle',{}).get('value') not in ('tibialis anterior','medial gastrocnemius'):
        reasons.append('muscle_outside_registered_profile')
    start=values.get('representation',{}).get('value')
    if start not in ('raw','envelope'):reasons.append('unsupported_input_representation')
    if start=='raw':
        fs=spec.get('sampling_rate_hz')
        fact=values.get('sampling_rate_hz',{})
        if fact.get('status')!='confirmed' or fact.get('value')!=fs:
            reasons.append('confirmed_sampling_rate_required')
        if not isinstance(fs,(int,float)) or not np.isfinite(fs) or fs<=700:
            reasons.append('sampling_rate_must_exceed_700_hz')
    try:
        if sha(spec['sample_path'])!=spec['sample_sha256']:reasons.append('samples_changed')
    except (KeyError,OSError):reasons.append('samples_missing')
    result=dict(version=VERSION,input=json.loads(json.dumps(spec)),protocol_path=str(Path(protocol_path).resolve()),
                processing_settings=protocol['study_38_envelope'],
                implementation_sha256=sha(__file__),numpy_version=np.__version__,scipy_version=scipy.__version__,
                primitive_implementation_sha256=sha(Path(__file__).with_name('physiology_control.py')),
                protocol_sha256=protocol['protocol_sha256'],max_depth=max_depth,steps=[],
                status='held',reasons=sorted(set(reasons)),scientific_approval=False)
    if reasons:return result
    queue=deque([(start,[])]);visited={start}
    while queue:
        state,steps=queue.popleft()
        if state=='phase-envelope':
            result.update(status='planned',steps=steps);return result
        if len(steps)>=max_depth:continue
        for op,pre,post in OPERATIONS:
            if state==pre and post not in visited:
                visited.add(post)
                queue.append((post,steps+[dict(operation=op,requires=pre,produces=post)]))
    result['reasons']=['no_supported_plan_within_depth']
    return result


def execute(saved,output):
    rebuilt=plan(saved['input'],saved['protocol_path'],saved['max_depth'])
    if rebuilt!=saved or saved['status']!='planned':raise ValueError('plan_changed_or_held')
    settings=checked_profile(saved['protocol_path'])['study_38_envelope']
    spec=saved['input']
    with np.load(spec['sample_path'],allow_pickle=False) as payload:
        values=np.asarray(payload['values'],dtype=float)
        if values.ndim!=1:raise ValueError('one_presegmented_channel_cycle_required')
        values=values.copy()
    if values.size<2 or not np.isfinite(values).all():raise ValueError('finite_cycle_required')
    if spec['facts']['representation']['value']=='raw' and values.size<16:
        raise ValueError('raw_cycle_too_short')
    fs=spec.get('sampling_rate_hz');trace=[]
    for step in saved['steps']:
        op=step['operation']
        if op=='mean-center':values=values-float(np.mean(values))
        elif op=='bandpass':
            sos=signal.butter(settings['butterworth_order'],settings['bandpass_hz'],btype='bandpass',fs=fs,output='sos')
            values=signal.sosfiltfilt(sos,values)
        elif op=='rectify':values=np.abs(values)
        elif op=='hampel':
            window=max(3,int(round(settings['hampel_window_seconds']*fs)))
            if window%2==0:window+=1
            values=_hampel(values,window,settings['hampel_sigma'])
        elif op=='rms':
            window=max(1,int(round(settings['rms_window_seconds']*fs)))
            if window>len(values):raise ValueError('rms_window_exceeds_cycle')
            values=np.sqrt(np.convolve(values*values,np.ones(window)/float(window),mode='same'))
        elif op=='phase-normalize':values=_normalise_cycle(values,settings['normalized_grid_points'])
        if not np.isfinite(values).all():raise ValueError('nonfinite_operation_output')
        trace.append(dict(step,points=len(values),values_sha256=hashlib.sha256(values.tobytes()).hexdigest()))
    if values.shape!=(101,):raise ValueError('target_grid_not_reached')
    # Revalidate input/evidence before publishing any output.
    if plan(spec,saved['protocol_path'],saved['max_depth'])!=saved:raise ValueError('inputs_changed_during_execution')
    out=Path(output);out.mkdir(parents=True,exist_ok=False)
    np.savez(out/'timing.npz',values=values,phase=np.linspace(0,100,101))
    report=dict(plan=saved,trace=trace,output_sha256=sha(out/'timing.npz'),
                scientific_approval=False,scope='One registered presegmented cycle; diagnostic output, not authorized scientific export.')
    (out/'execution.json').write_text(json.dumps(report,indent=2))
    return report
