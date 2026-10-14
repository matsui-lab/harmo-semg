"""Source-bound semantic review packets and opt-in loopback LLM candidates.

Standalone CLI: python -m harmo.semantic_review prepare|propose|apply|reassess.
Candidates and workload counts are never scientific permission or accuracy.
"""
import argparse
import hashlib
import json
from pathlib import Path
import time
from urllib.request import Request, build_opener, ProxyHandler
from lxml import etree
from harmo.catalog_unit_review import NoRedirect
from harmo.llm_candidates import validate_candidates, digest
from harmo.llm_evidence_ids import PROMPT, QUESTIONS, schema, expand


def save(path, value):
    with Path(path).open('x') as stream:
        json.dump(value,stream,indent=2,ensure_ascii=False)


def prepare(summary_path, output, max_channels=4, record_ids=None):
    from harmo.automatic_workflow import collect_saved_documents
    if type(max_channels) is not int or not 1 <= max_channels <= 256:
        raise ValueError('channel_budget_required')
    summary_path=Path(summary_path).resolve();summary=json.loads(summary_path.read_text())
    intake=summary['intake'];evidence=collect_saved_documents(intake)
    if evidence['held']:raise ValueError('fresh_source_binding_required: '+str(evidence['held']))
    cases=[];bindings=[];omitted=0
    for entry in intake['entries']:
        if entry.get('repository') in ('figshare','mendeley','zenodo') and entry.get('source_report'):
            from harmo.repository_context import review_cases
            new, bound, skipped = review_cases(entry,intake['catalog'],evidence['documents'],max_channels,record_ids)
            cases.extend(new);bindings.extend(bound);omitted+=skipped
            continue
        if entry.get('repository')!='physionet' or not entry.get('source_report'):continue
        stage=Path(entry['source_report']);description=stage/'description.html'
        raw=description.read_bytes();root=etree.fromstring(raw,etree.HTMLParser(no_network=True))
        source_hash=hashlib.sha256(raw).hexdigest()
        source=[dict(source_id=f'p{i}',text=' '.join(''.join(node.itertext()).split()),
                     path=str(description),source_sha256=source_hash,pointer=root.getroottree().getpath(node),kind='html')
                for i,node in enumerate(root.xpath('//p')) if ''.join(node.itertext()).strip()]
        bindings.append(dict(path=str(description),sha256=source_hash))
        for record in entry.get('details',{}).get('records',[]):
            if record['status']!='converted':continue
            if record_ids is not None and record['id'] not in record_ids:continue
            location=Path(record['location']);meta=json.loads((location/'record.json').read_text())['metadata']
            header=stage/'raw'/record['name'];header_hash=hashlib.sha256(header.read_bytes()).hexdigest()
            if header_hash!=meta['source_sha256'][record['name']]:raise ValueError('header_changed')
            bindings.append(dict(path=str(header),sha256=header_hash))
            sources=source+[dict(source_id='header',text=header.read_text(),path=str(header),
                                source_sha256=header_hash,pointer='whole-file',kind='text')]
            facts=[o for doc in evidence['documents'] if doc['dataset']==entry['dataset']
                   for o in doc['result']['observations'] if o['record']==record['name']]
            targets=[];signals=meta['signals'];omitted+=max(0,len(signals)-max_channels)*4
            for index,channel in enumerate(signals[:max_channels]):
                for field in ('sensor_type','anatomy','measurement_side','unit'):
                    targets.append(dict(target_id=f"{record['id']}:ch-{index}:{field}",record_id=record['id'],
                                        record=record['name'],channel=channel['label'],channel_id=f'ch-{index}',field=field))
            for field in ('task','complete_processing_protocol','amplitude_normalization'):
                targets.append(dict(target_id=f"{record['id']}:{field}",record_id=record['id'],record=record['name'],
                                    channel='',channel_id=None,field=field))
            rules=[]
            for target in targets:
                field='side' if target['field']=='measurement_side' else target['field']
                values=sorted({o['value'] for o in facts if o['channel']==target['channel'] and o['field']==field})
                if field=='unit':values=[signals[int(target['channel_id'].split('-')[1])]['unit']]
                rules.append(dict(target_id=target['target_id'],values=values,
                                  status='proposed' if len(values)==1 else ('ambiguous' if values else 'insufficient')))
            cases.append(dict(case_id=record['id'],dataset=entry['dataset'],record=record['name'],
                              sources=sources,targets=targets,rules=rules))
    packet=dict(version='semantic-review/1',summary_path=str(summary_path),summary_sha256=hashlib.sha256(summary_path.read_bytes()).hexdigest(),
                bindings=bindings,cases=cases,omitted_channel_fields=omitted,automatic_approvals=0)
    packet['packet_id']=digest(packet)
    out=Path(output);out.mkdir(parents=True,exist_ok=False);save(out/'packet.json',packet)
    # All items start unreviewed. No name, time or approval is fabricated.
    edits=dict(packet_id=packet['packet_id'],reviewer='',active_seconds=None,
               decisions=[dict(target_id=t['target_id'],action='unreviewed',value=None,evidence_ids=[],scope_reason='')
                          for c in cases for t in c['targets']])
    save(out/'decisions.json',edits)
    return dict(output=str(out),records=len(cases),review_items=len(edits['decisions']),omitted_channel_fields=omitted,
                human_time_measured=False,scope='Metadata evidence review; processing and scientific qualification remain separate.')


