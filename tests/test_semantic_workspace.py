import hashlib
import json

import pytest

from harmo.llm_candidates import digest
from harmo.semantic_review import checked_packet, propose, reviewed_documents
from harmo.semantic_workspace import prepare, evidence_preview


def fixture(tmp_path, count=1):
    source = tmp_path / 'source.txt'
    source.write_text('Original evidence. </script><script>alert(1)</script>')
    summary = tmp_path / 'summary.json'
    summary.write_text('{}')
    sha = hashlib.sha256(source.read_bytes()).hexdigest()
    sources = [dict(source_id='p'+str(i), path=str(source), source_sha256=sha,
                    pointer='paragraph/'+str(i), text=source.read_text()+str(i), kind='text') for i in range(5)]
    targets = [dict(target_id='r:ch-%d:anatomy'%i,record_id='r',record='trial.hea',
                    channel='TA',channel_id='ch-%d'%i,field='anatomy') for i in range(count)]
    packet = dict(version='semantic-review/1',summary_path=str(summary),
                  summary_sha256=hashlib.sha256(summary.read_bytes()).hexdigest(),
                  bindings=[dict(path=str(source),sha256=sha)],
                  cases=[dict(case_id='r',dataset='synthetic',record='trial.hea',sources=sources,
                              targets=targets,rules=[])])
    packet['packet_id']=digest(packet)
    path=tmp_path/'packet.json';path.write_text(json.dumps(packet))
    return path,source


def transport(requests, mode='expanded'):
    def call(name,payload=None):
        if name=='show':return {}
        if name=='tags':return dict(models=[dict(name='local',digest='synthetic-test')])
        if name=='version':return dict(version='synthetic-test')
        query=json.loads(payload['messages'][1]['content']);requests.append(query)
        ok=mode=='proposed' or len(query['sources'])>3
        value=dict(status='proposed' if ok else 'insufficient',value='tibialis-anterior' if ok else None,
                   alternatives=[],evidence_ids=[query['sources'][0]['source_id']] if ok else [])
        if mode=='invalid':value['evidence_ids']=['invented']
        return dict(done=True,done_reason='stop',message=dict(content=json.dumps(value)))
    return call


def test_adaptive_expansion_is_bounded_and_not_approval(tmp_path):
    path,_=fixture(tmp_path);requests=[]
    result=prepare(path,tmp_path/'out',model='local',transport=transport(requests))
    assert [len(r['sources']) for r in requests]==[3,5]
    assert result['items'][0]['llm_candidate']['value']=='tibialis-anterior'
    assert result['automatic_approvals']==0 and result['scientific_approval'] is False
    assert all(d['action']=='unreviewed' for d in result['decisions']['decisions'])
    assert result['accuracy_evaluated'] is False


def test_budget_and_metrics_keep_attempted_unresolved_target(tmp_path):
    path,_=fixture(tmp_path,2);requests=[]
    result=prepare(path,tmp_path/'out','local',max_calls=1,transport=transport(requests))
    assert len(requests)==1 and result['call_attempts']==1
    candidates=json.loads((tmp_path/'out/candidates.json').read_text())
    assert len(candidates['rows'])==1
    assert result['items'][1]['stop_reason']=='call_budget_exhausted'


def test_rule_conflict_is_not_resolved_by_llm(tmp_path):
    path,_=fixture(tmp_path);p=json.loads(path.read_text());p.pop('packet_id')
    p['cases'][0]['rules']=[dict(target_id='r:ch-0:anatomy',values=['soleus'],status='proposed')]
    p['packet_id']=digest(p);path.write_text(json.dumps(p))
    result=prepare(path,tmp_path/'out','local',transport=transport([],mode='proposed'))
    assert result['items'][0]['status']=='rule_llm_conflict'
    assert result['items'][0]['rule']['values']==['soleus']


def test_explicit_hold_never_queries_model(tmp_path):
    path,_=fixture(tmp_path);packet=checked_packet(path)
    d=dict(packet_id=packet['packet_id'],reviewer='Synthetic test reviewer',active_seconds=None,
           decisions=[dict(target_id='r:ch-0:anatomy',action='hold',value=None,evidence_ids=[],scope_reason='synthetic conflict')])
    edits=tmp_path/'decisions.json';edits.write_text(json.dumps(d))
    result=prepare(path,tmp_path/'out','local',decisions_path=edits,transport=lambda *a:pytest.fail('model called'))
    assert not result['items'] and result['retained_human_decisions']==1
    assert result['decisions']['decisions'][0]['action']=='hold'


