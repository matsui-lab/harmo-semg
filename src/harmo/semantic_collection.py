"""Local, append-only snapshots of executed harmonization and its bound evidence.

Registration preserves purpose holds. It does not authorize reuse or publish data.
SQLite transactions publish a revision and its content-addressed bytes together.
"""
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import re
import sqlite3

from harmo.walking_plan import read, sha, verify, digest


class Collection:
    def __init__(self, directory):
        self.root = Path(directory).resolve()
        self.root.mkdir(parents=True, exist_ok=True)
        self.path = self.root/'collection.sqlite'
        with self.connect() as db:
            db.executescript('''
                CREATE TABLE IF NOT EXISTS collection_meta(version INTEGER NOT NULL);
                CREATE TABLE IF NOT EXISTS objects(hash TEXT PRIMARY KEY, bytes BLOB NOT NULL);
                CREATE TABLE IF NOT EXISTS revisions(
                    id TEXT PRIMARY KEY, family TEXT NOT NULL, version INTEGER NOT NULL,
                    summary TEXT NOT NULL, manifest TEXT NOT NULL, manifest_hash TEXT NOT NULL,
                    UNIQUE(family,version));
            ''')
            if not db.execute('SELECT version FROM collection_meta').fetchall():
                db.execute('INSERT INTO collection_meta VALUES(1)')
            if db.execute('SELECT version FROM collection_meta').fetchall() != [(1,)]:
                raise ValueError('unsupported_collection_version')
        self.ensure_index()

    def connect(self):
        return sqlite3.connect(self.path, timeout=60)

    def ensure_index(self):
        from harmo.collection_index import schema, index_revision
        with self.connect() as db:
            exists = db.execute("SELECT 1 FROM sqlite_master WHERE name='indexed_revisions'").fetchone()
            if not exists and db.execute('SELECT COUNT(*) FROM revisions').fetchone()[0]:
                backup = self.root/'collection.pre-entity-index-v001.sqlite'
                if not backup.exists():
                    with backup.open('xb'):
                        pass
                    with sqlite3.connect(backup) as target:
                        db.backup(target)
            db.execute('BEGIN IMMEDIATE')
            schema(db)
            pending = db.execute('SELECT id FROM revisions WHERE id NOT IN (SELECT id FROM indexed_revisions)').fetchall()
            for (revision,) in pending:
                _, manifest = self._load(db, revision)
                index_revision(db, revision, manifest)

    def library(self, purpose=None):
        """Latest revision per record set; old versions remain accessible separately."""
        from harmo.collection_index import overview
        self.ensure_index()
        latest = {}
        for r in self.list():
            if r['family'] not in latest or r['version'] > latest[r['family']]['version']:
                latest[r['family']] = r
        with self.connect() as db:
            for r in latest.values():
                self._load(db, r['id'])
            return overview(db, list(latest.values()), purpose)

    def register(self, folder, expected, workspace, packet_path):
        folder = Path(folder).resolve()
        verify(expected)
        if str(folder/'execution.json') not in expected or str(folder/'plan.json') not in expected:
            raise ValueError('executed_artifacts_required')
        report = read(folder/'execution.json')
        if report.get('phase') != 'executed':
            raise ValueError('execution_required')
        plan = read(folder/'plan.json')
        inputs = dict(plan['inputs'])
        inputs.update(workspace.get('context_inputs', {}))
        inputs[str(Path(packet_path).resolve())] = sha(packet_path)
        packet = read(packet_path)
        inputs.update({b['path']: b['sha256'] for b in packet['bindings']})
        verify(inputs)
        files = dict(inputs, **expected)
        if sum(Path(p).stat().st_size for p in files) > 2*1024**3:
            raise ValueError('collection_snapshot_exceeds_2_GiB')
        if any(Path(p).stat().st_size > 512*1024**2 for p in files):
            raise ValueError('collection_file_exceeds_512_MiB')
        # Deterministic identity ignores the transient execution directory and run ID.
        def canonical(value):
            if isinstance(value, dict):
                return {canonical(k): canonical(v) for k, v in value.items() if k != 'run_id'}
            if isinstance(value, list):
                return [canonical(v) for v in value]
            return value.replace(str(folder), '$RUN') if isinstance(value, str) else value
        manifest = {}
        identity = {}
        for path, checksum in files.items():
            p = Path(path)
            name = 'run/'+p.relative_to(folder).as_posix() if p.is_relative_to(folder) else 'input/'+str(p)
            manifest[name] = checksum
            if name.startswith('run/') and p.suffix == '.json':
                identity[name] = digest(canonical(read(p)))
            else:
                identity[canonical(name)] = checksum
        workspace_bytes = json.dumps(workspace, ensure_ascii=False, sort_keys=True).encode()
        workspace_hash = hashlib.sha256(workspace_bytes).hexdigest()
        manifest['workspace.json'] = workspace_hash
        identity['workspace.json'] = workspace_hash
        family = digest(sorted((r['dataset'], r['record_id']) for r in plan['records']))
        revision_id = digest(identity)
        with self.connect() as db:
            db.execute('BEGIN IMMEDIATE')
            existing = db.execute('SELECT summary FROM revisions WHERE id=?', (revision_id,)).fetchone()
            if existing:
                self._load(db, revision_id)  # A duplicate is not allowed to hide damaged storage.
                return dict(json.loads(existing[0]), reused=True)
            for path, checksum in files.items():
                content = Path(path).read_bytes()
                if hashlib.sha256(content).hexdigest() != checksum:
                    raise ValueError('source_changed_during_registration')
                self._put(db, checksum, content)
            self._put(db, workspace_hash, workspace_bytes)
            verify(files)
            version = db.execute('SELECT COALESCE(MAX(version),0)+1 FROM revisions WHERE family=?', (family,)).fetchone()[0]
            summary = dict(id=revision_id, family=family, version=version,
                created=datetime.now(timezone.utc).isoformat(),
                datasets=sorted({r['dataset'] for r in plan['records']}),
                records=len(plan['records']), curves=report['curves'], comparisons=report['comparisons'],
                muscles=sorted({c['muscle'] for r in plan['records'] for c in r['channels']}),
                purposes=report['purposes'], review_metrics=report['review_metrics'],
                scientific_approval=False, amplitude_pooling_allowed=False,
                status='stored-not-scientifically-qualified')
            encoded = json.dumps(manifest, sort_keys=True)
            db.execute('INSERT INTO revisions VALUES(?,?,?,?,?,?)',
                (revision_id, family, version, json.dumps(summary), encoded,
                 digest(dict(summary=summary, manifest=manifest))))
            from harmo.collection_index import index_revision
            index_revision(db, revision_id, manifest)
        return dict(summary, reused=False)

    @staticmethod
    def _put(db, checksum, content):
        row = db.execute('SELECT bytes FROM objects WHERE hash=?', (checksum,)).fetchone()
        if row and hashlib.sha256(row[0]).hexdigest() != checksum:
            raise ValueError('stored_object_changed')
        if not row:
            db.execute('INSERT INTO objects VALUES(?,?)', (checksum, content))

    def list(self):
        with self.connect() as db:
            rows = []
            for encoded, manifest, checksum in db.execute('SELECT summary,manifest,manifest_hash FROM revisions ORDER BY rowid DESC'):
                summary = json.loads(encoded)
                if digest(dict(summary=summary, manifest=json.loads(manifest))) != checksum:
                    raise ValueError('stored_manifest_changed')
                rows.append(summary)
            return rows

    def _load(self, db, revision_id):
        if not isinstance(revision_id, str) or not re.fullmatch('[0-9a-f]{64}', revision_id):
            raise ValueError('invalid_registration_id')
        row = db.execute('SELECT summary,manifest,manifest_hash FROM revisions WHERE id=?', (revision_id,)).fetchone()
        if not row:
            raise ValueError('registration_not_found')
        summary, manifest = json.loads(row[0]), json.loads(row[1])
        if digest(dict(summary=summary, manifest=manifest)) != row[2]:
            raise ValueError('stored_manifest_changed')
        for checksum in set(manifest.values()):
            obj = db.execute('SELECT bytes FROM objects WHERE hash=?', (checksum,)).fetchone()
            if not obj or hashlib.sha256(obj[0]).hexdigest() != checksum:
                raise ValueError('stored_object_changed')
        return summary, manifest

    def get(self, revision_id):
        with self.connect() as db:
            summary, manifest = self._load(db, revision_id)
            def product(name):
                return json.loads(db.execute('SELECT bytes FROM objects WHERE hash=?', (manifest[name],)).fetchone()[0])
            return dict(registration=summary, result=product('run/execution.json'),
                        workspace=product('workspace.json'))

    def artifact(self, revision_id, filename):
        names = {'curves.csv': 'processing/curves.csv', 'diagnostic.json': 'processing/diagnostic.json',
                 'execution.json': 'execution.json', 'decisions.json': 'decisions.json',
                 'work-queue.json': 'work-queue.json'}
        if filename not in names:
            raise ValueError('artifact_not_allowed')
        with self.connect() as db:
            _, manifest = self._load(db, revision_id)
            return db.execute('SELECT bytes FROM objects WHERE hash=?',
                              (manifest['run/'+names[filename]],)).fetchone()[0]
