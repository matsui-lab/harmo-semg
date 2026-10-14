import importlib.util
import json
from pathlib import Path
import pytest

spec=importlib.util.spec_from_file_location('pilot',Path(__file__).parents[1]/'scripts/paper/run_local_llm_pilot.py')
pilot=importlib.util.module_from_spec(spec);spec.loader.exec_module(pilot)


def test_pilot_records_raw_output_without_reference_access(tmp_path):
    case=dict(case_id='test',targets=[dict(target_id='x')],sources=[dict(source_id='s',text='right')])
    packet=tmp_path/'input.json';packet.write_text(json.dumps(dict(prompt='extract',cases=[case])))
    calls=[]
    def api(path,value=None):
        calls.append((path,value))
        if path=='tags': return dict(models=[dict(name='test-model',digest='abc')])
        if path=='chat': return dict(done=True,done_reason='stop',message=dict(content=json.dumps(dict(candidates=[
            dict(target_id='x',status='proposed',value='right',alternatives=[],evidence=[dict(source_id='s',quote='right')])]))))
        return {}
    result=pilot.run(packet,tmp_path/'out','test-model',11439,0,transport=api,thinking=True,max_tokens=2048)
    assert result['validation']['structurally_complete']
    assert result['validation']['automatic_approvals']==0
    assert (tmp_path/'out/response.json').exists()
    manifest=json.loads((tmp_path/'out/manifest.json').read_text())
    assert manifest['reference_accessed'] is False
    assert manifest['peak_system_memory_bytes'] is None
    assert [p for p,_ in calls].count('chat')==1
    request=next(v for p,v in calls if p=='chat')
    assert request['think'] is True and request['options']['num_predict']==2048


def test_bad_port_rejected_before_network(tmp_path):
    with pytest.raises(ValueError,match='invalid_port'):
        pilot.run(tmp_path/'missing',tmp_path/'out','model',80,0)


@pytest.mark.parametrize('budget',[0,-1,8193])
def test_invalid_token_budget_rejected_before_network(tmp_path,budget):
    with pytest.raises(ValueError,match='invalid_token_budget'):
        pilot.run(tmp_path/'missing',tmp_path/'out','model',11439,0,max_tokens=budget)


def test_reasoning_budget_exhaustion_is_not_semantic_insufficiency(tmp_path):
    packet=tmp_path/'input.json'
    packet.write_text(json.dumps(dict(prompt='extract',cases=[dict(case_id='x',targets=[],sources=[])])))
    def api(path,value=None):
        if path=='tags':return dict(models=[dict(name='model',digest='abc')])
        if path=='chat':return dict(done=True,done_reason='length',message=dict(content=''),eval_count=2048)
        return {}
    with pytest.raises(ValueError,match='generation_budget_exhausted_without_final_answer'):
        pilot.run(packet,tmp_path/'out','model',11439,0,transport=api,thinking=True,max_tokens=2048)
    assert (tmp_path/'out/response.json').exists()
    assert not (tmp_path/'out/summary.json').exists()
