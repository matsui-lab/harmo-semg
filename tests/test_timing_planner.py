import json
from pathlib import Path
import numpy as np
import pytest
from harmo.timing_planner import plan,execute,sha
from harmo.physiology_control import preprocess_study38_cycle,_normalise_cycle
from harmo.timing_workflow import checked_profile

ROOT=Path(__file__).resolve().parents[1]
PROTOCOL=ROOT/'paper/physiology_positive_control_protocol.yaml'


def fixture(tmp_path,representation='raw'):
    raw=np.random.default_rng(9).normal(size=1000)
    if representation=='envelope':raw=np.abs(raw)
    path=tmp_path/'cycle.npz';np.savez(path,values=raw)
    source=tmp_path/'evidence.txt';source.write_text('Synthetic confirmed fixture; not empirical evidence.')
    facts=dict(task='walking',purpose='activation-timing',processing_protocol='registered-study38-gait120',
               cycle_boundary='same-side-heel-strike-to-heel-strike',muscle='tibialis anterior',side='right',
               representation=representation,sampling_rate_hz=1000)
    spec=dict(sample_path=str(path),sample_sha256=sha(path),sampling_rate_hz=1000,
        facts={k:dict(value=v,status='confirmed',evidence=dict(path=str(source),sha256=sha(source),scope_reason='fixture')) for k,v in facts.items()})
    return raw,spec


@pytest.mark.parametrize('representation,count',[('raw',6),('envelope',1)])
def test_plan_execution_exactly_matches_existing_functions(tmp_path,representation,count):
    values,spec=fixture(tmp_path,representation)
    p=plan(spec,PROTOCOL);assert p['status']=='planned' and len(p['steps'])==count
    execute(p,tmp_path/'output')
    expected=preprocess_study38_cycle(values,1000,checked_profile(PROTOCOL)['study_38_envelope']) if representation=='raw' else _normalise_cycle(values)
    with np.load(tmp_path/'output/timing.npz') as output:
        assert np.array_equal(output['values'],expected)


@pytest.mark.parametrize('field',['cycle_boundary','muscle','side','processing_protocol','representation','sampling_rate_hz'])
def test_unconfirmed_condition_holds(tmp_path,field):
    _,spec=fixture(tmp_path);spec['facts'][field]['status']='candidate'
    assert plan(spec,PROTOCOL)['status']=='held'


def test_changed_evidence_or_tampered_plan_rejected(tmp_path):
    _,spec=fixture(tmp_path);p=plan(spec,PROTOCOL)
    p['steps']=p['steps'][1:]
    with pytest.raises(ValueError):execute(p,tmp_path/'out')
    p=plan(spec,PROTOCOL);(tmp_path/'evidence.txt').write_text('changed')
    with pytest.raises(ValueError):execute(p,tmp_path/'out')
    assert not (tmp_path/'out').exists()


def test_budget_and_nyquist_hold(tmp_path):
    _,spec=fixture(tmp_path)
    assert plan(spec,PROTOCOL,max_depth=5)['status']=='held'
    spec['sampling_rate_hz']=600;spec['facts']['sampling_rate_hz']['value']=600
    assert plan(spec,PROTOCOL)['status']=='held'
