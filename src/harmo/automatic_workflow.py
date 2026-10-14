"""Bounded URL-to-purpose orchestration; never infer scientific approval."""
import json
from pathlib import Path
import sqlite3
from itertools import combinations, islice
import hashlib

from harmo.public_export import load_record, export_catalog
from harmo.purpose_requirements import summarize

PURPOSES = ('activation-timing', 'amplitude-pooling')


def emg(channel):
    return 'emg' in channel.name.lower() or channel.sensor_type in ('surface-emg','needle-emg')


def assess_catalog(catalog, output, purposes, max_comparisons=1000, datasets=None, documents=None, unit_review=None, record_ids=None):
    if (not isinstance(purposes,list) or not purposes or
            any(p not in PURPOSES for p in purposes) or len(set(purposes)) != len(purposes)):
        raise ValueError('unique_supported_purposes_required')
    if type(max_comparisons) is not int or max_comparisons < 1:
        raise ValueError('positive_comparison_budget_required')
    output = Path(output); output.mkdir(parents=True,exist_ok=False)
    with sqlite3.connect(Path(catalog).resolve().as_uri()+'?mode=ro',uri=True) as db:
        rows = db.execute('SELECT id,dataset,name,location FROM records ORDER BY id').fetchall()
    if datasets is not None:
        rows = [r for r in rows if r[1] in datasets]
    if record_ids is not None:
        rows = [r for r in rows if r[0] in record_ids]
    records, loaded = [], []
    documentary_checks = {}
    documentary_inputs = {}
    for row in rows:
        item = dict(record_id=row[0],dataset=row[1],name=row[2],status='held',reasons=[],comparisons=0)
        records.append(item)
        try:
            bundle,_,directory = load_record(row)
            from harmo.document_evidence import check_record
            observations=[o for d in (documents or []) if d['dataset']==row[1] for o in d['result']['observations']]
            documentary_inputs[row[0]]=observations
            item['documentary_observations'] = check_record(observations,bundle,row[2])
            documentary_checks[row[0]] = item['documentary_observations']
            from harmo.document_contract import resolve
            resolved,resolution=resolve(bundle,row[2],observations)
            if resolution['changed']:
                from harmo.public_export import checked_samples
                checked_samples(bundle,directory)
                bundle=resolved
                target=output/('resolved-%06d.json'%len(records))
                target.write_text(json.dumps(bundle.to_dict(),indent=2))
                resolution['output_path']=str(target)
            item['attribute_resolution']=resolution
            channels = [c for c in bundle.channels if emg(c)]
            if not channels:
                item['reasons'].append('no_recognized_EMG_channel')
            else:
                loaded.append((row,channels,item))
        except Exception as exc:
            item['reasons'].append('invalid_record_contract: '+str(exc))
    jobs, outstanding = [], []
    used, exported = 0, 0
    # Deterministic unordered recording pairs, all recognized EMG channels.
    # This is candidate enumeration, not anatomy equivalence or sample pairing.
    total_pairs = len(loaded)*(len(loaded)-1)//2
    if total_pairs > max_comparisons:
        outstanding.append(dict(reason='candidate_enumeration_budget',count=total_pairs-max_comparisons))
    for (left,channels,left_item),(right,rchannels,right_item) in islice(combinations(loaded,2),max_comparisons):
            cost = len(channels)*len(rchannels)*len(purposes)
            if used + cost > max_comparisons:
                outstanding.append(dict(left=left[0],right=right[0],reason='comparison_budget',required=cost))
                continue
            used += cost
            for channel in channels:
                request = dict(reference=dict(dataset=left[1],name=left[2],channel=channel.name),purposes=purposes)
                job = dict(left=left[0],right=right[0],channel=channel.channel_id,status='held')
                jobs.append(job)
                try:
                    result = export_catalog(catalog,request,output/('pair-%06d'%len(jobs)),candidate_ids={right[0]},
                        documentary_checks=documentary_checks,documentary_inputs=documentary_inputs,unit_review=unit_review)
                    job['result'] = result
                    job['requirements'] = [summarize(e['decision']) for e in result['entries'] if 'decision' in e]
                    exported += result['exported']
                    for item in (left_item,right_item):
                        item['comparisons'] += len(result['entries'])
                        if result['exported']: item['status'] = 'has_authorized_pair_output'
                    job['status'] = 'has_authorized_output' if result['exported'] else 'held'
                except Exception as exc:
                    job['error'] = str(exc)
    for item in records:
        if not item['comparisons'] and not item['reasons']:
            item['reasons'].append('no_evaluated_peer; inspect_budget_and_pair_errors')
        elif item['status'] == 'held' and not item['reasons']:
            item['reasons'].append('no_authorized_pair_output; inspect_pair_requirements')
    report = dict(version='automatic-workflow/0.1',purposes=purposes,records=records,jobs=jobs,
        unprocessed_pairs=outstanding,reserved_comparisons=used,exported=exported,
        status='incomplete' if outstanding or any('error' in j for j in jobs) or any(
            any(r.startswith('invalid_record_contract') for r in item['reasons']) for item in records) else 'evaluated',
        scope='Candidate-pair evaluation with existing gates; no implicit task/anatomy inference, processing, or population matching.')
    (output/'assessment.json').write_text(json.dumps(report,indent=2))
    return report


