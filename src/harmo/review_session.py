"""Bounded, transactional unit-review sessions followed by purpose reassessment."""
import hashlib
import json
import sqlite3
from datetime import datetime, timezone
from pathlib import Path
from harmo.unit_review_store import store_path, fingerprint, load_review


def _digest(value):
    return hashlib.sha256(json.dumps(value,sort_keys=True).encode()).hexdigest()


def _head(db):
    return db.execute('SELECT COALESCE(MAX(id),0) FROM events').fetchone()[0]


def prepare(catalog, output):
    """Create a readable evidence packet and an unsubmitted decision template."""
    review=load_review(catalog)
    if review is None:
        raise ValueError('persisted_unit_review_required')
    with sqlite3.connect(store_path(catalog).as_uri()+'?mode=ro',uri=True) as db:
        head=_head(db)
    # Re-read after obtaining the event token: a concurrently changing store
    # cannot be represented as an apparently stable review session.
    again=load_review(catalog)
    with sqlite3.connect(store_path(catalog).as_uri()+'?mode=ro',uri=True) as db:
        if head!=_head(db) or review!=again:
            raise ValueError('review_changed_during_preparation')
    packet=dict(version='unit-review-session/1',catalog=str(Path(catalog).resolve()),
                event_head=head,review_sha256=_digest(review),review=review)
    packet['session_id']=_digest(packet)
    edits=dict(session_id=packet['session_id'],reviewer='',decisions=[
        dict(record_id=rid,channel_id=c['channel_id'],action='skip',reason='')
        for rid,item in review['records'].items() for c in item['channels']])
    out=Path(output);out.mkdir(parents=True,exist_ok=False)
    (out/'packet.json').write_text(json.dumps(packet,indent=2))
    (out/'decisions.json').write_text(json.dumps(edits,indent=2))
    lines=['# Unit review session','',
           'Edit decisions.json: skip, confirm, or hold. Confirmation does not authorize scientific reuse.',
           'Conflicting units require a new evidence review; confirmation cannot override them.','']
    for rid,item in review['records'].items():
        lines += ['## Record '+rid,'', 'Record hold: '+str(item.get('record_hold')), '']
        for c in item['channels']:
            lines += ['### '+c.get('channel_name',c['channel_id']),'',
                      'Channel: '+c['channel_id']+'; hold: '+str(c.get('hold')),'',
                      '```json',json.dumps(c,indent=2,ensure_ascii=False),'```','']
    (out/'review.md').write_text('\n'.join(lines))
    return dict(session_id=packet['session_id'],channels=len(edits['decisions']),output=str(out))


def apply(catalog, packet_path, decisions_path):
    packet=json.loads(Path(packet_path).read_text())
    edits=json.loads(Path(decisions_path).read_text())
    session=packet.pop('session_id')
    if (_digest(packet)!=session or edits.get('session_id')!=session
        or packet['catalog']!=str(Path(catalog).resolve())):
        raise ValueError('session_identity_mismatch')
    reviewer=edits.get('reviewer')
    if not isinstance(reviewer,str) or not reviewer.strip():
        raise ValueError('reviewer_required')
    current=load_review(catalog)
    if _digest(current)!=packet['review_sha256']:
        raise ValueError('stale_review_session')
    items=json.loads(json.dumps(current['records']))
    changed={};seen=set()
    for edit in edits['decisions']:
        rid,cid=edit['record_id'],edit['channel_id']
        if (rid,cid) in seen:raise ValueError('duplicate_channel_decision')
        seen.add((rid,cid))
        item=items.get(rid,{})
        channels=[c for c in item.get('channels',[]) if c['channel_id']==cid]
        if len(channels)!=1:raise ValueError('unknown_review_channel')
        action=edit['action']
        if action not in ('skip','confirm','hold'):raise ValueError('unsupported_review_action')
        if action=='skip':continue
        if item.get('record_hold'):raise ValueError('fresh_bound_review_required')
        reason=edit.get('reason')
        if not isinstance(reason,str) or not reason.strip():raise ValueError('review_reason_required')
        c=channels[0]
        if action=='confirm' and (c.get('hold') or c.get('decision',{}).get('status') not in ('file_supported','consistent')):
            raise ValueError('conflict_requires_new_evidence_not_confirmation')
        c.update(hold=action=='hold',human_review=dict(action=action,reviewer=reviewer,reason=reason,session_id=session))
        changed[rid]=fingerprint(catalog,rid)[0]
    with sqlite3.connect(store_path(catalog)) as db:
        db.execute('BEGIN IMMEDIATE')
        if _head(db)!=packet['event_head']:raise ValueError('review_events_changed')
        if _digest(load_review(catalog))!=packet['review_sha256']:
            raise ValueError('source_changed_during_review')
        for rid,binding in changed.items():
            if fingerprint(catalog,rid)[0]!=binding:raise ValueError('source_changed_during_review')
            db.execute('INSERT INTO events(record_id,binding,kind,created,payload) VALUES(?,?,?,?,?)',
                (rid,binding,'review-session',datetime.now(timezone.utc).isoformat(),json.dumps(items[rid])))
    return dict(session_id=session,changed_records=len(changed),scientific_approval=False)


def reassess(catalog, output, purposes, max_comparisons=1000):
    from harmo.automatic_workflow import assess_catalog
    result=assess_catalog(catalog,output,purposes,max_comparisons)
    return dict(exported=result['exported'],status=result['status'],
                boundary='Existing gates retained; no processing-profile inference or trust promotion.')