def checked_packet(path):
    packet=json.loads(Path(path).read_text());identity=packet.pop('packet_id')
    if digest(packet)!=identity:raise ValueError('packet_changed')
    packet['packet_id']=identity
    for source in [*packet['bindings'],dict(path=packet['summary_path'],sha256=packet['summary_sha256'])]:
        if hashlib.sha256(Path(source['path']).read_bytes()).hexdigest()!=source['sha256']:
            raise ValueError('bound_source_changed')
    return packet


def propose(packet_path, output, model, port=11439, max_targets=32, transport=None,
            target_ids=None, source_ids_by_target=None):
    if not isinstance(model,str) or not model or 'cloud' in model.lower() or not 1024<=port<=65535 or not 1<=max_targets<=256:
        raise ValueError('bounded_local_model_required')
    packet=checked_packet(packet_path)
    targets={t['target_id']:c for c in packet['cases'] for t in c['targets']}
    if target_ids is not None and (not isinstance(target_ids,list) or len(set(target_ids))!=len(target_ids)
                                   or not set(target_ids)<=set(targets)):
        raise ValueError('unknown_or_duplicate_selected_target')
    selected=set(target_ids) if target_ids is not None else set(targets)
    for tid,ids in (source_ids_by_target or {}).items():
        if tid not in selected or not isinstance(ids,list) or not ids or len(set(ids))!=len(ids):
            raise ValueError('invalid_selected_sources')
        if not set(ids)<={s['source_id'] for s in targets[tid]['sources']}:
            raise ValueError('unknown_selected_source')
    out=Path(output);out.mkdir(parents=True,exist_ok=False)
    opener=build_opener(ProxyHandler({}),NoRedirect())
    def api(path,payload=None):
        with opener.open(Request(f'http://127.0.0.1:{port}/api/{path}',
            data=None if payload is None else json.dumps(payload).encode(),headers={'Content-Type':'application/json'}),timeout=180) as response:
            raw=response.read(8*1024**2+1)
        if len(raw)>8*1024**2:raise ValueError('response_budget')
        return json.loads(raw)
    if transport is not None:api=transport
    details=api('show',dict(model=model))
    if details.get('remote_model') or details.get('remote_host'):raise ValueError('remote_model_not_allowed')
    tags=api('tags');models=[m for m in tags['models'] if m['name']==model]
    if len(models)!=1:raise ValueError('unique_installed_model_required')
    save(out/'manifest.json',dict(model=models[0],details=details,runtime=api('version'),packet_id=packet['packet_id']))
    rows=[];total=len(selected)
    for case in packet['cases']:
        for target in case['targets']:
            if target['target_id'] not in selected:continue
            if len(rows)>=max_targets:break
            started=time.monotonic();item=dict(target_id=target['target_id'],status='failed')
            folder=out/f'query-{len(rows):04d}';folder.mkdir()
            try:
                query=dict(target,question=QUESTIONS[target['field']])
                ids=(source_ids_by_target or {}).get(target['target_id'])
                sources=[s for s in case['sources'] if ids is None or s['source_id'] in ids]
                item['source_ids']=[s['source_id'] for s in sources]
                content=json.dumps(dict(record=case['record'],targets=[query],sources=sources))
                if len(content)>100000:raise ValueError('context_character_budget')
                request=dict(model=model,stream=False,think=False,format=schema(sources,query),
                             keep_alive='5m',options=dict(temperature=0,seed=0,num_ctx=32768,num_predict=2048),
                             messages=[dict(role='system',content=PROMPT),dict(role='user',content=content)])
                save(folder/'request.json',request);response=api('chat',request);save(folder/'response.json',response)
                if not response.get('done') or response.get('done_reason')=='length':raise ValueError('incomplete_generation')
                decoded=expand(json.loads(response['message']['content']),query,sources)
                validation=validate_candidates(decoded,[query],sources)
                item.update(status='requires_semantic_review',validation=validation)
            except Exception as exc:item['error']=str(exc)
            item['wall_seconds']=time.monotonic()-started;rows.append(item)
    checked_packet(packet_path)
    report=dict(packet_id=packet['packet_id'],rows=rows,unprocessed_targets=total-len(rows),
                automatic_approvals=0,accuracy_evaluated=False,human_time_measured=False)
    save(out/'candidates.json',report);return report


