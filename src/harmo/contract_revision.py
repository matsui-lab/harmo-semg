"""Source-bound, explicitly reviewed metadata revisions; never scientific approval."""
import hashlib
import json
import sqlite3
from datetime import datetime, timezone
from pathlib import Path
from harmo.evidence.models import HarmoRecordingBundle, TrustReport, TrustEvidence


def digest(value):
    return hashlib.sha256(json.dumps(value,sort_keys=True,allow_nan=False).encode()).hexdigest()


def _base_path(directory):
    for name in ('recording.semantic.v002.bundle.json','recording.semantic.bundle.json','recording.bundle.json'):
        if (directory/name).exists():return directory/name
    raise ValueError('base_contract_missing')


def _file_sha(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def _check_evidence(evidence, rid, cid):
    if evidence.get('record_id')!=rid or evidence.get('channel_id')!=cid:
        raise ValueError('exact_record_channel_scope_required')
    path=Path(evidence['path'])
    if not path.is_absolute() or not evidence.get('source_uri') or not evidence.get('pointer'):
        raise ValueError('source_location_required')
    if path.stat().st_size>2*1024**2:raise ValueError('evidence_size_limit')
    raw=path.read_bytes()
    if hashlib.sha256(raw).hexdigest()!=evidence['sha256']:raise ValueError('evidence_changed')
    quote=evidence.get('quote','')
    if not quote.strip() or quote not in raw.decode('utf-8'):
        raise ValueError('verbatim_evidence_quote_required')
    if not evidence.get('scope_reason','').strip():raise ValueError('scope_reason_required')
    return evidence['source_uri']+'#'+evidence['pointer']+':sha256:'+evidence['sha256']


def load(directory, bundle, trust):
    """Called by normal catalog reads. Stale revisions fail closed."""
    path=directory/'contract-revisions.sqlite'
    if not path.exists():return bundle,trust
    with sqlite3.connect(path.as_uri()+'?mode=ro',uri=True) as db:
        row=db.execute('SELECT payload FROM revisions ORDER BY id DESC LIMIT 1').fetchone()
    if row is None:return bundle,trust
    event=json.loads(row[0])
    if event['base_sha256']!=_file_sha(_base_path(directory)):
        raise ValueError('reviewed_base_contract_changed')
    if _file_sha(directory/'normalized.npz')!=event['sample_sha256']:
        raise ValueError('reviewed_samples_changed')
    for edit in event['edits']:
        _check_evidence(edit['evidence'],bundle.recording_id,edit.get('channel_id'))
    revised=HarmoRecordingBundle.from_dict(event['bundle'])
    if revised.recording_id!=bundle.recording_id or revised.samples.to_dict()!=bundle.samples.to_dict():
        raise ValueError('reviewed_identity_or_samples_changed')
    result=TrustReport.from_dict(trust.to_dict())
    for dimension in ('semantic_fidelity','scientific_validity'):
        result.evidence.append(TrustEvidence(dimension,'unassessed','reviewed-contract-requires-revalidation',
            'Metadata revision invalidates previous semantic/scientific approval.'))
    return revised,result


def _row(catalog,rid):
    with sqlite3.connect(Path(catalog).resolve().as_uri()+'?mode=ro',uri=True) as db:
        row=db.execute('SELECT id,dataset,name,location FROM records WHERE id=?',(rid,)).fetchone()
    if row is None:raise ValueError('record_missing')
    return row


def prepare(catalog,rid,output):
    from harmo.public_export import load_record,checked_samples
    bundle,_,directory=load_record(_row(catalog,rid));checked_samples(bundle,directory)
    proposal=dict(version='contract-revision/1',record_id=rid,
        input_contract_sha256=digest(bundle.to_dict()),sample_sha256=bundle.samples.checksum,
        reviewer='',reason='',edits=[])
    out=Path(output);out.mkdir(parents=True,exist_ok=False)
    (out/'proposal.json').write_text(json.dumps(proposal,indent=2))
    (out/'current-contract.json').write_text(json.dumps(bundle.to_dict(),indent=2))
    return dict(output=str(out),record_id=rid,scope='task, anatomy, processing_note; no unit or sample edits')


def apply(catalog,proposal_path):
    from harmo.public_export import load_record,checked_samples
    proposal=json.loads(Path(proposal_path).read_text());rid=proposal['record_id']
    if not proposal.get('reviewer','').strip() or not proposal.get('reason','').strip():
        raise ValueError('reviewer_and_reason_required')
    bundle,_,directory=load_record(_row(catalog,rid));checked_samples(bundle,directory)
    if digest(bundle.to_dict())!=proposal['input_contract_sha256'] or bundle.samples.checksum!=proposal['sample_sha256']:
        raise ValueError('stale_contract_proposal')
    data=bundle.to_dict();seen=set();notes=[]
    if not proposal['edits']:raise ValueError('edits_required')
    for edit in proposal['edits']:
        field=edit['field'];cid=edit.get('channel_id')
        if (field,cid) in seen:raise ValueError('duplicate_field')
        seen.add((field,cid))
        ref=_check_evidence(edit['evidence'],rid,cid)
        mapping=dict(method='explicit-scoped-review',mapping_version='1',status='mapped',evidence_refs=[ref])
        if field=='task' and cid is None:
            value=dict(edit['value'])
            if value.get('compatibility_group') is not None:raise ValueError('task_equivalence_not_review_editable')
            value['mapping']=mapping;data['task']=value
        elif field=='anatomy' and cid is not None:
            matches=[c for c in data['channels'] if c['channel_id']==cid]
            if len(matches)!=1:raise ValueError('unique_channel_required')
            value=dict(edit['value']);value['mapping']=mapping
            matches[0].update(anatomy=value,side=value['laterality'],
                muscle=value['canonical_label'] if value['entity_type']=='muscle' else None)
        elif field=='processing_note' and cid is None and isinstance(edit['value'],str) and edit['value'].strip():
            notes.append(dict(text=edit['value'],evidence_ref=ref))
        else:raise ValueError('unsupported_revision_field')
    data['schema_version']='1.2.0'
    # Documentary processing descriptions are not validated processing execution.
    data['processing_state']=None
    revised=HarmoRecordingBundle.from_dict(data)
    from jsonschema import validate
    from harmo.schema_resources import schema_path
    validate(revised.to_dict(),json.loads(schema_path('recording-bundle-1.2.0.json').read_text()))
    event=dict(proposal,base_sha256=_file_sha(_base_path(directory)),bundle=revised.to_dict(),
               processing_notes=notes,created=datetime.now(timezone.utc).isoformat())
    with sqlite3.connect(directory/'contract-revisions.sqlite') as db:
        db.execute('CREATE TABLE IF NOT EXISTS revisions(id INTEGER PRIMARY KEY,payload TEXT NOT NULL)')
        db.commit();db.execute('BEGIN IMMEDIATE')
        # Check parent and evidence again immediately before committing the revision.
        current,_,_=load_record(_row(catalog,rid));checked_samples(current,directory)
        if digest(current.to_dict())!=proposal['input_contract_sha256']:raise ValueError('revision_parent_changed')
        for edit in proposal['edits']:_check_evidence(edit['evidence'],rid,edit.get('channel_id'))
        previous=db.execute('SELECT payload FROM revisions ORDER BY id DESC LIMIT 1').fetchone()
        if previous:
            event['edits']=json.loads(previous[0])['edits']+event['edits']
        db.execute('INSERT INTO revisions(payload) VALUES(?)',(json.dumps(event),))
        revision=db.execute('SELECT last_insert_rowid()').fetchone()[0]
    return dict(revision=revision,record_id=rid,scientific_approval=False,processing_execution=False)
