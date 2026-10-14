"""Developer-prefilled review aid; explicitly NOT independent ground truth."""
import argparse
import hashlib
import json
from pathlib import Path


def build(packet,profile,output):
    data=json.loads(packet.read_text()); prior=json.loads(profile.read_text())
    records={r['record_id']:r for r in prior['records']}
    labels=[]
    for case in data['cases']:
        record=records[case['case_id']]; semantics=record['record_semantics']
        sources=case['sources']
        def evidence(fragment):
            found=[s for s in sources if fragment in s['text']]
            if len(found)!=1: raise ValueError('unique_reference_excerpt_required: '+fragment)
            s=found[0]
            return dict(source_id=s['source_id'],quote=s['text'])
        parent=next(s for s in sources if s['source_id']=='mat-parent')
        trial=next(s for s in sources if s['source_id'].startswith('mat-trial-'))
        mat_parent=dict(source_id=parent['source_id'],quote=parent['text'])
        mat_trial=dict(source_id=trial['source_id'],quote=trial['text'])
        for target in case['targets']:
            field=target['field']; refs=[]; value=None; status='proposed'
            if field=='anatomy':
                value=target['channel'].strip().lower()
                refs=[mat_parent,evidence('EMG signals were recorded unilaterally')]
            elif field=='measurement_side':
                value={'RX':'right','LX':'left'}[semantics['emg_side']]
                refs=[mat_parent,evidence('EMGSide [RX/LX]'),evidence('Indicates the trailing foot')]
            elif field=='sensor_type':
                value='surface-emg'; refs=[evidence('Surface EMG signals were recorded using')]
            elif field=='unit':
                value=record['value']; refs=[mat_parent,evidence('unit: mV')]
            elif field=='task':
                value=semantics['task']; refs=[mat_trial,evidence('Indicates the type of trial')]
            elif field=='reference_side':
                value={'RX':'right','LX':'left'}[semantics['reference_foot']]
                refs=[mat_trial,evidence('Indicates the trailing foot')]
            else:
                status='insufficient'
            labels.append(dict(**target,reference_status=status,reference_value=value,evidence=refs,
                reviewed=False,reviewer=None,
                notes='Developer prefill from existing registered profile; verify scope and interpretation against original sources.' if value is not None else
                'Provisional: full processing/normalization not established by the existing profile. Review the complete source; this is not proof of absence.'))
    result=dict(status='developer_prefill_pending_human_review',split='development',
        provenance='Derived from existing reviewed-profile outputs, NOT independent annotation and NOT LLM output.',
        input_sha256=hashlib.sha256(packet.read_bytes()).hexdigest(),profile_sha256=hashlib.sha256(profile.read_bytes()).hexdigest(),
        labels=labels)
    output.parent.mkdir(parents=True,exist_ok=True)
    with output.open('x') as stream: json.dump(result,stream,indent=2,ensure_ascii=False)
    print(json.dumps(dict(targets=len(labels),proposed=sum(l['reference_status']=='proposed' for l in labels),
        provisional_insufficient=sum(l['reference_status']=='insufficient' for l in labels),human_reviewed=0)))


if __name__=='__main__':
    parser=argparse.ArgumentParser()
    parser.add_argument('--packet',type=Path,required=True);parser.add_argument('--profile',type=Path,required=True)
    parser.add_argument('--output',type=Path,required=True)
    a=parser.parse_args();build(a.packet,a.profile,a.output)
