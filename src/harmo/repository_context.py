"""Record provenance across repository payloads, archives and converted streams."""
import json
from pathlib import Path
from harmo.public_batch import sha

REPOSITORIES = ('figshare', 'zenodo', 'mendeley')


def records(entry):
    return entry.get('details', {}).get('records', []) if entry.get('repository') == 'physionet' else entry.get('ingestion', {}).get('records', [])


def bound_files(entry):
    stage = Path(entry['source_report']).resolve()
    receipt = json.loads((stage/'repository.evidence.json').read_text())
    if sha(stage/'repository.metadata.json') != receipt['source_sha256']:
        raise ValueError('repository_metadata_changed')
    files = [stage/'repository.evidence.json', stage/'repository.metadata.json']
    for evidence in sorted(stage.glob('related-article-*.receipt.json')):
        article = evidence.with_suffix('').with_suffix('.xml')
        receipt = json.loads(evidence.read_text())
        if sha(article) != receipt['sha256']:
            raise ValueError('cached_related_document_changed')
        files.extend([evidence, article])
    acquisition = stage/'acquisition.json'
    if not acquisition.exists():
        if records(entry): raise ValueError('acquisition_receipt_missing')
        return files
    files.append(acquisition)
    for asset in json.loads(acquisition.read_text()):
        path = (stage/'raw'/asset.get('local_name',asset['name'])).resolve()
        if not path.is_relative_to(stage/'raw') or not asset.get('verification',{}).get('pass') or sha(path) != asset['sha256']:
            raise ValueError('repository_payload_changed')
        files.append(path)
    for archive in entry.get('archives', []):
        if archive['status'] != 'extracted': continue
        path = Path(archive['provenance']).resolve()
        if not path.is_relative_to(stage): raise ValueError('archive_provenance_outside_source')
        files.append(path)
        manifest = json.loads(path.read_text())
        source, destination = Path(manifest['source']).resolve(), Path(manifest['destination']).resolve()
        if source not in files or sha(source) != manifest['source_sha256'] or not destination.is_relative_to(stage/'raw'):
            raise ValueError('archive_source_binding_changed')
        for member in manifest['members']:
            extracted = (destination/member['member']).resolve()
            if not extracted.is_relative_to(destination) or sha(extracted) != member['sha256']:
                raise ValueError('archive_member_changed')
            files.append(extracted)
    return files


def source_paths(entry, record, catalog):
    stage = Path(entry['source_report']).resolve()
    location = Path(record['location'])
    metadata = json.loads((location/'record.json').read_text())['metadata']
    output = []
    for relative, expected in metadata['source_sha256'].items():
        root = stage/'raw' if entry['repository'] == 'physionet' else Path(catalog).resolve().parent/'sources'
        path = (root/relative).resolve()
        if not path.is_relative_to(root) or sha(path) != expected:
            raise ValueError('record_source_changed_or_outside_store')
        output.append(path)
    if entry['repository'] != 'physionet':
        original = (stage/'raw'/record['source']).resolve()
        if not original.is_relative_to(stage/'raw') or sha(original) not in metadata['source_sha256'].values():
            raise ValueError('repository_file_to_record_binding_changed')
        output.append(original)
    return output


