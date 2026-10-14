import hashlib
import json
import pytest
from harmo import semantic_review as review
from harmo.llm_candidates import digest


def packet(tmp_path):
    source=tmp_path/'source.txt';source.write_text('Surface EMG was recorded from this channel.')
    summary=tmp_path/'summary.json';summary.write_text('{}')
    source_hash=hashlib.sha256(source.read_bytes()).hexdigest()
    data=dict(version='semantic-review/1',summary_path=str(summary),summary_sha256=hashlib.sha256(summary.read_bytes()).hexdigest(),
              bindings=[dict(path=str(source),sha256=source_hash)],cases=[dict(case_id='r',dataset='test',record='x.hea',
              targets=[dict(target_id='r:ch-0:sensor_type',record_id='r',record='x.hea',channel='TA',channel_id='ch-0',field='sensor_type')],
              sources=[dict(source_id='p0',path=str(source),source_sha256=source_hash,pointer='line1',text=source.read_text())])])
    data['packet_id']=digest(data)
    path=tmp_path/'packet.json';path.write_text(json.dumps(data))
    edits=dict(packet_id=data['packet_id'],reviewer='Synthetic reviewer',active_seconds=None,
               decisions=[dict(target_id='r:ch-0:sensor_type',action='confirm',value='surface-emg',evidence_ids=['p0'],scope_reason='Synthetic record-specific evidence')])
    decisions=tmp_path/'decisions.json';decisions.write_text(json.dumps(edits))
    return path,decisions,source


def test_named_review_is_metadata_only(tmp_path):
    path,decisions,_=packet(tmp_path)
    docs,metrics=review.reviewed_documents(path,decisions)
    assert docs[0]['result']['observations'][0]['value']=='surface-emg'
    assert metrics['scientific_approvals']==0 and metrics['self_reported_active_seconds'] is None
    receipt=review.apply(path,decisions,tmp_path/'receipt')
    assert receipt['metrics']['reviewed_items']==1


@pytest.mark.parametrize('change',['reviewer','null_reviewer','target','evidence','reason','duplicate'])
def test_invalid_review_is_rejected(tmp_path,change):
    path,decisions,_=packet(tmp_path);d=json.loads(decisions.read_text())
    if change=='reviewer':d['reviewer']=''
    if change=='null_reviewer':d['reviewer']=None
    if change=='target':d['decisions'][0]['target_id']='wrong'
    if change=='evidence':d['decisions'][0]['evidence_ids']=['invented']
    if change=='reason':d['decisions'][0]['scope_reason']=''
    if change=='duplicate':d['decisions'].append(d['decisions'][0])
    decisions.write_text(json.dumps(d))
    with pytest.raises(ValueError):review.reviewed_documents(path,decisions)


def test_changed_evidence_fails_closed(tmp_path):
    path,decisions,source=packet(tmp_path);source.write_text('changed')
    with pytest.raises(ValueError,match='bound_source_changed'):review.reviewed_documents(path,decisions)


def test_cloud_model_never_reaches_network(tmp_path):
    path,_,_=packet(tmp_path)
    with pytest.raises(ValueError,match='bounded_local_model_required'):
        review.propose(path,tmp_path/'out','example-cloud',transport=lambda *a:pytest.fail('network called'))


def test_local_candidate_never_becomes_confirmation(tmp_path):
    path,_,_=packet(tmp_path)
    def api(name,payload=None):
        if name=='show':return {}
        if name=='tags':return dict(models=[dict(name='local',digest='test')])
        if name=='version':return dict(version='fixture')
        return dict(done=True,done_reason='stop',message=dict(content=json.dumps(dict(status='proposed',value='surface-emg',alternatives=[],evidence_ids=['p0']))))
    result=review.propose(path,tmp_path/'out','local',transport=api)
    assert result['automatic_approvals']==0 and not result['accuracy_evaluated']
    assert result['rows'][0]['status']=='requires_semantic_review'


def test_human_hold_is_an_export_block_not_just_missing_confirmation(tmp_path):
    path,decisions,_=packet(tmp_path);d=json.loads(decisions.read_text())
    d['decisions'][0]['action']='hold';decisions.write_text(json.dumps(d))
    docs,_=review.reviewed_documents(path,decisions)
    assert docs and docs[0]['result']['observations'][0]['status']=='review_hold'