def collect_saved_documents(acquired, allow_retrieve=True):
    from harmo.document_evidence import extract
    from harmo.related_documents import related_dois,retrieve
    documents, held = [], []
    attempted = 0
    for entry in acquired['entries']:
        if not entry.get('source_report'): continue
        stage = Path(entry['source_report'])
        if entry.get('repository') == 'physionet':
            try:
                description = stage/'description.html'
                receipt = json.loads((stage/'source.evidence.json').read_text())
                raw = description.read_bytes()
                if hashlib.sha256(raw).hexdigest() != receipt['source_sha256']:
                    raise ValueError('description_hash_mismatch')
                documents.append(dict(dataset=entry['dataset'], result=extract(raw, receipt['source_uri'])))
                from harmo.scoped_prose import extract as join_prose
                for record in entry.get('details',{}).get('records',[]):
                    if record['status'] != 'converted': continue
                    relative=Path(record['name'])
                    header=stage/'raw'/relative
                    if relative.is_absolute() or '..' in relative.parts or not header.resolve().is_relative_to((stage/'raw').resolve()):
                        raise ValueError('header_outside_acquisition')
                    metadata=json.loads((Path(record['location'])/'record.json').read_text())['metadata']
                    header_raw=header.read_bytes()
                    if hashlib.sha256(header_raw).hexdigest()!=metadata['source_sha256'][record['name']]:
                        raise ValueError('header_hash_mismatch')
                    result=join_prose(raw,receipt['source_uri'],record['name'],header_raw,header.as_uri())
                    documents.append(dict(dataset=entry['dataset'],result=result))
            except Exception as exc:
                held.append(dict(source=str(stage),reason='physionet_document_binding: '+str(exc)))
            continue
        meta = stage/'repository.metadata.json'
        if meta.is_file() and meta.stat().st_size<=2*1024**2:
            try:
                metadata_document = json.loads(meta.read_text())
                if not isinstance(metadata_document,dict):
                    raise ValueError('repository_metadata_object_required')
            except Exception as exc:
                held.append(dict(source=str(meta),reason='invalid_repository_metadata: '+str(exc)))
                metadata_document = {}
            description = metadata_document.get('description')
            if isinstance(description,str) and description.strip():
                try:
                    documents.append(dict(dataset=entry['dataset'],result=extract(description.encode(),meta.as_uri()+'#description')))
                except Exception as exc:
                    held.append(dict(source=str(meta),reason=str(exc)))
            for linked in related_dois(metadata_document):
                if attempted>=5:
                    held.append(dict(source=str(meta),doi=linked['doi'],reason='related_document_budget')); continue
                attempted+=1
                try:
                    article=stage/('related-article-%03d.xml'%attempted)
                    if article.exists() and article.with_suffix('.receipt.json').exists():
                        receipt=json.loads(article.with_suffix('.receipt.json').read_text())
                        if receipt['doi'].casefold()!=linked['doi'].casefold() or hashlib.sha256(article.read_bytes()).hexdigest()!=receipt['sha256']:
                            raise ValueError('cached_related_document_changed')
                    else:
                        if not allow_retrieve:
                            raise ValueError('saved_related_document_required_offline')
                        receipt=retrieve(linked['doi'],article)
                        receipt['repository_link']=linked
                        (article.with_suffix('.receipt.json')).write_text(json.dumps(receipt,indent=2))
                    documents.append(dict(dataset=entry['dataset'],repository_metadata_path=str(meta),acquisition=receipt,result=extract(article.read_bytes(),receipt['url'],'xml')))
                except Exception as exc:
                    held.append(dict(source=str(meta),doi=linked['doi'],reason=str(exc)))
        receipt = stage/'acquisition.json'
        try:
            accepted = json.loads(receipt.read_text()) if receipt.is_file() else []
            if not isinstance(accepted,list) or any(not isinstance(a,dict) for a in accepted):
                raise ValueError('acquisition_list_required')
        except Exception as exc:
            held.append(dict(source=str(receipt),reason='invalid_acquisition_receipt: '+str(exc)))
            accepted = []
        count=0
        for asset in accepted:
            relative = Path(asset.get('local_name',asset.get('name','')))
            path = stage/'raw'/relative
            if relative.is_absolute() or '..' in relative.parts: continue
            if path.suffix.lower() not in ('.html','.htm','.xml','.json','.csv','.tsv','.zip') or path.is_symlink() or not path.is_file(): continue
            if not path.resolve().is_relative_to((stage/'raw').resolve()):
                held.append(dict(source=str(path),reason='document_path_outside_acquisition')); continue
            if any(p.startswith('.') for p in path.relative_to(stage/'raw').parts): continue
            count+=1
            if count>100:
                held.append(dict(source=str(stage),reason='document_count_budget')); break
            try:
                if path.suffix.lower() in ('.csv','.tsv'):
                    import csv
                    from harmo.document_evidence import HEADERS
                    with path.open(encoding='utf-8-sig') as stream:
                        line=stream.readline(65537)
                        if len(line)>65536: continue
                        header=next(csv.reader([line],delimiter='\t' if path.suffix.lower()=='.tsv' else ','),[])
                    if not any(HEADERS.get(h.lower().strip().replace('_',' '))=='record' for h in header):
                        continue  # Numeric waveforms are not record-metadata tables.
                if path.suffix.lower()=='.zip':
                    from harmo.archive_documents import read_documents
                    if not asset.get('verification',{}).get('pass'):
                        raise ValueError('document_acquisition_not_verified')
                    archive=read_documents(path,asset.get('sha256'))
                    documents.extend(dict(dataset=entry['dataset'],**d) for d in archive['documents'])
                    held.extend(dict(source=str(path),**h) for h in archive['held'])
                    continue
                if path.stat().st_size>2*1024**2 and path.suffix.lower() in ('.csv','.tsv'):
                    # A large waveform is not presumed to be a metadata table.
                    continue
                if path.stat().st_size>2*1024**2: raise ValueError('document_size_budget')
                if not asset.get('verification',{}).get('pass') or hashlib.sha256(path.read_bytes()).hexdigest()!=asset.get('sha256'):
                    raise ValueError('document_acquisition_hash_mismatch')
                documents.append(dict(dataset=entry['dataset'],result=extract(path.read_bytes(),path.as_uri(),
                    {'.xml':'xml','.json':'json','.csv':'csv','.tsv':'tsv'}.get(path.suffix.lower(),'html'))))
            except Exception as exc:
                held.append(dict(source=str(path),reason=str(exc)))
    from harmo.repository_context import join_documents
    documents=join_documents(documents,acquired['entries'])
    from harmo.mat_document_semantics import extract as mat_semantics
    for entry in acquired['entries']:
        if not entry.get('source_report') or entry.get('repository')=='physionet':continue
        stage=Path(entry['source_report']).resolve()
        grouped={}
        for record in entry.get('ingestion',{}).get('records',[]):
            if Path(record['source']).suffix.lower()=='.mat':grouped.setdefault(record['source'],[]).append(record)
        for source,records in grouped.items():
            path=(stage/'raw'/source).resolve()
            if not path.is_relative_to(stage/'raw'):raise ValueError('MAT_source_outside_acquisition')
            for article in sorted(stage.glob('related-article-*.xml')):
                try:
                    receipt=json.loads(article.with_suffix('.receipt.json').read_text())
                    if hashlib.sha256(article.read_bytes()).hexdigest()!=receipt['sha256']:raise ValueError('cached_related_document_changed')
                    documents.append(dict(dataset=entry['dataset'],result=mat_semantics(path,records,article)))
                except (ValueError,KeyError,OSError,TypeError) as exc:
                    held.append(dict(source=str(article),reason='MAT_document_join:'+str(exc)))
    return dict(documents=documents,held=held,scope='Downloaded HTML/XML and explicit JSON/CSV/TSV record metadata, including direct members of verified ZIP assets; descriptions and explicitly documented-by article DOIs resolved through Europe PMC. No nested archive, broad literature search or PDF interpretation; large CSV/TSV waveforms are not parsed as metadata tables.')


