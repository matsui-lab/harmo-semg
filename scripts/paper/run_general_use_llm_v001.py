"""Local candidate-coverage comparison, not semantic correctness scoring."""
import argparse
import json
from pathlib import Path
from run_local_llm_pilot import run


def main():
    p=argparse.ArgumentParser();p.add_argument('--packet',type=Path,required=True)
    p.add_argument('--output',type=Path,required=True);p.add_argument('--model',required=True)
    p.add_argument('--port',type=int,default=11439)
    a=p.parse_args();a.output.mkdir(parents=True,exist_ok=False)
    cases=json.loads(a.packet.read_text())['cases'];rows=[]
    for ci,case in enumerate(cases):
        for ti,target in enumerate(case['targets']):
            item=dict(case=case['case_id'],target_id=target['target_id'],field=target['field'])
            try:
                result=run(a.packet,a.output/f'{ci}-{ti}',a.model,a.port,ci,
                           evidence_ids=True,target_index=ti,max_tokens=2048)
                item.update(validation=result['validation'],wall_seconds=result['wall_seconds'],
                            done=result['done'],done_reason=result['done_reason'])
            except Exception as exc:item['error']=str(exc)
            rows.append(item)
            (a.output/'comparison.json').write_text(json.dumps(dict(model=a.model,rows=rows,
                accuracy_evaluated=False,human_time_measured=False,automatic_approvals=0,
                scope='One first-selected channel per development study; source-ID/schema checks are not entailment.'),indent=2))
            print(json.dumps(dict(target_id=target['target_id'],error=item.get('error'),completed=len(rows))),flush=True)


if __name__=='__main__':main()
