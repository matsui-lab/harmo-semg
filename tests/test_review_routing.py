from copy import deepcopy

import pytest

from harmo.review_routing import question, route_reason, route_work, RULES


def item(field='anatomy', values=None, sources=None):
    return dict(target=dict(field=field,target_id='r:ch:field'),
                rule=dict(values=['tibialis anterior'] if values is None else values),
                llm_candidate=None, status='needs_human_review',
                sources=[dict(source_id='header',text='TA'),dict(source_id='document-1',text='Definition')]
                if sources is None else sources)


@pytest.mark.parametrize('field', ['anatomy','measurement_side','task'])
def test_no_evidence_does_not_request_a_guess(field):
    row=item(field,sources=[])
    before=deepcopy(row)
    q=question(row)
    assert q['category']=='additional_evidence'
    assert not q['confirmation_allowed']
    assert q['required_evidence'] and row==before


@pytest.mark.parametrize('field', ['anatomy','task'])
def test_header_alone_requires_definition(field):
    q=question(item(field,sources=[dict(source_id='header',text='TA')]))
    assert q['category']=='additional_evidence' and not q['confirmation_allowed']


def test_side_still_needs_scope_confirmation_not_automatic_approval():
    q=question(item('measurement_side',values=['left'],sources=[dict(source_id='header',text='EmgSide: left')]))
    assert q['category']=='confirm_scope' and q['confirmation_allowed']
    assert q['automatic_resolution'] is False and q['evidence_entailment_verified'] is False


def test_available_sources_without_candidate_are_not_claimed_to_lack_information():
    q=question(item(values=[]))
    assert q['category']=='inspect_evidence' and q['confirmation_allowed']
    assert 'does not establish that the information is absent' in q['instruction']


def test_conflicting_candidates_keep_both_without_selection():
    row=item();row['llm_candidate']=dict(value='soleus')
    q=question(row)
    assert q['category']=='resolve_conflict'
    assert q['candidates']==['tibialis anterior','soleus']
    assert q['target_ids']==['r:ch:field']


@pytest.mark.parametrize('code', sorted(RULES))
def test_known_reasons_are_actionable_but_never_release_holds(code):
    r=route_reason(code)
    assert r['category']!='technical_investigation'
    assert r['next_action'] and r['reason']==code
    assert not r['automatic_resolution'] and not r['scientific_approval']


def test_unknown_and_combined_reasons_preserved():
    rows=[dict(reason='measurement_side_evidence,unseen-error',record_id='r',scope='ch')]
    result=route_work(rows)
    assert result['items'][0]['reason']==rows[0]['reason']
    assert result['counts']==dict(additional_evidence=1,technical_investigation=1)
    assert route_reason('existing_processing_representation_requires_other_recipe: envelope')['category']=='unsupported_processing'


def test_workspace_includes_guidance_without_changing_decisions(tmp_path):
    from test_semantic_workspace import fixture
    from harmo.semantic_workspace import prepare
    packet,_=fixture(tmp_path)
    result=prepare(packet,tmp_path/'workspace')
    assert result['review_routing']['counts']==dict(inspect_evidence=1)
    assert result['items'][0]['guidance']['target_ids']==[result['items'][0]['target']['target_id']]
    assert all(d['action']=='unreviewed' for d in result['decisions']['decisions'])
