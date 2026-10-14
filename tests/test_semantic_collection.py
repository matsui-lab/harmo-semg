from copy import deepcopy
import json
import sqlite3

import pytest

from test_semantic_server import session
from harmo.semantic_collection import Collection
from harmo.walking_plan import sha


def executed(tmp_path):
    s = session(tmp_path)
    evaluation = s.evaluate(deepcopy(s.data['decisions']))
    s.execute(evaluation['run_id'])
    return s, evaluation['run_id']


def test_register_duplicate_and_restart_without_sources(tmp_path):
    s, run_id = executed(tmp_path)
    saved = s.register(run_id)
    assert saved['version'] == 1 and not saved['reused']
    assert s.register(run_id)['reused']
    assert len(s.collection.list()) == 1
    folder = s.runs[run_id]['folder']
    expected = json.loads((folder/'execution.json').read_text())
    expected_csv = (folder/'processing/curves.csv').read_bytes()
    folder.rename(folder.with_name('original-run-moved'))
    s.workspace.rename(s.workspace.with_suffix('.moved'))
    restarted = Collection(s.collection.root)
    assert restarted.get(saved['id'])['result'] == expected
    assert restarted.artifact(saved['id'], 'curves.csv') == expected_csv
    assert restarted.get(saved['id'])['result']['scientific_approval'] is False
    assert restarted.get(saved['id'])['result']['review_metrics']['reviewed_items'] == 0


def test_new_identical_execution_reuses_and_changed_decision_versions(tmp_path):
    s, run_id = executed(tmp_path)
    first = s.register(run_id)
    second = s.evaluate(deepcopy(s.data['decisions']))
    s.execute(second['run_id'])
    assert s.register(second['run_id'])['id'] == first['id']
    decisions = deepcopy(s.data['decisions'])
    decisions['reviewer'] = 'Synthetic test reviewer'
    target = next(i['target']['target_id'] for i in s.data['items'] if i['target']['field']=='anatomy')
    row = next(d for d in decisions['decisions'] if d['target_id']==target)
    row.update(action='hold', scope_reason='Synthetic test: retain uncertainty')
    third = s.evaluate(decisions)
    s.execute(third['run_id'])
    revised = s.register(third['run_id'])
    assert revised['version'] == 2 and revised['family'] == first['family']
    assert revised['id'] != first['id']
    assert s.collection.get(first['id'])['result']['review_metrics']['reviewed_items'] == 0
    assert s.collection.get(revised['id'])['result']['review_metrics']['reviewed_items'] == 1


def test_tampering_fails_closed_and_transaction_is_atomic(tmp_path, monkeypatch):
    s, run_id = executed(tmp_path)
    original = s.collection._put
    count = 0
    def fail(db, checksum, content):
        nonlocal count
        count += 1
        original(db, checksum, content)
        if count == 2:
            raise ValueError('synthetic_disk_failure')
    monkeypatch.setattr(s.collection, '_put', fail)
    with pytest.raises(ValueError, match='synthetic_disk_failure'):
        s.register(run_id)
    assert not s.collection.list()
    with sqlite3.connect(s.collection.path) as db:
        assert db.execute('SELECT COUNT(*) FROM objects').fetchone()[0] == 0
    monkeypatch.setattr(s.collection, '_put', original)
    saved = s.register(run_id)
    with sqlite3.connect(s.collection.path) as db:
        db.execute("UPDATE objects SET bytes=? WHERE hash=(SELECT hash FROM objects LIMIT 1)", (b'changed',))
    with pytest.raises(ValueError, match='stored_object_changed'):
        s.collection.get(saved['id'])
    with pytest.raises(ValueError, match='stored_object_changed'):
        s.register(run_id)


def test_unexecuted_changed_artifact_and_paths_rejected(tmp_path):
    s = session(tmp_path)
    e = s.evaluate(deepcopy(s.data['decisions']))
    with pytest.raises(ValueError, match='execute_in_this_session'):
        s.register(e['run_id'])
    s.execute(e['run_id'])
    path = s.runs[e['run_id']]['folder']/'execution.json'
    path.write_text(path.read_text()+' ')
    with pytest.raises(ValueError, match='bound_input_changed'):
        s.register(e['run_id'])
    assert not s.collection.list()
    with pytest.raises(ValueError, match='invalid_registration_id'):
        s.collection.get('../outside')
    with pytest.raises(ValueError, match='artifact_not_allowed'):
        s.collection.artifact('a'*64, '../plan.json')
