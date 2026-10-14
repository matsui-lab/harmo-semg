import sqlite3
from copy import deepcopy
import pytest

from test_semantic_collection import executed
from harmo.semantic_collection import Collection


def test_records_derivatives_and_pair_purposes_are_separate(tmp_path):
    s, run_id = executed(tmp_path)
    reg = s.register(run_id)
    library = s.collection.library()
    assert len(library['common_records']) == 2
    assert len(library['derived_signals']) == 2
    assert library['use_assessments']
    assert all('purpose' not in d and not d['scientific_approval'] for d in library['derived_signals'])
    for a in library['use_assessments']:
        assert a['purpose'] in ('activation-timing', 'amplitude-pooling')
        assert a['left_channel_id'] and a['right_channel_id']
        assert a['assessment_scope']=='source-contract-pair'
        assert a['derived_signal_approval'] is False
        assert a['revision_ids']==[reg['id']]
    assert s.collection.library('waveform-shape')['use_assessments']==[]
    assert len(s.collection.library('waveform-shape')['derived_signals'])==2
    assert all(a['purpose']=='activation-timing' for a in s.collection.library('activation-timing')['use_assessments'])
    with pytest.raises(ValueError, match='unsupported_purpose'):
        s.collection.library('unknown')


def test_legacy_backfill_keeps_snapshots_objects_and_ids(tmp_path):
    s, run_id = executed(tmp_path)
    first = s.register(run_id)
    expected = s.collection.get(first['id'])
    with sqlite3.connect(s.collection.path) as db:
        count = db.execute('SELECT COUNT(*) FROM objects').fetchone()[0]
        # Simulate the legacy schema using only this test's temporary database.
        for table in ('common_records','derived_signals','use_assessments','entity_revisions','indexed_revisions'):
            db.execute('DROP TABLE '+table)
    restored = Collection(s.collection.root)
    assert restored.get(first['id'])==expected
    assert len(restored.library()['common_records'])==2
    assert (restored.root/'collection.pre-entity-index-v001.sqlite').exists()
    with sqlite3.connect(restored.path) as db:
        assert db.execute('SELECT COUNT(*) FROM objects').fetchone()[0]==count
    # Reopening is idempotent, not another registration/version.
    assert len(Collection(restored.root).list())==1


def test_reused_records_and_version_bound_decisions(tmp_path):
    s, run_id = executed(tmp_path)
    first = s.register(run_id)
    original = s.collection.library()
    decisions = deepcopy(s.data['decisions'])
    decisions['reviewer']='Synthetic test reviewer'
    target = next(i['target']['target_id'] for i in s.data['items'] if i['target']['field']=='anatomy')
    row = next(d for d in decisions['decisions'] if d['target_id']==target)
    row.update(action='hold',scope_reason='Synthetic test: do not reuse the muscle mapping')
    next_run=s.evaluate(decisions);s.execute(next_run['run_id'])
    second=s.register(next_run['run_id'])
    library=s.collection.library()
    assert second['version']==2
    assert {r['id'] for r in library['common_records']}=={r['id'] for r in original['common_records']}
    assert library['revisions'][0]['id']==second['id']
    assert all(first['id'] not in a['revision_ids'] for a in library['use_assessments'])
    assert s.collection.get(first['id'])['result']['review_metrics']['reviewed_items']==0


def test_index_corruption_is_not_silently_served(tmp_path):
    s, run_id=executed(tmp_path);s.register(run_id)
    with sqlite3.connect(s.collection.path) as db:
        db.execute("DELETE FROM entity_revisions WHERE rowid=(SELECT MIN(rowid) FROM entity_revisions)")
    with pytest.raises(ValueError,match='indexed_links_changed'):
        s.collection.library()
