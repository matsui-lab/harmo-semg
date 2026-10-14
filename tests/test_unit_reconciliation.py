import pytest
from harmo.unit_reconciliation import canonical_unit,reconcile_units,verify_wfdb_unit,QUESTIONS
import hashlib


def claim(source,value,**extra):
    return dict(target_id='channel-representation',field='physical_unit',source_id=source,
                value=value,status='proposed',structurally_valid=True,**extra)


def result(a,b):return reconcile_units([claim('doc',a),claim('header',b)],'channel-representation',['doc','header'])


def test_scale_disagreement_holds_without_conversion():
    r=result('µV','mV')
    assert r['status']=='conflict' and r['hold'] and r['candidate_unit'] is None
    assert not r['conversion_performed'] and not r['allow_amplitude_integration']


@pytest.mark.parametrize('a,b',[('µV','μV'),('uV','microvolts'),('mV','mV')])
def test_agreement_does_not_trigger_conflict_or_automatic_approval(a,b):
    r=result(a,b)
    assert r['status']=='consistent' and not r['hold']
    assert r['automatic_approvals']==0 and r['requires_semantic_review']


def test_absent_source_is_not_agreement():
    r=reconcile_units([claim('doc','mV')],'channel-representation',['doc','header'])
    assert r['hold'] and r['status']=='insufficient'


@pytest.mark.parametrize('value',['MV','10000/mV','%MVC',None])
def test_unknown_or_nonunit_values_hold(value):
    assert result(value,'mV')['hold']


def test_scope_mismatch_is_rejected():
    with pytest.raises(ValueError,match='target_scope'):
        reconcile_units([claim('doc','mV')],'different-channel',['doc'])


def test_calibration_cannot_be_reconciled_as_physiological_normalization():
    c=claim('doc','mV');c['field']='digital_to_physical_calibration'
    with pytest.raises(ValueError,match='wrong_field'):
        reconcile_units([c],'channel-representation',['doc'])
    assert len(QUESTIONS)==3


@pytest.fixture
def header_proof(tmp_path):
    path=tmp_path/'record.hea'
    path.write_text('record 1 1000 100\nrecord.dat 16 10000/mV 16 0 0 0 0 EMG\n')
    digest=hashlib.sha256(path.read_bytes()).hexdigest()
    return verify_wfdb_unit(path,digest,0,'EMG','channel-representation')


def test_header_can_support_candidate_when_prose_has_no_declaration(header_proof):
    doc=claim('doc',None);doc['status']='insufficient'
    r=reconcile_units([doc,claim('header','mV')],'channel-representation',['doc','header'],file_unit_evidence=header_proof)
    assert r['status']=='file_supported' and r['candidate_unit']=='mV' and not r['hold']
    assert r['requires_semantic_review'] and not r['allow_amplitude_integration']


def test_header_never_overrides_contradictory_description(header_proof):
    r=reconcile_units([claim('doc','µV'),claim('header','mV')],'channel-representation',['doc','header'],file_unit_evidence=header_proof)
    assert r['status']=='conflict' and r['hold']


@pytest.mark.parametrize('status',['ambiguous','error'])
def test_uncertainty_is_not_treated_as_silence(header_proof,status):
    doc=claim('doc',None);doc['status']=status
    r=reconcile_units([doc,claim('header','mV')],'channel-representation',['doc','header'],file_unit_evidence=header_proof)
    assert r['hold'] and r['status']=='insufficient'


def test_unexamined_document_still_holds(header_proof):
    r=reconcile_units([claim('header','mV')],'channel-representation',['doc','header'],file_unit_evidence=header_proof)
    assert r['hold']


@pytest.mark.parametrize('defect',['hash','channel','implicit_unit'])
def test_header_proof_rejects_unverified_or_implicit_units(tmp_path,defect):
    path=tmp_path/'record.hea'
    gain='10000' if defect=='implicit_unit' else '10000/mV'
    path.write_text(f'record 1 1000 100\nrecord.dat 16 {gain} 16 0 0 0 0 EMG\n')
    digest=hashlib.sha256(path.read_bytes()).hexdigest()
    with pytest.raises(ValueError):
        verify_wfdb_unit(path,'0'*64 if defect=='hash' else digest,0,'other' if defect=='channel' else 'EMG','t')
