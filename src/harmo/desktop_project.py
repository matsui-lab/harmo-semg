"""Portable, offline intake snapshots. Import regenerates review, never approval.

Only checked source/record artifacts are exported, not a user's working tree,
collection, previous decisions, executable code, or arbitrary SQLite databases.
"""
from copy import deepcopy
import hashlib
import json
from pathlib import Path, PurePosixPath
import re
import sqlite3
import stat
import ntpath
from urllib.parse import unquote, urlsplit
from zipfile import ZipFile, ZIP_DEFLATED

VERSION = 'harmo-project/1'
MAX_BYTES = 512 * 1024**2
MAX_FILES = 10000
TOKEN = '@project/'


def relative(value):
    if not isinstance(value, str) or not value or '\\' in value or ':' in value:
        raise ValueError('unsafe_project_path')
    path = PurePosixPath(value)
    if path.is_absolute() or any(p in ('', '.', '..') for p in value.split('/')):
        raise ValueError('unsafe_project_path')
    if any(ord(c) < 32 for c in value):
        raise ValueError('unsafe_project_path')
    if any(p.endswith(('.', ' ')) or re.fullmatch(r'(CON|PRN|AUX|NUL|COM[1-9]|LPT[1-9])',p.split('.')[0],re.I)
           for p in path.parts):
        raise ValueError('nonportable_project_path')
    return value


def tree(value, transform):
    if isinstance(value, str): return transform(value)
    if isinstance(value, list): return [tree(v, transform) for v in value]
    if isinstance(value, dict): return {k: tree(v, transform) for k, v in value.items()}
    return value


def encoder(mappings):
    ordered = sorted(mappings, key=lambda p: -len(str(p[0])))
    def convert(value):
        is_uri = value.startswith('file:')
        path = unquote(urlsplit(value).path) if is_uri else value
        if is_uri and re.match(r'^/[A-Za-z]:/',path): path=path[1:]
        if not path.startswith('/') and not ntpath.isabs(path): return value
        path=path.replace('\\','/')
        for old, new in ordered:
            old = str(old).replace('\\','/')
            compare,base=(path.casefold(),old.casefold()) if re.match(r'^[A-Za-z]:/',old) else (path,old)
            if compare == base or compare.startswith(base+'/'):
                suffix = path[len(old):].lstrip('/')
                result = TOKEN+new+('/'+suffix if suffix else '')
                return result
        raise ValueError('project_reference_outside_selected_sources: '+path)
    return convert


def expand(value, root):
    def convert(text):
        if text.startswith(TOKEN):
            return str(root/relative(text[len(TOKEN):]))
        if text.startswith(('/', 'file:')) or ntpath.isabs(text):
            raise ValueError('absolute_reference_in_portable_metadata')
        return text
    return tree(value, convert)