def join_documents(documents, entries):
    """Unique repository filename -> payload path -> stream, never guessed aliases."""
    joined = []
    for entry in entries:
        if entry.get('repository') not in REPOSITORIES: continue
        mapping = {}
        for r in records(entry): mapping.setdefault(r['source'], []).append(r)
        stage = Path(entry['source_report'])
        metadata = json.loads((stage/'repository.metadata.json').read_text())
        # Count names across the entire listed repository, not just downloads:
        # selecting one of two same-named files must not make its name unique.
        name_counts = {}
        listed = metadata.get('files', [])
        if isinstance(listed, dict):
            listed = [dict(row, key=key) for key,row in listed['entries'].items()]
        for row in listed:
            name = row.get('name', row.get('filename',row.get('key')))
            name_counts[name] = name_counts.get(name, 0)+1
        aliases = {}
        acquisition = stage/'acquisition.json'
        if acquisition.exists():
            for asset in json.loads(acquisition.read_text()):
                if name_counts.get(asset['name']) == 1:
                    aliases[asset['name']] = asset.get('local_name', asset['name'])
        member_paths = {}
        for archive in entry.get('archives', []):
            if archive['status'] != 'extracted': continue
            manifest = json.loads(Path(archive['provenance']).read_text())
            folder = Path(manifest['destination']).relative_to(stage/'raw')
            for member in manifest['members']:
                member_paths.setdefault(member['member'], []).append(str(folder/member['member']))
        for name, paths in member_paths.items():
            if len(paths)==1 and name not in name_counts:
                aliases[name] = paths[0]
        for document in documents:
            if document['dataset'] != entry['dataset']: continue
            additions = []
            for observation in document['result']['observations']:
                origin = observation['record']
                relative = aliases.get(origin, origin)
                matches = mapping.get(relative, [])
                if len(matches) == 1:
                    r = matches[0]
                    additions.append(dict(observation, record=r['name'],
                        file_to_record_join=dict(source=origin,local_source=relative,record=r['name'],
                            rule='unique-repository-file-one-converted-stream/2')))
            if additions: joined.append(dict(dataset=entry['dataset'],result=dict(observations=additions)))
    return documents+joined


def review_cases(entry, catalog, evidence, max_channels, record_ids):
    """Use actual proposal schema as a schema source, never a fake WFDB header."""
    from lxml import etree
    stage = Path(entry['source_report']).resolve()
    paths = bound_files(entry)
    meta = stage/'repository.metadata.json'
    document = json.loads(meta.read_text())
    description = document.get('description','')
    node = etree.fromstring(('<div>'+description+'</div>').encode(), etree.HTMLParser(no_network=True))
    text = ' '.join(node.itertext()) if node is not None else description
    source = [dict(source_id='description',text=text,path=str(meta),source_sha256=sha(meta),pointer='/description',kind='json-description')]
    # Include downloaded small text documents. Waveforms and derived schemas do
    # not count as prose evidence for anatomy/task confirmation.
    for path in paths:
        if path.suffix.lower() in ('.md','.txt','.html','.xml') and path.stat().st_size <= 100000:
            source.append(dict(source_id='document-'+str(len(source)),text=path.read_text(),path=str(path),
                               source_sha256=sha(path),pointer='whole-file',kind='text'))
    cases, omitted = [], 0
    for record in records(entry):
        if record_ids is not None and record['id'] not in record_ids: continue
        paths.extend(source_paths(entry,record,catalog))
        location = Path(record['location']);schema = location/'proposal.json'
        paths.extend([schema,location/'record.json'])
        meta = json.loads((location/'record.json').read_text())['metadata']
        sources = source+[dict(source_id='header',text=schema.read_text(),path=str(schema),source_sha256=sha(schema),
                               pointer='whole-proposal',kind='derived-record-schema')]
        targets, rules = [], []
        signals = meta['signals'];omitted += max(0,len(signals)-max_channels)*4
        for index,ch in enumerate(signals[:max_channels]):
            for field in ('sensor_type','anatomy','measurement_side','unit'):
                targets.append(dict(target_id=f"{record['id']}:ch-{index}:{field}",record_id=record['id'],record=record['name'],
                    channel=ch['label'],channel_id=f'ch-{index}',field=field))
        for field in ('task','complete_processing_protocol','amplitude_normalization'):
            targets.append(dict(target_id=record['id']+':'+field,record_id=record['id'],record=record['name'],channel='',channel_id=None,field=field))
        facts = [o for d in evidence if d['dataset']==entry['dataset'] for o in d['result']['observations'] if o['record']==record['name']]
        for t in targets:
            field = 'side' if t['field']=='measurement_side' else t['field']
            values = sorted({o['value'] for o in facts if o['channel']==t['channel'] and o['field']==field})
            rules.append(dict(target_id=t['target_id'],values=values,status='proposed' if len(values)==1 else 'ambiguous' if values else 'insufficient'))
        cases.append(dict(case_id=record['id'],dataset=entry['dataset'],record=record['name'],sources=sources,targets=targets,rules=rules,
                          record_pointer='proposal:'+record['source'],schema_is_not_semantic_evidence=True))
    return cases, [dict(path=str(p),sha256=sha(p)) for p in dict.fromkeys(paths)], omitted
