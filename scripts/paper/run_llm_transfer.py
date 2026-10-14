"""Run fixed transfer questions without development-specific evidence filtering."""
import argparse
import json
from pathlib import Path
from run_local_llm_pilot import run


def transfer(packet,output,model):
    output.mkdir(parents=True,exist_ok=False);rows=[]
    data=json.loads(packet.read_text())
    for ci,case in enumerate(data['cases']):
        for ti,target in enumerate(case['targets']):
            try:
                result=run(packet,output/f'{ci}-{ti}',model,11439,ci,evidence_ids=True,target_index=ti)
                entry=result['validation']['entries'][0]
                row=dict(case_index=ci,dataset=case['dataset'],field=target['field'],channel=target['channel'],
                         candidate=entry['candidate'],errors=entry['errors'],wall_seconds=result['wall_seconds'])
            except Exception as exc:
                row=dict(case_index=ci,field=target['field'],error=str(exc))
            rows.append(row)
            (output/'results.json').write_text(json.dumps(dict(model=model,accuracy_evaluated=False,
                automatic_approvals=0,rows=rows),indent=2,ensure_ascii=False))


if __name__=='__main__':
    p=argparse.ArgumentParser();p.add_argument('--model',required=True);p.add_argument('--output',type=Path,required=True)
    args=p.parse_args()
    transfer(Path('paper/results/llm_transfer_packet_v001_2026.09.14-r1/model_input.json'),args.output,args.model)
