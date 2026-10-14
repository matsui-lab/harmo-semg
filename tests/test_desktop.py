from copy import deepcopy
import hashlib
import json
from pathlib import Path
import stat
import zipfile

import pytest

from harmo.desktop import Desktop, data_directory
from harmo.desktop_demo import create
from harmo.desktop_project import export_project, import_project, make_workspace, relative
from harmo.semantic_server import ReviewSession
from harmo.walking_plan import sha


def archive(tmp_path):
    source=create(tmp_path/'source')
    destination=tmp_path/'example.harmo'
    export_project(source,destination)
    return source,destination


def rewrite(source,destination,change):
    with zipfile.ZipFile(source) as z:
        entries={n:z.read(n) for n in z.namelist()}
    change(entries)
    with zipfile.ZipFile(destination,'w') as z:
        for n,raw in entries.items(): z.writestr(n,raw)


def test_portable_roundtrip_without_original_files(tmp_path):
    source,packed=archive(tmp_path)
    original={str(p.relative_to(source.parent)):sha(p) for p in source.parent.rglob('*') if p.is_file()}
    # Break every original reference before import. Do not patch missing paths.
    source.parent.rename(tmp_path/'unavailable-source')
    summary=import_project(packed,tmp_path/'different location')
    workspace=make_workspace(summary,tmp_path/'review')
    session=ReviewSession(workspace,tmp_path/'runs')
    result=session.evaluate(deepcopy(session.data['decisions']))
    execution=session.execute(result['run_id'])
    assert execution['curves']==2 and execution['comparisons']==1
    assert result['review_metrics']['reviewed_items']==0
    assert not execution['scientific_approval']
    assert original=={str(p.relative_to(tmp_path/'unavailable-source')):sha(p)
                     for p in (tmp_path/'unavailable-source').rglob('*') if p.is_file()}
    assert all(not p.startswith(str(tmp_path/'source')) for p in session.data['context_inputs'])


@pytest.mark.parametrize('name',['../escape','/absolute','a/../b','a\\b','a//b','a/./b','a:stream','x\x00'])
def test_unsafe_paths(name):
    with pytest.raises(ValueError): relative(name)


def test_tampered_member_rejected_before_materialization(tmp_path):
    _,packed=archive(tmp_path)
    bad=tmp_path/'bad.harmo'
    rewrite(packed,bad,lambda entries:entries.update({'data/source-000/description.html':b'changed'}))
    with pytest.raises(ValueError,match='checksum'):
        import_project(bad,tmp_path/'target')
    assert not (tmp_path/'target').exists()


def test_absolute_metadata_rejected_before_reads(tmp_path):
    _,packed=archive(tmp_path)
    def change(entries):
        manifest=json.loads(entries['project.json'])
        manifest['intake']['catalog']='/etc/passwd'
        entries['project.json']=json.dumps(manifest).encode()
    bad=tmp_path/'bad.harmo'
    rewrite(packed,bad,change)
    with pytest.raises(ValueError,match='absolute_reference'):
        import_project(bad,tmp_path/'target')
    assert not (tmp_path/'target').exists()


def test_cannot_disable_record_template_safety(tmp_path):
    _,packed=archive(tmp_path)
    def change(entries):
        manifest=json.loads(entries['project.json'])
        name=next(n for n in manifest['files'] if n.endswith('recording.bundle.json'))
        manifest['files'][name]['template']=False
        entries['project.json']=json.dumps(manifest).encode()
    bad=tmp_path/'bad.harmo'
    rewrite(packed,bad,change)
    with pytest.raises(ValueError,match='relocatable_record'):
        import_project(bad,tmp_path/'target')
    assert not (tmp_path/'target').exists()


def test_zip_symlink_rejected(tmp_path):
    path=tmp_path/'symlink.harmo'
    with zipfile.ZipFile(path,'w') as z:
        info=zipfile.ZipInfo('escape'); info.external_attr=(stat.S_IFLNK|0o777)<<16
        z.writestr(info,'/etc/passwd')
    with pytest.raises(ValueError,match='regular_'):
        import_project(path,tmp_path/'out')


def test_excessive_declared_zip_budget(tmp_path,monkeypatch):
    _,packed=archive(tmp_path)
    monkeypatch.setattr('harmo.desktop_project.MAX_BYTES',1)
    monkeypatch.setattr('harmo.desktop_project.MAX_FILES',1)
    with pytest.raises(ValueError,match='excessive'):
        import_project(packed,tmp_path/'out')


def test_existing_destinations_preserved(tmp_path):
    source,packed=archive(tmp_path)
    before=sha(packed)
    with pytest.raises(FileExistsError): export_project(source,packed)
    with pytest.raises(FileExistsError): import_project(packed,source.parent)
    assert sha(packed)==before


def test_listing_persists_and_does_not_expose_paths(tmp_path):
    app=Desktop(tmp_path/'app')
    folder=app.projects/('a'*32); folder.mkdir()
    (folder/'project.json').write_text(json.dumps(dict(id='a'*32,name='Example',kind='project',workspace='/private/internal')))
    assert Desktop(app.root).listing()==[dict(id='a'*32,name='Example',kind='project')]
    with pytest.raises(ValueError):app.project('../bad')
    assert data_directory(tmp_path)==tmp_path


def test_frozen_schemas(tmp_path,monkeypatch):
    import harmo.schema_resources as resources
    root=tmp_path/'harmo'; (root/'schemas').mkdir(parents=True)
    (root/'schemas/x.json').write_text('{}')
    monkeypatch.setattr(resources,'__file__',str(root/'schema_resources.py'))
    monkeypatch.setattr(resources.sys,'frozen',True,raising=False)
    assert resources.schema_path('x.json')==root/'schemas/x.json'


def test_loopback_startup_never_resolves_DNS(monkeypatch):
    from harmo.semantic_server import LoopbackServer
    import socketserver
    monkeypatch.setattr('socket.getfqdn',lambda *a:pytest.fail('No DNS lookup during local startup'))
    monkeypatch.setattr(socketserver.TCPServer,'server_bind',lambda server:None)
    server=object.__new__(LoopbackServer)
    server.server_address=('127.0.0.1',12345)
    server.server_bind()
    assert server.server_name=='localhost' and server.server_port==12345
