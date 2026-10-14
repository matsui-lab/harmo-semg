"""Finite local comparison queue: acquire, test serially, unload each model."""
import argparse
import json
from pathlib import Path
import time
from urllib.request import Request, build_opener, ProxyHandler
from run_local_llm_comparison import compare


def suite(packet, output):
    output.mkdir(parents=True, exist_ok=False)
    opener=build_opener(ProxyHandler({}))
    def api(path, payload):
        request=Request('http://127.0.0.1:11439/api/'+path,
                        data=json.dumps(payload).encode(),headers={'Content-Type':'application/json'})
        with opener.open(request,timeout=7200) as response:
            return json.load(response)
    state=dict(scope='Development only; no human-reference accuracy estimate.',
               automatic_approvals=0,models=[])
    def save():
        (output/'status.json').write_text(json.dumps(state,indent=2))
    for model in ('qwen3.5:9b','gemma4:12b','qwen3.5:27b'):
        item=dict(model=model,status='downloading',started_at=time.time())
        state['models'].append(item); save()
        try:
            item['pull']=api('pull',dict(model=model,stream=False))
            if item['pull'].get('status')!='success':
                raise RuntimeError('model_download_did_not_succeed')
            item['status']='testing'; save()
            folder=output/model.replace(':','_')
            rows=compare(packet,folder,model)
            item['requests_completed']=sum('error' not in row for row in rows)
            item['status']='completed' if item['requests_completed']==8 else 'completed_with_failures'
        except Exception as exc:
            item.update(status='failed',error=str(exc))
        finally:
            try:
                item['unload']=api('generate',dict(model=model,keep_alive=0,stream=False))
            except Exception as exc:
                item['unload_error']=str(exc)
            item['finished_at']=time.time();save()
    state['finished']=True;save()


if __name__=='__main__':
    parser=argparse.ArgumentParser()
    parser.add_argument('--packet',type=Path,required=True)
    parser.add_argument('--output',type=Path,required=True)
    args=parser.parse_args()
    suite(args.packet,args.output)