def reviewed_documents(packet_path, decisions_path):
    packet=checked_packet(packet_path);edits=json.loads(Path(decisions_path).read_text())
    if edits.get('packet_id')!=packet['packet_id'] or not isinstance(edits.get('reviewer'),str):
        raise ValueError('packet_and_review_actor_required')
    reviewer = edits['reviewer'].strip()
    anonymous = not reviewer
    if anonymous:
        from uuid import UUID
        actor = edits.get('review_actor')
        if not isinstance(actor, dict) or actor.get('kind') != 'anonymous-local-session':
            raise ValueError('anonymous_review_actor_required')
        try:
            identity = UUID(actor['id'])
            if identity.version != 4 or str(identity) != actor['id']:
                raise ValueError('invalid_anonymous_review_actor')
        except (ValueError, TypeError, KeyError, AttributeError):
            raise ValueError('invalid_anonymous_review_actor') from None
        reviewer = 'anonymous-session:'+str(identity)
    targets={t['target_id']:(case,t) for case in packet['cases'] for t in case['targets']}
    seen=set();documents=[];reviewed=0
    for decision in edits['decisions']:
        identity=decision['target_id']
        if identity not in targets or identity in seen:raise ValueError('unknown_or_duplicate_review_target')
        seen.add(identity);action=decision['action']
        if action not in ('unreviewed','confirm','hold','reject'):raise ValueError('invalid_review_action')
        if action=='unreviewed':continue
        if not str(decision.get('scope_reason','')).strip():raise ValueError('explicit_scope_reason_required')
        reviewed+=1
        case,target=targets[identity];field=target['field'];value=decision['value']
        if action in ('hold','reject'):
            documents.append(dict(dataset=case['dataset'],result=dict(observations=[dict(record=case['record'],
                channel=target['channel'],field='side' if field=='measurement_side' else field,value='explicit-human-hold',
                status='review_hold',reviewer=reviewer,scope_reason=decision['scope_reason'])])))
            continue
        if field not in ('sensor_type','anatomy','measurement_side','unit','task'):
            raise ValueError('processing_requires_validated_execution_not_a_metadata_confirmation')
        if not isinstance(value,str) or not value.strip():raise ValueError('literal_review_value_required')
        ids=decision['evidence_ids'];sources={s['source_id']:s for s in case['sources']}
        if not isinstance(ids,list) or not ids or any(s not in sources for s in ids):raise ValueError('explicit_evidence_required')
        if field in ('sensor_type','anatomy','task') and all(s=='header' for s in ids):
            raise ValueError('prose_scope_evidence_required')
        observations=[]
        for sid in ids:
            source=sources[sid]
            observations.append(dict(record=case['record'],channel=target['channel'],
                field='side' if field=='measurement_side' else field,value=value,status='source_observation',
                source_uri=Path(source['path']).as_uri(),source_sha256=source['source_sha256'],pointer=source['pointer'],quote=source['text'],
                scope_evidence=dict(record=dict(value=case['record'],pointer=case.get('record_pointer','header:line1')),
                                    channel=dict(value=target['channel'],pointer='exact-target')),
                scope_rule='anonymous-human-semantic-review/1' if anonymous else 'named-human-semantic-review/1',
                reviewer=reviewer,scope_reason=decision['scope_reason']))
        documents.append(dict(dataset=case['dataset'],result=dict(observations=observations)))
    seconds=edits.get('active_seconds')
    if seconds is not None and (type(seconds) not in (int,float) or not 0<=seconds<86400):raise ValueError('invalid_self_reported_active_time')
    metrics=dict(reviewed_items=reviewed,unreviewed_items=len(targets)-reviewed,
                 reviewer_identity='anonymous-session' if anonymous else 'supplied-name',
                 self_reported_active_seconds=seconds,independently_timed=False,scientific_approvals=0)
    return documents,metrics


