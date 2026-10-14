"""Append-only local unit decisions, bound to source and converted content.

These decisions never approve scientific reuse or modify physical samples.
"""
import hashlib
import json
from pathlib import Path
import sqlite3
from datetime import datetime, timezone


def store_path(catalog):
    return Path(str(Path(catalog).resolve()) + '.unit-review.sqlite')


def fingerprint(catalog, record_id):
    from harmo.public_export import load_record, checked_samples
    with sqlite3.connect(Path(catalog).resolve().as_uri()+'?mode=ro', uri=True) as db:
        row = db.execute('SELECT id,dataset,name,location,metadata FROM records WHERE id=?', (record_id,)).fetchone()
    if row is None:
        raise ValueError('record_missing')
    bundle, _, directory = load_record(row[:4])
    checked_samples(bundle, directory)
    name = Path(row[2])
    if name.name != row[2] or name.suffix != '.hea':
        raise ValueError('WFDB_binding_required')
    stage = directory.parent.parent
    header = stage/'raw'/name
    header_sha = hashlib.sha256(header.read_bytes()).hexdigest()
    if json.loads(row[4]).get('source_sha256', {}).get(name.name) != header_sha:
        raise ValueError('header_changed')
    description_sha = hashlib.sha256((stage/'description.html').read_bytes()).hexdigest()
    # Path-independent contract binding, including channel identity and units.
    binding = dict(dataset=row[1], name=row[2], sample=bundle.samples.checksum,
                   channels=[c.to_dict() for c in bundle.channels],
                   header=header_sha, description=description_sha)
    digest = hashlib.sha256(json.dumps(binding, sort_keys=True).encode()).hexdigest()
    return digest, binding, bundle


def append(catalog, record_id, item, binding, kind):
    path = store_path(catalog)
    with sqlite3.connect(path) as db:
        db.execute('CREATE TABLE IF NOT EXISTS events (id INTEGER PRIMARY KEY, record_id TEXT NOT NULL, binding TEXT NOT NULL, kind TEXT NOT NULL, created TEXT NOT NULL, payload TEXT NOT NULL)')
        db.execute('INSERT INTO events(record_id,binding,kind,created,payload) VALUES(?,?,?,?,?)',
                   (record_id, binding, kind, datetime.now(timezone.utc).isoformat(), json.dumps(item)))


def save_review(catalog, review):
    """Import a run queue only when its evidence still matches the catalog."""
    count = 0
    previous = load_review(catalog) or dict(records={})
    # An enabled but empty queue must still enforce missing-review holds.
    with sqlite3.connect(store_path(catalog)) as db:
        db.execute('CREATE TABLE IF NOT EXISTS events (id INTEGER PRIMARY KEY, record_id TEXT NOT NULL, binding TEXT NOT NULL, kind TEXT NOT NULL, created TEXT NOT NULL, payload TEXT NOT NULL)')
    for rid, item in review['records'].items():
        item = json.loads(json.dumps(item))
        try:
            digest, binding, bundle = fingerprint(catalog, rid)
            if item.get('sample_sha256') != bundle.samples.checksum:
                raise ValueError('sample_changed')
            for channel in item['channels']:
                if channel.get('hold') is not False:
                    continue
                proof = channel.get('header_evidence', {})
                index = proof.get('channel_index')
                if (type(index) is not int or not 0 <= index < len(bundle.channels)
                    or bundle.channels[index].channel_id != channel['channel_id']
                    or bundle.channels[index].name != proof.get('channel_label')
                    or proof.get('source_sha256') != binding['header']
                    or channel.get('description_sha256') != binding['description']):
                    raise ValueError('review_evidence_changed')
            old = previous['records'].get(rid, {})
            if not old.get('record_hold'):
                for channel in item['channels']:
                    prior = next((c for c in old.get('channels', []) if c['channel_id'] == channel['channel_id']), {})
                    human = prior.get('human_review')
                    if human and (human['action'] == 'hold' or not channel.get('hold')):
                        channel['human_review'] = human
                        channel['hold'] = human['action'] == 'hold'
        except (ValueError, OSError, KeyError, TypeError) as exc:
            digest = 'invalid'
            item = dict(record_hold=True, channels=[], error=str(exc))
        append(catalog, rid, item, digest, 'candidate')
        count += 1
    return count


def load_review(catalog):
    path = store_path(catalog)
    if not path.exists():
        return None
    report = dict(version='persistent-unit-review/1', records={})
    with sqlite3.connect(path.as_uri()+'?mode=ro', uri=True) as db:
        rows = db.execute('SELECT record_id,binding,payload FROM events WHERE id IN (SELECT MAX(id) FROM events GROUP BY record_id)').fetchall()
    for rid, binding, payload in rows:
        try:
            current, _, _ = fingerprint(catalog, rid)
            if current != binding:
                raise ValueError('review_binding_changed')
            report['records'][rid] = json.loads(payload)
        except (ValueError, OSError, KeyError, TypeError) as exc:
            report['records'][rid] = dict(record_hold=True, channels=[], error=str(exc))
    return report


def decide(catalog, record_id, channel_id, action, reviewer, reason):
    """Confirm an existing candidate or hold it. Conflicting units need correction upstream."""
    if action not in ('confirm', 'hold') or not reviewer.strip() or not reason.strip():
        raise ValueError('action_reviewer_reason_required')
    review = load_review(catalog)
    item = (review or {}).get('records', {}).get(record_id)
    if not item or item.get('record_hold'):
        raise ValueError('current_bound_review_required')
    channels = [c for c in item['channels'] if c['channel_id'] == channel_id]
    if len(channels) != 1:
        raise ValueError('unique_review_channel_required')
    channel = channels[0]
    if action == 'confirm' and (channel.get('hold') or channel.get('decision', {}).get('status') not in ('consistent', 'file_supported')):
        raise ValueError('conflict_or_hold_requires_new_evidence_review')
    channel.update(hold=action == 'hold', human_review=dict(action=action, reviewer=reviewer, reason=reason))
    digest, _, _ = fingerprint(catalog, record_id)
    append(catalog, record_id, item, digest, action)
    return dict(status=action, record_id=record_id, channel_id=channel_id, scientific_approval=False)
