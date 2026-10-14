import hashlib
import json
import sqlite3
import pytest
from harmo import contract_revision as revision
from harmo.public_export import load_record,export_catalog
from test_public_export import setup_catalog


def fixture(tmp_path):
    request=setup_catalog(tmp_path)
    catalog=tmp_path/'catalog.sqlite';out=tmp_path/'proposal'
    revision.prepare(catalog,'1',out)
    proposal=json.loads((out/'proposal.json').read_text())
    evidence=tmp_path/'source.txt';evidence.write_text('This recording is a walking trial. EMG is a released envelope.')
    proof=dict(path=str(evidence),sha256=hashlib.sha256(evidence.read_bytes()).hexdigest(),
        quote='This recording is a walking trial.',source_uri='urn:synthetic-fixture',pointer='line1',
        record_id='1',channel_id=None,scope_reason='Synthetic record-specific test statement')
    original=json.loads((out/'current-contract.json').read_text())
    task=original['task'];task['compatibility_group']=None
    proposal.update(reviewer='Synthetic test reviewer',reason='Test revised scoped description',
                    edits=[dict(field='task',value=task,evidence=proof)])
    path=out/'proposal.json';path.write_text(json.dumps(proposal))
    return catalog,path,evidence,request


def test_revision_preserves_samples_and_invalidates_prior_approval(tmp_path):
    catalog,path,evidence,request=fixture(tmp_path)
    original=(tmp_path/'1/recording.bundle.json').read_bytes()
    samples=(tmp_path/'1/normalized.npz').read_bytes()
    assert revision.apply(catalog,path)['revision']==1
    b,t,_=load_record(('1','test/1.0.0','1',str(tmp_path/'1')))
    assert b.processing_state is None
    assert t.dimension_status('scientific_validity').value=='unassessed'
    assert (tmp_path/'1/recording.bundle.json').read_bytes()==original
    assert (tmp_path/'1/normalized.npz').read_bytes()==samples
    result=export_catalog(catalog,request,tmp_path/'out')
    assert result['exported']==0
    with pytest.raises(ValueError,match='stale'):revision.apply(catalog,path)
    evidence.write_text('Changed scope')
    with pytest.raises(ValueError,match='evidence_changed'):
        load_record(('1','test/1.0.0','1',str(tmp_path/'1')))


@pytest.mark.parametrize('change',['unit','quote','scope','equivalence'])
def test_invalid_edit_cannot_create_revision(tmp_path,change):
    catalog,path,evidence,_=fixture(tmp_path)
    p=json.loads(path.read_text())
    if change=='unit':p['edits'][0]['field']='unit'
    if change=='quote':p['edits'][0]['evidence']['quote']='not in source'
    if change=='scope':p['edits'][0]['evidence']['record_id']='2'
    if change=='equivalence':p['edits'][0]['value']['compatibility_group']='invented'
    path.write_text(json.dumps(p))
    with pytest.raises(ValueError):revision.apply(catalog,path)
    assert not (tmp_path/'1/contract-revisions.sqlite').exists()


def test_processing_note_does_not_manufacture_processing_state(tmp_path):
    catalog,path,evidence,_=fixture(tmp_path)
    p=json.loads(path.read_text())
    p['edits'][0].update(field='processing_note',value='Released envelope; preprocessing parameters require review.')
    path.write_text(json.dumps(p));revision.apply(catalog,path)
    b,_,_=load_record(('1','test/1.0.0','1',str(tmp_path/'1')))
    assert b.processing_state is None


def test_anatomy_revision_is_channel_scoped_and_parent_evidence_retained(tmp_path):
    catalog,path,evidence,_=fixture(tmp_path)
    p=json.loads(path.read_text())
    current=json.loads((path.parent/'current-contract.json').read_text())
    channel=current['channels'][0]
    p['edits'][0].update(field='anatomy',channel_id=channel['channel_id'],value=channel['anatomy'])
    p['edits'][0]['evidence']['channel_id']=channel['channel_id']
    # Synthetic scope declaration tests binding, not the truth of an anatomy claim.
    path.write_text(json.dumps(p));revision.apply(catalog,path)
    revision.prepare(catalog,'1',tmp_path/'next')
    q=json.loads((tmp_path/'next/proposal.json').read_text())
    second=tmp_path/'second.txt';second.write_text('processing note')
    q.update(reviewer='Synthetic test reviewer',reason='second revision',edits=[dict(
        field='processing_note',value='processing note',evidence=dict(path=str(second),
        sha256=hashlib.sha256(second.read_bytes()).hexdigest(),quote='processing note',
        record_id='1',channel_id=None,source_uri='urn:test:second',pointer='line1',scope_reason='fixture'))])
    qp=tmp_path/'next/proposal.json';qp.write_text(json.dumps(q));revision.apply(catalog,qp)
    with sqlite3.connect(tmp_path/'1/contract-revisions.sqlite') as db:
        assert db.execute('SELECT COUNT(*) FROM revisions').fetchone()[0]==2
    evidence.write_text('changed ancestor evidence')
    with pytest.raises(ValueError,match='evidence_changed'):
        load_record(('1','test/1.0.0','1',str(tmp_path/'1')))