def apply(packet_path, decisions_path, output):
    _,metrics=reviewed_documents(packet_path,decisions_path)
    out=Path(output);out.mkdir(parents=True,exist_ok=False)
    receipt=dict(version='semantic-review-receipt/1',packet_path=str(Path(packet_path).resolve()),
                 packet_sha256=hashlib.sha256(Path(packet_path).read_bytes()).hexdigest(),
                 decisions_path=str(Path(decisions_path).resolve()),
                 decisions_sha256=hashlib.sha256(Path(decisions_path).read_bytes()).hexdigest(),metrics=metrics)
    save(out/'review.receipt.json',receipt)
    return receipt


def reassess(summary_path, output, max_comparisons=1000, review=None, *, offline=False, record_ids=None):
    from harmo.automatic_workflow import collect_saved_documents,assess_catalog
    acquired=json.loads(Path(summary_path).read_text())['intake']
    documents=collect_saved_documents(acquired, allow_retrieve=False) if offline else collect_saved_documents(acquired)
    if documents['held']:raise ValueError('fresh_source_binding_required')
    if review:
        receipt=json.loads(Path(review).read_text())
        for key in ('packet','decisions'):
            if hashlib.sha256(Path(receipt[key+'_path']).read_bytes()).hexdigest()!=receipt[key+'_sha256']:
                raise ValueError('review_receipt_changed')
        packet=checked_packet(receipt['packet_path'])
        if Path(packet['summary_path']).resolve()!=Path(summary_path).resolve():raise ValueError('review_dataset_scope_mismatch')
        additions,_=reviewed_documents(receipt['packet_path'],receipt['decisions_path'])
        # Preserve disagreements with source-derived facts; never overwrite them.
        documents['documents'].extend(additions)
    return assess_catalog(acquired['catalog'],output,['activation-timing','amplitude-pooling'],max_comparisons,
                          datasets={e['dataset'] for e in acquired['entries'] if 'dataset' in e},documents=documents['documents'],
                          record_ids=record_ids)


def main():
    p=argparse.ArgumentParser(description=__doc__);sub=p.add_subparsers(dest='action',required=True)
    prepare_p=sub.add_parser('prepare');prepare_p.add_argument('--summary',required=True);prepare_p.add_argument('--output',required=True)
    prepare_p.add_argument('--max-channels',type=int,default=4)
    propose_p=sub.add_parser('propose');propose_p.add_argument('--packet',required=True);propose_p.add_argument('--output',required=True)
    propose_p.add_argument('--model',required=True);propose_p.add_argument('--port',type=int,default=11439);propose_p.add_argument('--max-targets',type=int,default=32)
    apply_p=sub.add_parser('apply');apply_p.add_argument('--packet',required=True);apply_p.add_argument('--decisions',required=True);apply_p.add_argument('--output',required=True)
    reassess_p=sub.add_parser('reassess');reassess_p.add_argument('--summary',required=True);reassess_p.add_argument('--output',required=True)
    reassess_p.add_argument('--max-comparisons',type=int,default=1000)
    reassess_p.add_argument('--review')
    a=p.parse_args()
    if a.action=='prepare':r=prepare(a.summary,a.output,a.max_channels)
    elif a.action=='propose':r=propose(a.packet,a.output,a.model,a.port,a.max_targets)
    elif a.action=='apply':r=apply(a.packet,a.decisions,a.output)
    else:r=reassess(a.summary,a.output,a.max_comparisons,a.review)
    print(json.dumps(r,indent=2))


if __name__=='__main__':main()
