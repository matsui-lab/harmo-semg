"""Opt-in local candidate review; never mutate sample arrays or trust reports."""
import hashlib
import json
from pathlib import Path
import sqlite3
from urllib.request import Request,build_opener,ProxyHandler,HTTPRedirectHandler
from lxml import html
from harmo.public_export import load_record
from harmo.llm_evidence_ids import PROMPT,schema,expand
from harmo.llm_candidates import validate_candidates
from harmo.unit_reconciliation import QUESTIONS,verify_wfdb_unit,reconcile_units


class NoRedirect(HTTPRedirectHandler):
    def redirect_request(self,*args,**kwargs):raise ValueError('redirect_not_allowed')


def review_catalog(catalog,output,datasets,model,port=11439,max_channels=32,transport=None):
    if not isinstance(model,str) or not model or 'cloud' in model.lower() or not 1024<=port<=65535 or max_channels<1:raise ValueError('invalid_local_review_options')
    output=Path(output);output.mkdir(parents=True,exist_ok=False)
    opener=build_opener(ProxyHandler({}),NoRedirect())
    def api(path,payload=None):
        request=Request(f'http://127.0.0.1:{port}/api/{path}',data=None if payload is None else json.dumps(payload).encode(),headers={'Content-Type':'application/json'})
        with opener.open(request,timeout=180) as response:
            raw=response.read(8*1024**2+1)
        if len(raw)>8*1024**2:raise ValueError('response_budget')
        return json.loads(raw)
    if transport is not None:api=transport
    with sqlite3.connect(Path(catalog).resolve().as_uri()+'?mode=ro',uri=True) as db:
        rows=db.execute('SELECT id,dataset,name,location,metadata FROM records ORDER BY id').fetchall()
    report=dict(version='catalog-unit-review/1',status='running',model=model,records={},automatic_approvals=0)
    def save():
        path=output/'review.queue.tmp';path.write_text(json.dumps(report,indent=2));path.replace(output/'review.queue.json')
    save()
    used=0
    try:
        report['runtime']=api('version');report['model_manifest']=api('show',dict(model=model))
        if report['model_manifest'].get('remote_model') or report['model_manifest'].get('remote_host'):
            raise ValueError('remote_model_not_allowed')
    except Exception as exc:report['runtime_error']=str(exc)
    for row in rows:
        if row[1] not in datasets:continue
        item=dict(record_id=row[0],channels=[],record_hold=False)
        report['records'][row[0]]=item
        try:
            bundle,_,directory=load_record(row[:4]);item['sample_sha256']=bundle.samples.checksum
            stage=directory.parent.parent;meta=json.loads(row[4]);name=Path(row[2])
            if name.name!=row[2] or name.suffix!='.hea':raise ValueError('WFDB_header_binding_required')
            header=stage/'raw'/name
            digest=meta.get('source_sha256',{}).get(name.name)
            if not digest:raise ValueError('source_header_hash_required')
            document=stage/'description.html'
            if document.stat().st_size>2*1024**2:raise ValueError('document_size_budget')
            raw=document.read_bytes();docsha=hashlib.sha256(raw).hexdigest()
            sources=[dict(source_id=f'doc-{i}',text=' '.join(p.text_content().split()),source_sha256=docsha,
                uri=document.as_uri(),pointer=p.getroottree().getpath(p)) for i,p in enumerate(html.fromstring(raw).xpath('//main//p')) if p.text_content().strip()]
            if not sources:raise ValueError('document_paragraphs_required')
            for ci,ch in enumerate(bundle.channels):
                if 'emg' not in ch.name.lower() and ch.sensor_type not in ('surface-emg','needle-emg'):continue
                result=dict(channel_id=ch.channel_id,channel_name=ch.name,hold=True)
                item['channels'].append(result)
                try:
                    if used>=max_channels:raise ValueError('channel_review_budget')
                    used+=1
                    target=dict(target_id=row[0]+':'+ch.channel_id,channel=ch.name,field='physical_unit',question=QUESTIONS['physical_unit'])
                    proof=verify_wfdb_unit(header,digest,ci,ch.name,target['target_id'])
                    request=dict(model=model,stream=False,think=False,keep_alive='5m',format=schema(sources,target),
                        options=dict(temperature=0,seed=0,num_ctx=16384,num_predict=1024),
                        messages=[dict(role='system',content=PROMPT),dict(role='user',content=json.dumps(dict(sources=sources,targets=[target])))])
                    if len(request['messages'][1]['content'])>24000:raise ValueError('document_context_budget')
                    folder=output/f'query-{used:04d}';folder.mkdir()
                    (folder/'request.json').write_text(json.dumps(request,indent=2))
                    if 'runtime_error' in report:raise ValueError('local_model_unavailable')
                    response=api('chat',request);(folder/'response.json').write_text(json.dumps(response,indent=2))
                    if response.get('done_reason')=='length':raise ValueError('generation_budget_exhausted')
                    decoded=expand(json.loads(response['message']['content']),target,sources)
                    validated=validate_candidates(decoded,[target],sources)
                    candidate=validated['entries'][0]['candidate']
                    claims=[dict(target_id=target['target_id'],field='physical_unit',source_id='description',
                        **{k:candidate[k] for k in ('status','value','evidence')},structurally_valid=validated['structurally_complete']),
                        dict(target_id=target['target_id'],field='physical_unit',source_id='header',status='proposed',value=proof['unit'],
                             structurally_valid=True,evidence=[dict(source_id='header',quote=proof['evidence_line'])])]
                    decision=reconcile_units(claims,target['target_id'],['description','header'],file_unit_evidence=proof)
                    result.update(decision=decision,hold=decision['hold'],claims=claims,header_evidence=proof,description_sha256=docsha)
                except Exception as exc:result['error']=str(exc)
                save()
        except Exception as exc:item.update(record_hold=True,error=str(exc))
        save()
    report['reviewed_channels']=used
    report['status']='completed';save()
    return report


def amplitude_holds(review,record_id,channel_id,sample_sha256):
    item=(review or {}).get('records',{}).get(record_id)
    if item is None:return ['unit_review_missing'] if review is not None else []
    if item.get('record_hold') or item.get('sample_sha256')!=sample_sha256:return ['unit_review_unresolved_or_stale']
    matches=[c for c in item['channels'] if c['channel_id']==channel_id]
    if len(matches)!=1 or matches[0]['hold']:return ['unit_review_requires_resolution']
    return []
