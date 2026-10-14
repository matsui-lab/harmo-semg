"""Source-isolated extraction and deterministic reconciliation on real inputs."""
import json
import os
from pathlib import Path
import subprocess
import time
from urllib.request import build_opener,ProxyHandler
from run_local_llm_pilot import run
from harmo.llm_evidence_ids import QUESTIONS as RUN_QUESTIONS
from harmo.unit_reconciliation import QUESTIONS,reconcile_units


def main():
    out=Path('paper/results/sourcewise_units_v001_2026.09.14-r1');out.mkdir(parents=True,exist_ok=False)
    original=json.loads(Path('paper/results/llm_transfer_packet_v001_2026.09.14-r1/model_input.json').read_text())
    cases=[];specs=[]
    for ci in (0,1):
        case=original['cases'][ci];target=case['targets'][3]
        for source,field in [('description','physical_unit'),('header','physical_unit'),
                             ('header','digital_to_physical_calibration'),('description','physiological_amplitude_normalization')]:
            selected=[s for s in case['sources'] if (s['source_id']=='wfdb-header')==(source=='header')]
            newtarget=dict(target,field=field)
            cases.append(dict(case,sources=selected,targets=[newtarget]))
            specs.append(dict(case_index=ci,source_id=source,field=field,target_id=target['target_id']))
    packet=out/'model_input.json';packet.write_text(json.dumps(dict(prompt=original['prompt'],cases=cases),indent=2,ensure_ascii=False))
    RUN_QUESTIONS.update(QUESTIONS)
    env=os.environ.copy();env.update(OLLAMA_HOST='127.0.0.1:11469',OLLAMA_MODELS='/private/tmp/harmo-gemma4-runtime.AD9diU/models',OLLAMA_NO_CLOUD='1')
    state=dict(status='starting',automatic_approvals=0,accuracy_evaluated=False,rows=[])
    def save():(out/'report.json').write_text(json.dumps(state,indent=2,ensure_ascii=False))
    save();server=None
    try:
        with (out/'server.log').open('w') as log:
            server=subprocess.Popen(['/private/tmp/harmo-gemma4-runtime.AD9diU/ollama','serve'],env=env,stdout=log,stderr=subprocess.STDOUT)
        opener=build_opener(ProxyHandler({}))
        for _ in range(30):
            if server.poll() is not None:raise RuntimeError('server_exited')
            try:
                with opener.open('http://127.0.0.1:11469/api/version',timeout=1) as response:state['runtime']=json.load(response)
                break
            except OSError:time.sleep(0.5)
        else:raise RuntimeError('server_not_ready')
        state['status']='running';save()
        for i,spec in enumerate(specs):
            try:
                summary=run(packet,out/str(i),'gemma4:12b',11469,i,evidence_ids=True,max_tokens=1024)
                entry=summary['validation']['entries'][0]
                state['rows'].append(dict(spec,**{k:entry['candidate'][k] for k in ('status','value','evidence')},
                    structurally_valid=summary['validation']['structurally_complete'],errors=entry['errors']))
            except Exception as exc:state['rows'].append(dict(spec,status='error',value=None,structurally_valid=False,error=str(exc)))
            save()
        state['reconciliation']=[]
        for ci in (0,1):
            claims=[r for r in state['rows'] if r['case_index']==ci and r['field']=='physical_unit']
            state['reconciliation'].append(dict(case_index=ci,**reconcile_units(claims,claims[0]['target_id'],['description','header'])))
        state['status']='completed';save()
    except Exception as exc:state.update(status='failed',error=str(exc));save();raise
    finally:
        if server is not None:
            server.terminate()
            try:server.wait(timeout=10)
            except subprocess.TimeoutExpired:server.kill();server.wait()
            state['server_returncode']=server.returncode;save()


if __name__=='__main__':main()
