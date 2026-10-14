import copy
import pytest
from harmo.llm_candidates import validate_candidates

TARGETS=[dict(target_id='x',field='measurement_side',canonical_choices=['left','right'])]
SOURCES=[dict(source_id='s',text='The measurement side is right.')]


def answer():
    return dict(candidates=[dict(target_id='x',status='proposed',value='right',alternatives=[],
        evidence=[dict(source_id='s',quote='measurement side is right')])])


def test_literal_evidence_never_becomes_approval():
    report=validate_candidates(answer(),TARGETS,SOURCES)
    assert report['structurally_complete'] and report['automatic_approvals']==0
    assert report['entries'][0]['status']=='requires_semantic_review'


@pytest.mark.parametrize('field,value,error',[
    ('target_id','other','unknown_target'),('status','approved','invalid_status'),
    ('value','RX','unsupported_canonical_value'),('evidence',[],'evidence_required'),
    ('evidence',[dict(source_id='s',quote='left')],'quote_not_found')])
def test_bad_candidates_rejected(field,value,error):
    response=answer(); response['candidates'][0][field]=value
    report=validate_candidates(response,TARGETS,SOURCES)
    assert not report['structurally_complete']
    assert error in report['entries'][0]['errors']


def test_duplicate_and_missing_targets_reported():
    response=answer(); response['candidates']*=2
    assert not validate_candidates(response,TARGETS,SOURCES)['structurally_complete']
    assert validate_candidates(dict(candidates=[]),TARGETS,SOURCES)['missing_targets']==['x']


def test_insufficient_is_valid_not_a_correctness_claim():
    report=validate_candidates(dict(candidates=[dict(target_id='x',status='insufficient',value=None,alternatives=[],evidence=[])]),TARGETS,SOURCES)
    assert report['structurally_complete'] and report['automatic_approvals']==0


def test_real_quote_does_not_establish_entailment():
    response=answer(); response['candidates'][0]['value']='left'
    report=validate_candidates(response,TARGETS,SOURCES)
    assert report['structurally_complete']
    assert report['entries'][0]['status']=='requires_semantic_review'