def test_unavailable_runtime_keeps_review_usable(tmp_path):
    path,_=fixture(tmp_path)
    def unavailable(*args):raise OSError('synthetic offline')
    result=prepare(path,tmp_path/'out','local',transport=unavailable)
    assert result['items'][0]['stop_reason']=='local_model_failed'
    assert (tmp_path/'out/review.html').exists()


def test_ui_escapes_source_markup_and_keeps_full_unreviewed_set(tmp_path):
    path,_=fixture(tmp_path,3)
    result=prepare(path,tmp_path/'out',max_targets=1)
    html=(tmp_path/'out/review.html').read_text()
    assert '</script><script>alert(1)</script>' not in html
    assert '\\u003c/script\\u003e' in html
    assert result['omitted_pending_items']==2
    assert len(result['decisions']['decisions'])==3
    assert '/*WORKSPACE_DATA*/null' not in html


def test_preview_retains_original_source_and_is_not_scope_proof():
    source = dict(text='<p>Measured Tibialis Anterior &amp; Soleus.</p>')
    preview = evidence_preview(source, dict(field='anatomy', channel='Tibialis Anterior'), [])
    assert preview['text'] == ' Measured Tibialis Anterior & Soleus. '
    assert preview['matched_term'] == 'Tibialis Anterior'
    assert 'not verified' in preview['note']
    assert source['text'].startswith('<p>')


def test_side_preview_prefers_emg_definition_not_foot():
    source = dict(text='Foot is right. ' + 'Other context. '*200 + 'EmgSide defines the recorded leg.')
    preview = evidence_preview(source, dict(field='measurement_side'), ['right'])
    assert preview['matched_term'] == 'EmgSide'
    assert 'EmgSide defines the recorded leg.' in preview['text']


def test_header_preview_displays_exact_channel_index():
    source = dict(kind='derived-record-schema', text=json.dumps(dict(channels=['TA', 'SOL'])))
    preview = evidence_preview(source, dict(field='anatomy', channel='SOL'), ['soleus'])
    assert preview['text'] == 'channels[1]: "SOL"'
    assert 'not independently verified' in preview['note']


def test_selected_sources_and_targets_validated_before_network(tmp_path):
    path,_=fixture(tmp_path)
    with pytest.raises(ValueError,match='unknown_selected_source'):
        propose(path,tmp_path/'bad','local',target_ids=['r:ch-0:anatomy'],
                source_ids_by_target={'r:ch-0:anatomy':['invented']},transport=lambda *a:pytest.fail('network'))


def test_changed_packet_during_inference_rejected(tmp_path):
    path,source=fixture(tmp_path);base=transport([],mode='proposed')
    def changing(name,payload=None):
        if name=='chat':source.write_text('changed')
        return base(name,payload)
    with pytest.raises(ValueError,match='bound_source_changed'):
        prepare(path,tmp_path/'out','local',transport=changing)
    assert not (tmp_path/'out/workspace.json').exists()


def test_manual_export_uses_existing_review_gate(tmp_path):
    path,_=fixture(tmp_path);result=prepare(path,tmp_path/'out')
    edits=result['decisions'];edits['reviewer']='Synthetic test reviewer'
    edits['decisions'][0].update(action='confirm',value='tibialis-anterior',evidence_ids=['p0'],scope_reason='Synthetic scoped confirmation')
    file=tmp_path/'decisions.json';file.write_text(json.dumps(edits))
    docs,metrics=reviewed_documents(path,file)
    assert metrics['reviewed_items']==1 and metrics['scientific_approvals']==0
    assert docs[0]['result']['observations'][0]['channel']=='TA'


@pytest.mark.parametrize('budget',[0,-1,257,True])
def test_invalid_target_budget(tmp_path,budget):
    with pytest.raises(ValueError,match='target_budget'):
        prepare('not-read',tmp_path/'out',max_targets=budget)
