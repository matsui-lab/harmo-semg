import json
from copy import deepcopy

import pytest

from test_general_workflow import fixture
from harmo.semantic_review import prepare as packet_prepare
from harmo.semantic_workspace import prepare as workspace_prepare
from harmo.semantic_server import ReviewSession
from harmo.walking_plan import sha


def session(tmp_path, maximum=8):
    summary = fixture(tmp_path/'source-fixture')
    packet_prepare(summary, tmp_path/'packet', max_channels=2)
    workspace_prepare(tmp_path/'packet/packet.json', tmp_path/'workspace', max_targets=maximum)
    return ReviewSession(tmp_path/'workspace/workspace.json', tmp_path/'runs')


def test_real_processing_from_unreviewed_snapshot_is_not_human_confirmation(tmp_path):
    s = session(tmp_path)
    catalog = tmp_path/'source-fixture/catalog.sqlite'
    checksum = sha(catalog)
    review = deepcopy(s.data['decisions'])
    evaluated = s.evaluate(review)
    assert evaluated['ready_records'] == 2
    assert evaluated['review_metrics']['reviewed_items'] == 0
    assert {p['purpose'] for p in evaluated['purposes']} == {'activation-timing', 'amplitude-pooling'}
    assert not (s.runs[evaluated['run_id']]['folder']/'processing').exists()
    result = s.execute(evaluated['run_id'])
    assert result['curves'] == 2 and result['comparisons'] == 1
    assert result['scientific_approval'] is False
    assert result['amplitude_pooling_allowed'] is False
    assert sha(catalog) == checksum
    assert s.execute(evaluated['run_id']) == result


def test_human_hold_reaches_processing(tmp_path):
    s = session(tmp_path)
    decisions = deepcopy(s.data['decisions'])
    decisions['reviewer'] = 'Synthetic test reviewer'
    target = next(i['target']['target_id'] for i in s.data['items'] if i['target']['field']=='anatomy')
    row = next(d for d in decisions['decisions'] if d['target_id']==target)
    row.update(action='hold', scope_reason='Synthetic test: do not interpret this muscle')
    result = s.evaluate(decisions)
    assert result['review_metrics']['reviewed_items'] == 1
    assert any(r['before'] != r['after'] or r['channels_before'] != r['channels_after'] for r in result['records'])
    executed = s.execute(result['run_id'])
    assert executed['curves'] < 2


def test_hidden_or_removed_decisions_rejected(tmp_path):
    s = session(tmp_path, maximum=1)
    decisions = deepcopy(s.data['decisions'])
    decisions['decisions'].pop()
    with pytest.raises(ValueError, match='all_original'):
        s.evaluate(decisions)
    decisions = deepcopy(s.data['decisions'])
    decisions['decisions'][-1]['action'] = 'hold'
    with pytest.raises(ValueError, match='non_displayed'):
        s.evaluate(decisions)


def test_changed_plan_and_source_fail_closed(tmp_path):
    s = session(tmp_path)
    result = s.evaluate(deepcopy(s.data['decisions']))
    folder = s.runs[result['run_id']]['folder']
    plan = folder/'plan.json'
    plan.write_text(plan.read_text()+' ')
    with pytest.raises(ValueError, match='saved_plan_changed'):
        s.execute(result['run_id'])
    s.workspace.write_text(s.workspace.read_text()+' ')
    with pytest.raises(ValueError, match='workspace_changed'):
        s.evaluate(deepcopy(s.data['decisions']))


def test_no_unknown_run_or_existing_output(tmp_path):
    s = session(tmp_path)
    with pytest.raises(ValueError, match='evaluate_in_this_session'):
        s.execute('../outside')
    with pytest.raises(FileExistsError):
        ReviewSession(s.workspace, s.root)


def test_offline_refuses_missing_related_article(tmp_path, monkeypatch):
    from harmo.automatic_workflow import collect_saved_documents
    from harmo import related_documents
    stage = tmp_path/'stage'; stage.mkdir()
    (stage/'repository.metadata.json').write_text('{}')
    monkeypatch.setattr(related_documents, 'related_dois', lambda value: [dict(doi='10.1234/example')])
    monkeypatch.setattr(related_documents, 'retrieve', lambda *a, **k: pytest.fail('network must not be reached'))
    # Stub downstream joins to isolate the missing-document acquisition guard.
    monkeypatch.setattr('harmo.repository_context.join_documents', lambda docs, entries: docs)
    result = collect_saved_documents(dict(entries=[dict(source_report=str(stage),dataset='synthetic')]), allow_retrieve=False)
    assert any('saved_related_document_required_offline' in h['reason'] for h in result['held'])
