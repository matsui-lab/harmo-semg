"""Frozen eight-question development comparison; no reference-based scoring."""
import argparse
import json
from pathlib import Path
from run_local_llm_pilot import run


def compare(packet, output, model, port=11439, thinking=False):
    output.mkdir(parents=True, exist_ok=False)
    rows=[]
    for index in range(8):
        folder=output/str(index)
        try:
            result=run(packet,folder,model,port,0,small=True,focused=True,
                       evidence_ids=True,target_index=index,thinking=thinking)
            request=json.loads((folder/'request.json').read_text())
            target=json.loads(request['messages'][1]['content'])['targets'][0]
            entry=result['validation']['entries'][0]
            rows.append(dict(index=index,field=target['field'],
                candidate=entry['candidate'],errors=entry['errors'],
                structurally_complete=result['validation']['structurally_complete'],
                wall_seconds=result['wall_seconds'],counts=result['counts'],
                model_memory_snapshot=result['model_memory_snapshot']))
        except Exception as exc:
            rows.append(dict(index=index,error=str(exc)))
        (output/'comparison.json').write_text(json.dumps(dict(model=model,thinking=thinking,
            scope='Single development recording; selected excerpts; no independent accuracy evaluation.',
            automatic_approvals=0,accuracy_evaluated=False,rows=rows),indent=2,ensure_ascii=False))
    return rows


if __name__=='__main__':
    parser=argparse.ArgumentParser()
    parser.add_argument('--packet',type=Path,required=True)
    parser.add_argument('--output',type=Path,required=True)
    parser.add_argument('--model',required=True)
    parser.add_argument('--port',type=int,default=11439)
    parser.add_argument('--thinking',action='store_true')
    args=parser.parse_args()
    compare(args.packet,args.output,args.model,args.port,args.thinking)
