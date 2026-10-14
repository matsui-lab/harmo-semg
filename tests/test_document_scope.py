import json
import pytest
from harmo.document_evidence import extract,match


def read(data): return extract(json.dumps(data).encode(),'https://example.org/metadata','json')


def test_finite_record_list_retains_each_target_pointer():
    result=read([dict(record=['r1','r2'],channel='A',side='left')])
    assert len(result['observations'])==2
    for i,record in enumerate(('r1','r2')):
        item=match(result['observations'],record,['A'])['attributes'][0]['evidence'][0]
        assert item['scope_evidence']['record']['pointer']==f'/0/record/{i}'
    assert not match(result['observations'],'r3',['A'])['attributes']


def test_protocol_join_keeps_full_reference_chain():
    result=read(dict(records=[dict(record='r1',protocol_id='P1')],protocols=[dict(protocol_id='P1',task='walking')]))
    item=result['observations'][0]
    assert item['record']=='r1' and item['value']=='walking' and item['channel']==''
    assert item['pointer']=='/protocols/0/task'
    assert item['scope_evidence']['protocol_reference']['pointer']=='/records/0/protocol_id'
    assert item['scope_evidence']['protocol_definition']['pointer']=='/protocols/0/protocol_id'


def test_explicit_inline_definition_does_not_silently_override_protocol():
    result=read(dict(records=[dict(record='r1',task='running',protocol_id='P1')],protocols=[dict(protocol_id='P1',task='walking')]))
    attribute=match(result['observations'],'r1',[])['attributes'][0]
    assert attribute['status']=='conflicting'


def test_missing_reference_and_unused_protocol_are_not_applied():
    result=read(dict(records=[dict(record='r1',protocol_id='missing')],protocols=[dict(protocol_id='P1',task='walking')]))
    assert not result['observations'] and result['unresolved']


def test_protocol_channel_facts_are_not_inherited():
    result=read(dict(records=[dict(record='r1',channel='A',protocol_id='P1')],protocols=[dict(protocol_id='P1',side='left',muscle='TA')]))
    assert not result['observations']


@pytest.mark.parametrize('targets',[[],['r1','r1'],['all'],['r1',3]])
def test_invalid_record_lists_remain_unresolved(targets):
    result=read([dict(record=targets,task='walking')])
    assert not result['observations'] and result['unresolved']


def test_duplicate_protocol_ids_are_rejected():
    with pytest.raises(ValueError,match='duplicate_protocol_identity'):
        read(dict(records=[],protocols=[dict(protocol_id='P1'),dict(protocol_id='P1')]))


def test_expansion_budget_including_unresolved_references():
    with pytest.raises(ValueError,match='expanded_scope_budget'):
        read([dict(record=[f'r{i}' for i in range(1000)],protocol_id='missing') for _ in range(3)])