def run(urls, output, purposes, r_library='', max_bytes=1024**3, max_records=None, max_comparisons=1000,
        local_llm_model=None,local_llm_port=11439,max_unit_channels=32,
        timing_profile=None,source_cache=None,timing_protocol=None,article=None,download=False,resume=False):
    # Validate before downloads or writes.
    if not isinstance(purposes,list) or not purposes or any(p not in PURPOSES for p in purposes) or len(set(purposes))!=len(purposes):
        raise ValueError('unique_supported_purposes_required')
    if type(max_comparisons) is not int or max_comparisons < 1:
        raise ValueError('positive_comparison_budget_required')
    if timing_profile is not None:
        # Explicit registered execution, never automatic scientific profile inference.
        if timing_profile not in ('study38-gait120','study38-gait120-planned') or purposes != ['activation-timing']:
            raise ValueError('registered_profile_requires_activation_timing_only')
        if not source_cache or not timing_protocol or max_records is not None or local_llm_model is not None:
            raise ValueError('registered_profile_requires_cache_protocol_and_no_record_limit_or_llm_override')
        if max_comparisons < 20:
            raise ValueError('registered_profile_requires_20_comparisons')
        from harmo.reuse_workflow import run as registered_run
        if timing_profile=='study38-gait120-planned':
            registered=registered_run(urls,source_cache,timing_protocol,article,output,resume,download,max_bytes,planned=True)
        else:
            registered=registered_run(urls,source_cache,timing_protocol,article,output,resume,download,max_bytes)
        result=dict(version='automatic-workflow/registered-timing-1',
            status=('completed' if registered['status']=='completed' else 'completed-with-holds') if timing_profile=='study38-gait120-planned' else ('completed-with-holds' if registered['status']=='completed' else 'partial'),
            intake=dict(run_dir=str(Path(output).resolve())),
            assessment=dict(exported=registered['timing']['released'],status=registered['timing']['status']),
            registered_timing=registered,
            scope='Explicit registered two-study profile; not inferred from arbitrary URLs or LLM candidates.')
        (Path(output)/'automatic.summary.json').write_text(json.dumps(result,indent=2))
        return result
    if any((source_cache,timing_protocol,article,download,resume)):
        raise ValueError('timing_options_require_explicit_profile')
    if local_llm_model is not None and (not isinstance(local_llm_model,str) or not local_llm_model or 'cloud' in local_llm_model.lower() or not 1024<=local_llm_port<=65535 or max_unit_channels<1):
        raise ValueError('invalid_local_review_options')
    from harmo.mixed_batch import run as intake
    acquired = intake(urls,output,r_library,max_bytes,max_records)
    evidence = collect_saved_documents(acquired)
    from harmo.catalog_evidence import ARTICLE_HASH,promote_units
    dictionary_bindings=[]
    for index,document in enumerate(evidence['documents']):
        receipt=document.get('acquisition',{})
        if receipt.get('sha256')!=ARTICLE_HASH or not document.get('repository_metadata_path'):
            continue
        try:
            result=promote_units(Path(acquired['catalog']).parent,receipt['path'],
                Path(acquired['run_dir'])/f'dictionary-binding-{index:03d}.json',record_semantics=True,
                linked_metadata=document['repository_metadata_path'])
            dictionary_bindings.append(dict(dataset=document['dataset'],result=result))
        except Exception as exc:
            dictionary_bindings.append(dict(dataset=document['dataset'],error=str(exc)))
    directory = Path(acquired['run_dir'])/'purpose-assessment'
    unit_review=None
    if local_llm_model:
        from harmo.catalog_unit_review import review_catalog
        unit_review=review_catalog(acquired['catalog'],directory.parent/'unit-review',
            {e['dataset'] for e in acquired['entries'] if 'dataset' in e},local_llm_model,local_llm_port,max_unit_channels)
        from harmo.unit_review_store import save_review, load_review
        save_review(acquired['catalog'],unit_review)
        unit_review=load_review(acquired['catalog'])
    assessed = assess_catalog(acquired['catalog'],directory,purposes,max_comparisons,
        datasets={e['dataset'] for e in acquired['entries'] if 'dataset' in e},documents=evidence['documents'],unit_review=unit_review)
    result = dict(version='automatic-workflow/0.1',intake=acquired,evidence=evidence,assessment=assessed,
        status='completed-with-holds' if assessed['status']=='evaluated' and acquired['status']!='partial' and not evidence['held'] else 'partial',
        remaining_capabilities=['unstructured_document_interpretation','general_record_scope_resolution','automatic_processing_planner','unseen_study_validation'])
    result['dictionary_bindings']=dictionary_bindings
    result['unit_review']=unit_review
    if any('error' in b or b.get('result',{}).get('held',0) for b in dictionary_bindings):
        result['status']='partial'
    (directory.parent/'automatic.summary.json').write_text(json.dumps(result,indent=2))
    return result
