import json
import numpy as np
import pytest
from harmo.footswitch_timing import DEFAULT, profile, state_masks, detect_cycles, envelope, compare


def settings():return json.loads(json.dumps(DEFAULT))


def test_known_synthetic_events_keep_sample_indices():
    fs=1000;mask=np.zeros(10000,dtype=bool)
    for start in range(1000,9000,1000):mask[start:start+300]=True
    result=detect_cycles(mask,fs,settings())
    assert result['onsets']==list(range(1000,9000,1000))
    assert len(result['cycles'])==7
    assert all(c['duration_seconds']==1 for c in result['cycles'])
    assert result['physiological_event_identity']=='unverified'


def test_chatter_not_joined_into_fake_clean_cycle():
    mask=np.zeros(8000,dtype=bool)
    for start in (1000,2000,3000,4000,5000,6000):mask[start:start+300]=True
    mask[2500:2505]=True
    result=detect_cycles(mask,1000,settings())
    assert any(c['reason']=='intervening_rejected_transition' for c in result['rejected_cycles'])
    assert not any(c['start']==2000 for c in result['cycles'])


def test_initial_true_state_is_not_invented_onset():
    mask=np.ones(500,dtype=bool)
    result=detect_cycles(mask,1000,settings())
    assert not result['onsets'] and result['status']=='held'


def test_additive_four_state_decoding_is_electrical_not_anatomical():
    levels,masks=state_masks([0,1,8,9,0,1,8,9])
    np.testing.assert_array_equal(masks['lower-component'],[0,1,0,1,0,1,0,1])
    np.testing.assert_array_equal(masks['upper-component'],[0,0,1,1,0,0,1,1])
    assert 'heel' not in masks and 'toe' not in masks


@pytest.mark.parametrize('values',[[0]*10,[0,1,8,20],np.arange(10),[0,1,np.nan],[0,1,np.inf]])
def test_ambiguous_or_invalid_signal_held(values):
    with pytest.raises(ValueError):state_masks(values)


def test_envelope_is_deterministic_and_does_not_change_input():
    fs=1500;t=np.arange(15000)/fs;x=(2+np.sin(2*np.pi*t))*np.sin(2*np.pi*100*t)
    before=x.copy();first=envelope(x,fs,settings());second=envelope(x,fs,settings())
    np.testing.assert_array_equal(first,second);np.testing.assert_array_equal(x,before)
    assert first.shape==x.shape and np.isfinite(first).all() and (first>=0).all()
    assert np.corrcoef(first[1500:-1500],(2+np.sin(2*np.pi*t[1500:-1500])))[0,1]>.99


def test_nyquist_and_missing_values_rejected():
    with pytest.raises(ValueError,match='nyquist'):envelope(np.ones(1000),600,settings())
    with pytest.raises(ValueError):envelope(np.full(1000,np.nan),1500,settings())


def test_comparison_does_not_optimize_away_a_phase_shift():
    x=1+np.cos(2*np.pi*(np.arange(101)-20)/100)
    y=1+np.cos(2*np.pi*(np.arange(101)-40)/100)
    a=dict(record='a',side='right',muscle='tibialis anterior',anchor='lower-component',median=x.tolist())
    b=dict(a,record='b',median=y.tolist());r=compare(a,b)
    assert r['circular_peak_difference_percent']==20
    assert r['zero_lag_correlation']<.5 and not r['scientific_approval']


def test_profile_cannot_claim_heel_strike(tmp_path):
    p=tmp_path/'profile.json';s=settings();s['phase_reference']='heel-strike';p.write_text(json.dumps(s))
    with pytest.raises(ValueError,match='unsupported_profile_semantics'):profile(p)


def empty_catalog_summary(tmp_path):
    import sqlite3
    catalog=tmp_path/'catalog.sqlite'
    with sqlite3.connect(catalog) as db:
        db.execute('CREATE TABLE records (id TEXT, dataset TEXT, name TEXT, location TEXT)')
    summary=tmp_path/'summary.json'
    summary.write_text(json.dumps({'intake':{'catalog':str(catalog),'entries':[]}}))
    return summary


def test_empty_catalog_never_becomes_approved(tmp_path, monkeypatch):
    import harmo.footswitch_timing as module
    summary=empty_catalog_summary(tmp_path)
    monkeypatch.setattr(module,'collect_saved_documents',lambda _:dict(held=[],documents=[]))
    module.run(summary,tmp_path/'result')
    report=json.loads((tmp_path/'result/diagnostic.json').read_text())
    assert report['status']=='held' and not report['curves']
    assert not report['scientific_approval'] and not report['heel_strike_alignment_verified']
    assert not report['amplitude_pooling_allowed']


def test_stale_document_binding_stops_execution(tmp_path, monkeypatch):
    import harmo.footswitch_timing as module
    summary=empty_catalog_summary(tmp_path)
    monkeypatch.setattr(module,'collect_saved_documents',lambda _:dict(held=['stale'],documents=[]))
    with pytest.raises(ValueError,match='fresh_source_bindings_required'):
        module.run(summary,tmp_path/'result')
    assert not (tmp_path/'result/diagnostic.json').exists()


def test_input_changed_during_run_cannot_publish_report(tmp_path, monkeypatch):
    import harmo.footswitch_timing as module
    summary=empty_catalog_summary(tmp_path)
    def changed(_):
        summary.write_text(summary.read_text()+'\n')
        return dict(held=[],documents=[])
    monkeypatch.setattr(module,'collect_saved_documents',changed)
    with pytest.raises(ValueError,match='inputs_changed_during_execution'):
        module.run(summary,tmp_path/'result')
    assert not (tmp_path/'result/diagnostic.json').exists()
