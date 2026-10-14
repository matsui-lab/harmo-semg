import json
import pytest
from test_general_workflow import fixture
from harmo.semantic_review import prepare
from harmo.review_metrics import evaluate


def test_unreviewed_items_are_not_reference_accuracy(tmp_path):
    summary=fixture(tmp_path/'source');prepare(summary,tmp_path/'packet')
    path=tmp_path/'packet/decisions.json';d=json.loads(path.read_text());d['reviewer']='Synthetic test reviewer'
    path.write_text(json.dumps(d))
    result=evaluate(tmp_path/'packet/packet.json',path)
    assert result['confirmed_reference_items']==0
    assert result['rules_reviewed_subset']['agreement_among_proposals'] is None
    assert not result['time_saving_measured']


def test_paired_comparison_uses_only_attempted_confirmed_targets(tmp_path):
    summary=fixture(tmp_path/'source');prepare(summary,tmp_path/'packet')
    path=tmp_path/'packet/decisions.json';d=json.loads(path.read_text());d['reviewer']='Synthetic test reviewer'
    selected=[x for x in d['decisions'] if x['target_id'].endswith('sensor_type')][:2]
    for x in selected:x.update(action='confirm',value='surface-emg',evidence_ids=['p0','header'],scope_reason='Synthetic source scope')
    path.write_text(json.dumps(d))
    packet=json.loads((tmp_path/'packet/packet.json').read_text()); case=packet['cases'][0]
    source=case['sources'][0]
    candidate=dict(target_id=selected[0]['target_id'],status='proposed',value='surface-emg',alternatives=[],evidence=[dict(source_id=source['source_id'],quote=source['text'])])
    cp=tmp_path/'candidates.json'
    cp.write_text(json.dumps(dict(packet_id=packet['packet_id'],rows=[dict(target_id=candidate['target_id'],validation=dict(entries=[dict(candidate=candidate)]))])))
    result=evaluate(tmp_path/'packet/packet.json',path,cp)
    paired=result['paired_attempted_and_confirmed']
    assert result['confirmed_reference_items']==2 and len(paired['targets'])==1
    assert paired['local_llm']['agreed']==1
    assert paired['local_llm']['reviewed_targets']==paired['rules']['reviewed_targets']==1
    candidate['evidence'][0]['quote']='invented quote'
    cp.write_text(json.dumps(dict(packet_id=packet['packet_id'],rows=[dict(target_id=candidate['target_id'],validation=dict(entries=[dict(candidate=candidate)]))])))
    result=evaluate(tmp_path/'packet/packet.json',path,cp)
    assert result['paired_attempted_and_confirmed']['local_llm']['proposed']==0
