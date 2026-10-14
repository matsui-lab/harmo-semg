"""One development case, loopback-only Ollama, no reference labels in input."""
import argparse
import hashlib
import json
from pathlib import Path
import time
from urllib.request import Request,build_opener,HTTPRedirectHandler,ProxyHandler
from harmo.llm_candidates import validate_candidates


class NoRedirect(HTTPRedirectHandler):
    def redirect_request(self,*args,**kwargs): raise ValueError('redirect_not_allowed')


def run(packet,output,model,port,case_index,transport=None,small=False,focused=False,evidence_ids=False,target_index=0,thinking=False,max_tokens=8192):
    if not 1024<=port<=65535: raise ValueError('invalid_port')
    if not 1<=max_tokens<=8192: raise ValueError('invalid_token_budget')
    opener=build_opener(ProxyHandler({}),NoRedirect())
    def api(path,value=None):
        payload=None if value is None else json.dumps(value).encode()
        with opener.open(Request(f'http://127.0.0.1:{port}/api/'+path,data=payload,
            headers={'Content-Type':'application/json'}),timeout=900) as response:
            raw=response.read(8*1024**2+1)
        if len(raw)>8*1024**2: raise ValueError('response_size_budget')
        return json.loads(raw)
    if transport is not None: api=transport
    data=json.loads(packet.read_text()); case=data['cases'][case_index]
    if small:
        first=next(t['channel'] for t in case['targets'] if t['channel'])
        case=dict(case,targets=[t for t in case['targets'] if t['channel'] in ('',first)])
    if focused:
        # Development-case retrieval ablation. Text is retained verbatim; no answers added.
        terms=('EMGSide','EMG signals were recorded','Surface EMG signals were recorded',
            'unit: mV','Indicates the type of trial','Indicates the trailing foot')
        case=dict(case,sources=[s for s in case['sources'] if s['source_id'].startswith('mat-') or any(t in s['text'] for t in terms)])
    if evidence_ids:
        from harmo.llm_evidence_ids import QUESTIONS
        target=dict(case['targets'][target_index])
        target['question']=QUESTIONS[target['field']]
        if target['field']=='reference_side': target['canonical_choices']=['left','right']
        case=dict(case,targets=[target])
    models=api('tags')['models']
    matched=[m for m in models if m['name']==model]
    if len(matched)!=1: raise ValueError('unique_local_model_required')
    output.mkdir(parents=True,exist_ok=False)
    def save(name,value): (output/name).write_text(json.dumps(value,indent=2,ensure_ascii=False))
    request=dict(model=model,stream=False,think=thinking,format='json',keep_alive='5m',
        options=dict(temperature=0,seed=0,num_ctx=32768,num_predict=max_tokens),
        messages=[dict(role='system',content=data['prompt']),dict(role='user',content=json.dumps(case,ensure_ascii=False))])
    if small:
        request['format']={'type':'object','additionalProperties':False,'required':['candidates'],'properties':{
            'candidates':{'type':'array','minItems':len(case['targets']),'maxItems':len(case['targets']),
                'items':{'type':'object','additionalProperties':False,
                    'required':['target_id','status','value','alternatives','evidence'],
                    'properties':{'target_id':{'type':'string','enum':[t['target_id'] for t in case['targets']]},
                        'status':{'type':'string','enum':['proposed','ambiguous','insufficient']},
                        'value':{'type':['string','null']},'alternatives':{'type':'array','items':{'type':'string'}},
                        'evidence':{'type':'array','items':{'type':'object','additionalProperties':False,
                            'required':['source_id','quote'],'properties':{'source_id':{'type':'string','enum':[s['source_id'] for s in case['sources']]},'quote':{'type':'string'}}}}}}}}}
    if evidence_ids:
        from harmo.llm_evidence_ids import PROMPT,schema
        request['format']=schema(case['sources'],case['targets'][0])
        request['messages'][0]['content']=PROMPT
    manifest=dict(model=matched[0],runtime=api('version'),model_details=api('show',dict(model=model)),
        packet_sha256=hashlib.sha256(packet.read_bytes()).hexdigest(),case_id=case['case_id'],
        reference_accessed=False,evidence_id_selection=evidence_ids,small_schema_constrained=small,focused_development_retrieval=focused,scope='Development smoke test, not human-reference accuracy evaluation.',
        peak_system_memory_bytes=None,input_truncation_verified=False)
    save('manifest.json',manifest);save('request.json',request)
    start=time.monotonic()
    try:
        response=api('chat',request);save('response.json',response)
        output_text=response.get('message',{}).get('content','')
        if response.get('done_reason')=='length' and not output_text.strip():
            raise ValueError('generation_budget_exhausted_without_final_answer')
        decoded=json.loads(output_text)
        if evidence_ids:
            from harmo.llm_evidence_ids import expand
            decoded=expand(decoded,case['targets'][0],case['sources'])
            save('resolved_evidence.json',decoded)
        validation=validate_candidates(decoded,case['targets'],case['sources'])
        summary=dict(wall_seconds=time.monotonic()-start,validation=validation,
            done=response.get('done'),done_reason=response.get('done_reason'),
            counts={k:response.get(k) for k in ('prompt_eval_count','eval_count','total_duration','load_duration','eval_duration')},
            model_digest_unchanged=any(m['name']==model and m['digest']==matched[0]['digest'] for m in api('tags')['models']),
            model_memory_snapshot=api('ps'),accuracy_evaluated=False)
        save('summary.json',summary)
        print(json.dumps(dict(wall_seconds=summary['wall_seconds'],complete=validation['structurally_complete'],
            targets=len(case['targets']),rejected=sum(bool(e['errors']) for e in validation['entries']),
            done_reason=summary['done_reason'],counts=summary['counts']),indent=2))
        return summary
    except Exception as exc:
        save('failure.json',dict(error=str(exc),wall_seconds=time.monotonic()-start))
        raise


if __name__=='__main__':
    parser=argparse.ArgumentParser();parser.add_argument('--packet',type=Path,required=True)
    parser.add_argument('--output',type=Path,required=True);parser.add_argument('--model',default='gemma4:e2b')
    parser.add_argument('--port',type=int,default=11439);parser.add_argument('--case-index',type=int,default=0)
    parser.add_argument('--small',action='store_true')
    parser.add_argument('--focused',action='store_true')
    parser.add_argument('--evidence-ids',action='store_true');parser.add_argument('--target-index',type=int,default=0)
    parser.add_argument('--thinking',action='store_true')
    parser.add_argument('--max-tokens',type=int,default=8192)
    a=parser.parse_args();run(a.packet,a.output,a.model,a.port,a.case_index,small=a.small,focused=a.focused,evidence_ids=a.evidence_ids,target_index=a.target_index,thinking=a.thinking,max_tokens=a.max_tokens)
