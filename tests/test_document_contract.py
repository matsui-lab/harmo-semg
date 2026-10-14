import json
import pytest
from harmo.document_contract import resolve
from harmo.document_evidence import extract
from test_semantic_promotion import fixture


def observations(**updates):
    row=dict(record='x.hea',channel='TA',muscle='tibialis anterior',side='left',sensor_type='surface emg',task='walking',unit='V')
    row.update(updates)
    return extract(json.dumps([row]).encode(),'https://example.org/source','json')['observations']


def test_source_facts_fill_missing_attributes_preserving_samples():
    bundle,_=fixture(); before=bundle.to_dict()
    result,report=resolve(bundle,'x.hea',observations())
    assert result.channels[0].side=='left'
    assert result.channels[0].sensor_type=='surface-emg'
    assert result.channels[0].anatomy.canonical_label=='tibialis anterior'
    assert result.channels[0].anatomy.laterality=='left'
    assert result.task.activity=='walking' and result.task.compatibility_group is None
    assert result.samples.to_dict()==bundle.samples.to_dict()
    assert bundle.to_dict()==before
    assert report['scientific_use']=='unassessed'
    assert sum(d['status']=='filled' for d in report['decisions'])==4
    again,repeated=resolve(result,'x.hea',observations())
    assert again.to_dict()==result.to_dict() and not repeated['changed']


def test_no_scope_match_no_changes():
    bundle,_=fixture()
    result,report=resolve(bundle,'other',observations())
    assert result.to_dict()==bundle.to_dict() and not report['changed']


def test_prose_protocol_context_is_valid_and_does_not_create_equivalence():
    bundle,_=fixture()
    facts=observations()
    for fact in facts:
        if fact['field']=='task':fact['protocol_context']='walking while performing serial subtractions'
    result,_=resolve(bundle,'x.hea',facts)
    assert result.task.facets['source_protocol_context']=='walking while performing serial subtractions'
    assert result.task.compatibility_group is None


def test_ambiguous_abbreviation_not_promoted():
    bundle,_=fixture()
    result,report=resolve(bundle,'x.hea',observations(muscle='TA'))
    assert result.channels[0].anatomy is None
    assert any(d.get('reason')=='full_name_not_in_versioned_vocabulary' for d in report['decisions'])


def test_conflict_does_not_fill_laterality():
    bundle,_=fixture()
    result,report=resolve(bundle,'x.hea',observations()+observations(side='right'))
    assert result.channels[0].side is None
    assert result.channels[0].anatomy.laterality=='unknown'
    assert any(d['status']=='conflicting' for d in report['decisions'])


def test_unit_scale_is_never_reinterpreted():
    bundle,_=fixture()
    result,report=resolve(bundle,'x.hea',observations(unit='mV'))
    assert result.channels[0].unit=='V'
    assert any(d['field']=='unit' and d['status']=='conflicting' for d in report['decisions'])


def test_group_is_not_expanded_into_muscle():
    bundle,_=fixture()
    result,_=resolve(bundle,'x.hea',observations(muscle='hamstrings'))
    assert result.channels[0].anatomy.entity_type=='muscle-group'
    assert result.channels[0].muscle is None


def test_automatic_view_keeps_original_and_trust(tmp_path):
    from test_public_export import setup_catalog
    from harmo.automatic_workflow import assess_catalog
    setup_catalog(tmp_path,trusted=False)
    path=tmp_path/'1/recording.bundle.json'
    data=json.loads(path.read_text())
    data['task']=None
    for c in data['channels']:
        c['anatomy']=None; c['muscle']=None; c['side']=None; c['sensor_type']='unknown'
    path.write_text(json.dumps(data))
    before=path.read_bytes(); trust=(tmp_path/'1/trust.report.json').read_bytes()
    report=assess_catalog(tmp_path/'catalog.sqlite',tmp_path/'out',['amplitude-pooling'],
        documents=[dict(dataset='test/1.0.0',result=dict(observations=observations(record='1')))])
    item=report['records'][0]
    assert item['attribute_resolution']['changed']
    assert item['attribute_resolution']['output_path']
    assert report['exported']==0
    assert path.read_bytes()==before and (tmp_path/'1/trust.report.json').read_bytes()==trust
