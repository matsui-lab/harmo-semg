"""Purpose-independent records/derivatives and version-bound pair assessments.

An additive, rebuildable index of immutable collection snapshots. Source-contract
assessments never qualify derived waveforms by association.
"""
import json
from pathlib import Path
from harmo.walking_plan import digest

PURPOSES = ('activation-timing', 'waveform-shape', 'amplitude-pooling')


def schema(db):
    for table in ('common_records', 'derived_signals', 'use_assessments'):
        db.execute(f'CREATE TABLE IF NOT EXISTS {table}(id TEXT PRIMARY KEY, payload TEXT NOT NULL)')
    db.execute('''CREATE TABLE IF NOT EXISTS entity_revisions(
        revision_id TEXT NOT NULL, kind TEXT NOT NULL, entity_id TEXT NOT NULL,
        PRIMARY KEY(revision_id,kind,entity_id))''')
    db.execute('CREATE TABLE IF NOT EXISTS indexed_revisions(id TEXT PRIMARY KEY, version INTEGER NOT NULL, index_hash TEXT NOT NULL)')


def product(db, manifest, name):
    row = db.execute('SELECT bytes FROM objects WHERE hash=?', (manifest[name],)).fetchone()
    if not row:
        raise ValueError('missing_stored_product')
    return json.loads(row[0])


def add(db, revision, table, value):
    identity = digest(value)
    encoded = json.dumps(value, sort_keys=True)
    old = db.execute(f'SELECT payload FROM {table} WHERE id=?', (identity,)).fetchone()
    if old and old[0] != encoded:
        raise ValueError('indexed_entity_changed')
    db.execute(f'INSERT OR IGNORE INTO {table} VALUES(?,?)', (identity, encoded))
    db.execute('INSERT OR IGNORE INTO entity_revisions VALUES(?,?,?)', (revision, table, identity))
    return identity


def index_revision(db, revision, manifest):
    if db.execute('SELECT 1 FROM indexed_revisions WHERE id=?', (revision,)).fetchone():
        return
    plan = product(db, manifest, 'run/plan.json')
    execution = product(db, manifest, 'run/processing/diagnostic.json')
    assessment = product(db, manifest, 'run/assessment/assessment.json')
    records = {}
    for r in plan['records']:
        sample_path = str(Path(r['row'][3])/'normalized.npz')
        value = dict(dataset=r['dataset'], source_record_id=r['record_id'], name=r['record'],
                     samples_object=manifest.get('input/'+sample_path),
                     scope='source-record-not-a-purpose-specific-copy')
        records[r['record_id']] = add(db, revision, 'common_records', value)
    for c in execution['curves']:
        # Purpose and batch identity are deliberately excluded. Conditions and
        # processing representation remain part of the derivative identity.
        value = dict(record_id=records[c['record_id']], channel_id=c['channel_id'],
                     channel=c['channel'], muscle=c['muscle'], side=c['side'],
                     task=c['task'], comparison_reference=c['comparison_reference'],
                     representation=c['representation_status'],
                     recipe=c.get('recipe', plan['registry']['processing']),
                     profile=execution['profile'], output_object=manifest['run/processing/'+c['key']+'.npz'],
                     cycles=c['cycles'], scientific_approval=False,
                     scope='derived-signal-not-qualified-by-source-pair-assessments')
        add(db, revision, 'derived_signals', value)
    for job in assessment['jobs']:
        for entry in job.get('result', {}).get('entries', []):
            decision = entry.get('decision', {})
            assertion = decision.get('assertion', {})
            left, right = job['left'], entry['record_id']
            if left not in records or right not in records:
                raise ValueError('assessment_record_outside_registered_scope')
            if assertion and (assertion['left_id'] != left or assertion['right_id'] != right or
                              assertion['intended_use'] != entry['purpose'] or
                              assertion['left_channel_id'] != job['channel'] or
                              assertion['right_channel_id'] != entry['channel']):
                raise ValueError('assessment_pair_identity_mismatch')
            value = dict(left_record_id=records[left], right_record_id=records[right],
                         left_channel_id=job['channel'], right_channel_id=entry['channel'],
                         purpose=entry['purpose'], status=entry['status'],
                         decision=decision, reasons=entry.get('reasons', []),
                         request=job['result'].get('request'),
                         assessment_scope='source-contract-pair', derived_signal_approval=False,
                         scientific_approval=False)
            add(db, revision, 'use_assessments', value)
    # Omitted and failed comparisons remain in the original assessment snapshot;
    # absence of a use_assessment must always mean not evaluated, never allowed.
    links = db.execute('SELECT kind,entity_id FROM entity_revisions WHERE revision_id=? ORDER BY kind,entity_id', (revision,)).fetchall()
    db.execute('INSERT INTO indexed_revisions VALUES(?,1,?)', (revision, digest(links)))


def overview(db, revisions, purpose=None):
    if purpose is not None and purpose not in PURPOSES:
        raise ValueError('unsupported_purpose')
    selected = {r['id'] for r in revisions}
    for revision in selected:
        row = db.execute('SELECT version,index_hash FROM indexed_revisions WHERE id=?', (revision,)).fetchone()
        links = db.execute('SELECT kind,entity_id FROM entity_revisions WHERE revision_id=? ORDER BY kind,entity_id', (revision,)).fetchall()
        if not row or row[0] != 1 or digest(links) != row[1]:
            raise ValueError('indexed_links_changed')
    output = dict(purposes=list(PURPOSES), selected_purpose=purpose,
                  scope='latest-revision-per-record-set; no cross-revision approval propagation',
                  revisions=revisions, scientific_approval=False)
    for table in ('common_records', 'derived_signals', 'use_assessments'):
        associations = {}
        for revision, entity in db.execute('SELECT revision_id,entity_id FROM entity_revisions WHERE kind=?', (table,)):
            if revision in selected:
                associations.setdefault(entity, []).append(revision)
        values = []
        found = set()
        for identity, encoded in db.execute(f'SELECT id,payload FROM {table} ORDER BY id'):
            if identity not in associations:
                continue
            found.add(identity)
            value = json.loads(encoded)
            if digest(value) != identity:
                raise ValueError('indexed_entity_changed')
            if table == 'use_assessments' and purpose and value['purpose'] != purpose:
                continue
            values.append(dict(value, id=identity, revision_ids=sorted(associations[identity])))
        if found != set(associations):
            raise ValueError('indexed_entity_missing')
        output[table] = values
    return output
