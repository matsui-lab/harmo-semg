import json
import sqlite3
import pytest
from harmo import review_session as session, unit_review_store as store


@pytest.fixture
def packet(tmp_path,monkeypatch):
    catalog=tmp_path/'catalog.sqlite'
    monkeypatch.setattr(store,'fingerprint',lambda *a:('binding',{},None))
    monkeypatch.setattr(session,'fingerprint',lambda *a:('binding',{},None))
    store.append(catalog,'r',dict(record_hold=False,sample_sha256='sample',channels=[
        dict(channel_id='a',hold=False,decision=dict(status='file_supported')),
        dict(channel_id='b',hold=True,decision=dict(status='conflict'))]),'binding','candidate')
    out=tmp_path/'review';session.prepare(catalog,out)
    edits=json.loads((out/'decisions.json').read_text());edits['reviewer']='synthetic test reviewer'
    return catalog,out,edits


def submit(packet):
    catalog,out,edits=packet
    (out/'decisions.json').write_text(json.dumps(edits))
    return session.apply(catalog,out/'packet.json',out/'decisions.json')


def test_explicit_confirmation_and_hold_persist(packet):
    catalog,out,edits=packet
    edits['decisions'][0].update(action='confirm',reason='fixture evidence checked')
    edits['decisions'][1].update(action='hold',reason='fixture remains conflicting')
    assert submit(packet)['changed_records']==1
    review=store.load_review(catalog)
    assert review['records']['r']['channels'][0]['human_review']['action']=='confirm'
    with pytest.raises(ValueError):submit(packet)


def test_invalid_batch_leaves_no_partial_confirmation(packet):
    catalog,out,edits=packet
    for edit in edits['decisions']:edit.update(action='confirm',reason='fixture')
    with pytest.raises(ValueError,match='conflict_requires'):submit(packet)
    with sqlite3.connect(store.store_path(catalog)) as db:
        assert db.execute('SELECT COUNT(*) FROM events').fetchone()[0]==1


def test_packet_tampering_rejected(packet):
    catalog,out,edits=packet
    data=json.loads((out/'packet.json').read_text());data['event_head']=100
    (out/'packet.json').write_text(json.dumps(data))
    with pytest.raises(ValueError,match='identity'):submit(packet)


def test_changed_source_rejected(packet,monkeypatch):
    monkeypatch.setattr(store,'fingerprint',lambda *a:('new-binding',{},None))
    with pytest.raises(ValueError,match='stale'):submit(packet)


def test_duplicate_rejected(packet):
    packet[2]['decisions'].append(packet[2]['decisions'][0])
    with pytest.raises(ValueError,match='duplicate'):submit(packet)
