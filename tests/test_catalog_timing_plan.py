import json
import pytest
from test_public_export import setup_catalog
from test_timing_planner import PROTOCOL, sha
from harmo.catalog_timing_plan import prepare, current
from harmo.timing_planner import plan, execute


def test_no_assertions_holds_and_preserves_catalog(tmp_path):
    setup_catalog(tmp_path)
    catalog=tmp_path/'catalog.sqlite'; before=sha(catalog)
    p=prepare(catalog,'1','ch-1',PROTOCOL,tmp_path/'plan')
    assert p['status']=='held'
    assert 'confirmed_cycle_boundary_required' in p['reasons']
    assert sha(catalog)==before


def assertions(tmp_path):
    setup_catalog(tmp_path)
    contract=tmp_path/'1/recording.bundle.json'
    data=json.loads(contract.read_text())
    data['channels'][0]['side']='right'
    data['channels'][0]['muscle']='tibialis anterior'
    data['channels'][0]['anatomy']['canonical_label']='tibialis anterior'
    data['channels'][0]['anatomy']['laterality']='right'
    contract.write_text(json.dumps(data))
    catalog=tmp_path/'catalog.sqlite'
    b,_,c=current(catalog,'1','ch-1')
    source=tmp_path/'proof.txt';source.write_text('Synthetic test only, not scientific evidence.')
    values=dict(task=b.task.activity.lower(),muscle=c.anatomy.canonical_label,side=c.side,
                sampling_rate_hz=b.timebase.sampling_rate_hz,purpose='activation-timing',
                processing_protocol='registered-study38-gait120',representation='envelope',
                cycle_boundary='same-side-heel-strike-to-heel-strike')
    proof=dict(path=str(source),sha256=sha(source),quote=source.read_text(),source_uri='urn:test',
               pointer='line1',scope_reason='Synthetic fixture',record_id='1',channel_id='ch-1')
    facts={k:dict(value=v,status='confirmed',evidence=proof) for k,v in values.items()}
    path=tmp_path/'facts.json';path.write_text(json.dumps(facts))
    return catalog,path


def test_confirmed_channel_executes_and_stale_contract_stops(tmp_path):
    catalog,path=assertions(tmp_path)
    p=prepare(catalog,'1','ch-1',PROTOCOL,tmp_path/'plan',path)
    assert p['status']=='planned',p['reasons']
    execute(p,tmp_path/'result')
    contract=tmp_path/'1/recording.bundle.json'
    data=json.loads(contract.read_text());data['task']['source_label']='changed'
    contract.write_text(json.dumps(data))
    assert plan(p['input'],PROTOCOL)['status']=='held'
    with pytest.raises(ValueError):execute(p,tmp_path/'stale')
    assert not (tmp_path/'stale').exists()


@pytest.mark.parametrize('change',['scope','quote','muscle'])
def test_invalid_assertions_rejected(tmp_path,change):
    catalog,path=assertions(tmp_path);facts=json.loads(path.read_text())
    if change=='scope':facts['task']['evidence']['channel_id']='ch-2'
    if change=='quote':facts['task']['evidence']['quote']='absent text'
    if change=='muscle':facts['muscle']['value']='medial gastrocnemius'
    path.write_text(json.dumps(facts))
    with pytest.raises(ValueError):prepare(catalog,'1','ch-1',PROTOCOL,tmp_path/'plan',path)
    assert not (tmp_path/'plan').exists()


def test_registered_muscle_alias_only(tmp_path):
    catalog,path=assertions(tmp_path)
    contract=tmp_path/'1/recording.bundle.json';data=json.loads(contract.read_text())
    data['channels'][0]['anatomy']['canonical_label']='gastrocnemius medialis'
    data['channels'][0]['muscle']='gastrocnemius medialis'
    contract.write_text(json.dumps(data))
    facts=json.loads(path.read_text());facts['muscle']['value']='medial gastrocnemius'
    path.write_text(json.dumps(facts))
    assert prepare(catalog,'1','ch-1',PROTOCOL,tmp_path/'plan',path)['status']=='planned'


def test_changed_dependency_stops_execution(tmp_path):
    catalog,path=assertions(tmp_path)
    dependency=tmp_path/'original.bin';dependency.write_bytes(b'original source')
    facts=json.loads(path.read_text())
    facts['cycle_boundary']['evidence']['dependencies']=[dict(path=str(dependency),sha256=sha(dependency))]
    path.write_text(json.dumps(facts))
    p=prepare(catalog,'1','ch-1',PROTOCOL,tmp_path/'plan',path)
    assert p['status']=='planned'
    dependency.write_bytes(b'changed source')
    with pytest.raises(ValueError):execute(p,tmp_path/'execution')
    assert not (tmp_path/'execution').exists()
