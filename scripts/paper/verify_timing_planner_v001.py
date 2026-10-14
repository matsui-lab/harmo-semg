"""Real registered Subject 1 replay; not unseen-study validation."""
import argparse
import hashlib
import json
from pathlib import Path
import numpy as np
from scipy.io import loadmat
from harmo.timing_workflow import checked_profile
from harmo.timing_planner import plan,execute,sha
from harmo.physiology_control import preprocess_study38_cycle

p=argparse.ArgumentParser()
p.add_argument('--root',required=True);p.add_argument('--output',required=True)
args=p.parse_args();root=Path(args.root).resolve();out=Path(args.output);out.mkdir(parents=True,exist_ok=False)
protocol_path=root/'paper/physiology_positive_control_protocol.yaml';protocol=checked_profile(protocol_path)
source=root/'downloads/additional_framework_v001_2026.09.12-r1/Subject1.mat'
assert hashlib.md5(source.read_bytes()).hexdigest()==protocol['source_assets']['study_38']['Subject1.mat']['md5']
s=loadmat(source,squeeze_me=True,struct_as_record=False)['s']
assert str(s.EMGSide).strip()=='RX'
labels=[str(x).strip() for x in np.atleast_1d(s.EmgVarName)]
cycles=[(i,t) for i,t in enumerate(np.atleast_1d(s.Data).ravel()) if str(t.Task).strip()=='Walking' and str(t.Foot).strip()=='RX'][:5]
assert len(cycles)==5
rows=[]
for i,t in cycles:
    for muscle,definition in protocol['muscles'].items():
        values=np.asarray(t.EMG,dtype=float)[labels.index(definition['study_38_label']),:]
        name='cycle-%03d-%s'%(i,muscle.replace(' ','-'))
        path=(out/(name+'.npz')).resolve();np.savez(path,values=values)
        fs=float(s.EMGFreq)
        state=dict(task='walking',purpose='activation-timing',processing_protocol='registered-study38-gait120',
            cycle_boundary='same-side-heel-strike-to-heel-strike',muscle=muscle,side='right',representation='raw',sampling_rate_hz=fs)
        spec=dict(sample_path=str(path),sample_sha256=sha(path),sampling_rate_hz=fs,
            facts={k:dict(value=v,status='confirmed',evidence=dict(path=str(protocol_path),sha256=sha(protocol_path),
                scope_reason='Registered Subject1 first five Walking/Foot=EMGSide=RX cycles; checked original MAT hash and channel labels.')) for k,v in state.items()})
        proposal=plan(spec,protocol_path)
        execute(proposal,out/name)
        with np.load(out/name/'timing.npz') as result:
            equal=np.array_equal(result['values'],preprocess_study38_cycle(values,fs,protocol['study_38_envelope']))
        rows.append(dict(cycle_index=i,muscle=muscle,exact=equal,steps=len(proposal['steps'])))
summary=dict(source_sha256=sha(source),comparisons=rows,all_exact=all(r['exact'] for r in rows),
             scientific_approval=False,scope='One previously used participant, five cycles, two muscles; not independent validation.')
(out/'summary.json').write_text(json.dumps(summary,indent=2))
print(json.dumps(dict(comparisons=len(rows),all_exact=summary['all_exact'])))
raise SystemExit(0 if summary['all_exact'] else 1)