def export_project(summary_path, destination):
    from harmo.walking_plan import prepare, sha, verify
    from harmo.repository_context import records, bound_files, source_paths
    # Existing integrity and supported-source checks run before packaging.
    plan = prepare(summary_path, {'records': 2}, offline=True)
    summary = json.loads(Path(summary_path).read_text())
    intake = deepcopy(summary['intake'])
    db_path = Path(intake['catalog']).resolve()
    with sqlite3.connect(db_path.as_uri()+'?mode=ro', uri=True) as db:
        rows = db.execute('SELECT id,dataset,name,status,location,metadata FROM records ORDER BY id').fetchall()
    if not 0 < len(rows) <= 1024: raise ValueError('portable_record_budget')
    mappings = [(db_path, 'catalog/catalog.sqlite'), (db_path.parent/'sources', 'catalog/sources')]
    paths = {}
    def add(path, target, template=False):
        path = Path(path)
        if path.is_symlink() or not path.is_file(): raise ValueError('regular_project_file_required')
        target = relative(target)
        if target in paths and paths[target][0] != path: raise ValueError('duplicate_project_file')
        paths[target] = (path, template)
    for row in rows:
        if not re.fullmatch(r'[A-Za-z0-9_-]{1,128}', row[0]): raise ValueError('unsupported_record_identifier')
        folder = Path(row[4]).resolve()
        target = 'catalog/records/'+row[0]
        mappings.append((folder, target))
        for name in ('record.json','recording.bundle.json','trust.report.json','proposal.json','normalized.npz'):
            if (folder/name).is_file(): add(folder/name, target+'/'+name, name.endswith('.json'))
    for index, entry in enumerate(intake['entries']):
        if not entry.get('source_report'): continue
        stage = Path(entry['source_report']).resolve()
        prefix = 'source-%03d' % index
        mappings.append((stage, prefix))
        if entry['repository'] == 'physionet':
            files = [stage/'description.html', stage/'source.evidence.json']
        else:
            files = bound_files(entry)
        for record in records(entry):
            files.extend(source_paths(entry, record, db_path))
        archive_manifests = {Path(a['provenance']).resolve() for a in entry.get('archives', []) if a['status']=='extracted'}
        for path in dict.fromkeys(files):
            path = Path(path).resolve()
            if path.is_relative_to(stage):
                # Source documents (including raw JSON) are never rewritten.
                add(path, prefix+'/'+path.relative_to(stage).as_posix(), path in archive_manifests)
            elif path.is_relative_to(db_path.parent/'sources'):
                add(path, 'catalog/sources/'+path.relative_to(db_path.parent/'sources').as_posix())
            else: raise ValueError('source_not_in_portable_roots')
    encode = encoder(mappings)
    # Intake is data-only; do not carry stale purpose decisions or output paths.
    intake.pop('run_dir', None)
    encoded_intake = tree(intake, encode)
    encoded_rows = []
    for row in rows:
        record = list(row)
        record[4] = encode(record[4])
        record[5] = json.dumps(tree(json.loads(record[5]), encode))
        encoded_rows.append(record)
    manifest = dict(version=VERSION, intake=encoded_intake, records=encoded_rows, files={},
                    synthetic=bool(summary.get('synthetic', False)),
                    source_summary_sha256=sha(summary_path), scientific_approval=False,
                    review_policy='Regenerate all decisions as unreviewed on import.')
    if len(paths) > MAX_FILES: raise ValueError('project_file_budget')
    total = 0
    with ZipFile(destination, 'x', ZIP_DEFLATED) as archive:
        for target, (path, template) in sorted(paths.items()):
            if path.stat().st_size > MAX_BYTES-total: raise ValueError('project_size_budget')
            raw = path.read_bytes()
            original_hash = hashlib.sha256(raw).hexdigest()
            if template:
                raw = json.dumps(tree(json.loads(raw), encode), ensure_ascii=False).encode()
            total += len(raw)
            if total > MAX_BYTES: raise ValueError('project_size_budget')
            manifest['files'][target] = dict(sha256=hashlib.sha256(raw).hexdigest(), bytes=len(raw),
                template=template, original_sha256=original_hash)
            archive.writestr('data/'+target, raw)
        verify(plan['inputs'])
        archive.writestr('project.json', json.dumps(manifest, ensure_ascii=False))
    return dict(files=len(paths), bytes=total, records=len(rows), scientific_approval=False)


