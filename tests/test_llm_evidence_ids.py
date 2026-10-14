import pytest
from harmo.llm_evidence_ids import expand, schema
from jsonschema import validate, ValidationError
from harmo.llm_candidates import validate_candidates

SOURCES=[dict(source_id='s',text='The side is right.')]
TARGET=dict(target_id='x')


def test_resolves_verbatim_without_approving_semantics():
    candidate=dict(status='proposed',value='left',alternatives=[],evidence_ids=['s'])
    response=expand(candidate,TARGET,SOURCES)
    assert response['candidates'][0]['evidence'][0]['quote']==SOURCES[0]['text']
    result=validate_candidates(response,[TARGET],SOURCES)
    assert result['structurally_complete'] and result['automatic_approvals']==0
    assert result['entries'][0]['status']=='requires_semantic_review'


@pytest.mark.parametrize('ids',[['missing'],['s','s'],[3]])
def test_invalid_ids_fail(ids):
    with pytest.raises(ValueError): expand(dict(status='proposed',value='right',alternatives=[],evidence_ids=ids),TARGET,SOURCES)


def test_proposed_alternatives_are_rejected_even_if_runtime_returns_them():
    candidate=dict(status='proposed',value='right',alternatives=['left'],evidence_ids=['s'])
    with pytest.raises(ValidationError):
        validate(candidate,schema(SOURCES))
    result=validate_candidates(expand(candidate,TARGET,SOURCES),[TARGET],SOURCES)
    assert not result['structurally_complete']
    assert result['automatic_approvals']==0


@pytest.mark.parametrize('candidate',[
    dict(status='proposed',value='right',alternatives=[],evidence_ids=['s']),
    dict(status='ambiguous',value=None,alternatives=['left','right'],evidence_ids=['s']),
    dict(status='insufficient',value=None,alternatives=[],evidence_ids=[]),
])
def test_schema_accepts_intended_statuses(candidate):
    validate(candidate,schema(SOURCES))
