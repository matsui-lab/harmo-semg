import json
from copy import deepcopy
from uuid import uuid4
import pytest
from test_semantic_review import packet
from test_semantic_server import session
from harmo.semantic_review import reviewed_documents


def test_anonymous_confirmation_keeps_evidence_checks(tmp_path):
    path, decisions, _ = packet(tmp_path)
    edits = json.loads(decisions.read_text())
    edits['reviewer'] = ''
    edits['review_actor'] = dict(kind='anonymous-local-session', id=str(uuid4()))
    decisions.write_text(json.dumps(edits))
    docs, metrics = reviewed_documents(path, decisions)
    observation = docs[0]['result']['observations'][0]
    assert observation['scope_rule']=='anonymous-human-semantic-review/1'
    assert observation['reviewer'].startswith('anonymous-session:')
    assert metrics['reviewer_identity']=='anonymous-session'
    assert metrics['scientific_approvals']==0
    edits['decisions'][0]['evidence_ids']=['invented']
    decisions.write_text(json.dumps(edits))
    with pytest.raises(ValueError, match='explicit_evidence_required'):
        reviewed_documents(path, decisions)


@pytest.mark.parametrize('actor', [{}, {'kind':'named-person','id':'x'}, {'kind':'anonymous-local-session','id':'x'}])
def test_invalid_anonymous_actor_rejected(tmp_path, actor):
    path, decisions, _ = packet(tmp_path)
    edits=json.loads(decisions.read_text());edits.update(reviewer='',review_actor=actor)
    decisions.write_text(json.dumps(edits))
    with pytest.raises(ValueError,match='anonymous_review_actor'):
        reviewed_documents(path, decisions)


def test_no_name_review_to_processing_and_registration(tmp_path):
    s=session(tmp_path)
    edits=deepcopy(s.data['decisions'])
    edits['review_actor']=dict(kind='anonymous-local-session',id=str(uuid4()))
    target=next(i['target']['target_id'] for i in s.data['items'] if i['target']['field']=='anatomy')
    row=next(d for d in edits['decisions'] if d['target_id']==target)
    row.update(action='hold',scope_reason='Synthetic test: insufficient evidence')
    evaluated=s.evaluate(edits)
    assert evaluated['review_metrics']['reviewer_identity']=='anonymous-session'
    result=s.execute(evaluated['run_id'])
    assert result['curves']<2 and not result['scientific_approval']
    saved=s.register(evaluated['run_id'])
    exported=json.loads(s.collection.artifact(saved['id'],'decisions.json'))
    assert exported['reviewer']=='' and exported['review_actor']==edits['review_actor']