def import_project(source, destination):
    """Verify before extraction; only data files are accepted. No extractall()."""
    root = Path(destination).resolve()
    if root.exists(): raise FileExistsError(root)
    with ZipFile(source) as archive:
        infos = archive.infolist()
        names = [i.filename for i in infos]
        if len(infos)>MAX_FILES+1 or len(set(n.casefold() for n in names)) != len(names):
            raise ValueError('duplicate_or_excessive_project_members')
        if sum(i.file_size for i in infos)>MAX_BYTES+16*1024**2:
            raise ValueError('project_size_budget')
        for info in infos:
            relative(info.filename)
            mode = info.external_attr >> 16
            if stat.S_ISLNK(mode) or info.is_dir() or info.flag_bits & 1:
                raise ValueError('regular_unencrypted_project_members_required')
        if archive.getinfo('project.json').file_size>16*1024**2: raise ValueError('manifest_size_budget')
        manifest = json.loads(archive.read('project.json'))
        if manifest.get('version') != VERSION: raise ValueError('unsupported_project_version')
        files = manifest['files']
        if set(names) != {'project.json', *('data/'+relative(n) for n in files)}:
            raise ValueError('unmanifested_project_member')
        if not 0<len(manifest['records'])<=1024: raise ValueError('portable_record_budget')
        # Validate transformed metadata without opening any of its paths.
        intake = expand(manifest['intake'], root)
        rows = expand(manifest['records'], root)
        for row in rows:
            if len(row)!=6 or not all(isinstance(v,str) for v in row): raise ValueError('invalid_record_row')
            row[5] = json.dumps(expand(json.loads(row[5]), root))
        output = {}
        for name, meta in files.items():
            raw = archive.read('data/'+name)
            if len(raw)!=meta['bytes'] or hashlib.sha256(raw).hexdigest()!=meta['sha256']:
                raise ValueError('project_checksum_mismatch')
            if meta['template']:
                # No executable extensions, pickle or generated scripts.
                if not name.endswith('.json'): raise ValueError('JSON_template_required')
                raw = json.dumps(expand(json.loads(raw), root), ensure_ascii=False).encode()
            output[name] = raw
        required = ['record.json','recording.bundle.json','trust.report.json','normalized.npz']
        for row in rows:
            location = Path(row[4])
            if not location.is_relative_to(root): raise ValueError('record_outside_project')
            if any((location/n).relative_to(root).as_posix() not in output for n in required):
                raise ValueError('incomplete_portable_record')
            for name in required[:3]+['proposal.json']:
                key = (location/name).relative_to(root).as_posix()
                if key in files and not files[key]['template']:
                    raise ValueError('relocatable_record_metadata_required')
        for entry in intake['entries']:
            stage = Path(entry.get('source_report', ''))
            if not stage.is_relative_to(root): raise ValueError('source_outside_project')
            for item in entry.get('archives', []):
                if item['status'] != 'extracted': continue
                path = Path(item['provenance'])
                if not path.is_relative_to(stage): raise ValueError('archive_outside_source')
                key = path.relative_to(root).as_posix()
                if key not in files or not files[key]['template']:
                    raise ValueError('relocatable_archive_metadata_required')
        catalog_path = root/'catalog/catalog.sqlite'
        if intake['catalog'] != str(catalog_path) or 'catalog/catalog.sqlite' in output:
            raise ValueError('reconstructed_catalog_required')
        root.mkdir(parents=True)
        for name, raw in output.items():
            target = root/name
            target.parent.mkdir(parents=True, exist_ok=True)
            with target.open('xb') as stream: stream.write(raw)
        from harmo.public_batch import catalog
        catalog_path.parent.mkdir(exist_ok=True)
        with sqlite3.connect(catalog_path) as db:
            catalog(db)
            db.executemany('INSERT INTO records VALUES(?,?,?,?,?,?)', rows)
        from harmo.walking_plan import save, sha, prepare
        summary = root/'automatic.summary.json'
        save(summary, dict(intake=intake, portable_import=dict(version=VERSION,
             source_sha256=sha(source), source_summary_sha256=manifest['source_summary_sha256']),
             synthetic=bool(manifest.get('synthetic', False)),
             scientific_approval=False))
        plan = prepare(summary, {'records':2}, offline=True)
        # Ensure the imported plan cannot depend on a previous machine's files.
        package = Path(__file__).resolve().parent
        import sys
        executable = str(Path(sys.executable).resolve()) if getattr(sys, 'frozen', False) else None
        if any(not Path(p).is_relative_to(root) and not Path(p).is_relative_to(package)
               and p != executable
               for p in plan['inputs']):
            raise ValueError('imported_plan_has_external_dependency')
        save(root/'import.receipt.json', dict(project_sha256=sha(source),
             source_files={n:m['original_sha256'] for n,m in files.items()},
             imported_files={n:sha(root/n) for n in files},
             review_reset=True, scientific_approval=False))
        return summary


def make_workspace(summary, output):
    from harmo.walking_plan import prepare
    from harmo.semantic_review import prepare as packet
    from harmo.semantic_workspace import prepare as workspace
    root = Path(output)
    plan = prepare(summary, {'records':2}, offline=True)
    packet(summary, root/'packet', max_channels=16, record_ids={r['record_id'] for r in plan['records']})
    workspace(root/'packet/packet.json', root/'workspace', max_targets=128)
    return root/'workspace/workspace.json'
