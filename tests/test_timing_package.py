import hashlib
import io
import json
import stat
import zipfile
from pathlib import Path
import pytest
import numpy as np
from harmo.timing_package import checked_archive,restore,reference_names,VERSION
from harmo.reuse_workflow import URLS

ROOT=Path(__file__).resolve().parents[1]
ARTICLE=b'<article>Synthetic transport fixture, not scientific evidence.</article>'


@pytest.fixture(autouse=True)
def synthetic_article_hash(monkeypatch):
    monkeypatch.setattr('harmo.catalog_evidence.ARTICLE_HASH',hashlib.sha256(ARTICLE).hexdigest())


def package(tmp_path,change=None):
    # Synthetic payloads test transport, never physiological validity.
    buffer=io.BytesIO()
    np.savez(buffer,left_phase=np.linspace(0,100,101),right_phase=np.linspace(0,100,101),
             left_values=np.ones(101),right_values=np.ones(101))
    files={n:buffer.getvalue() for n in reference_names()}
    files.update({'sources/Subject%d.mat'%i:b'not-a-registered-source' for i in range(1,11)})
    files['sources/Gait120_011_to_020.zip']=b'not-an-archive'
    files['protocol.yaml']=(ROOT/'paper/physiology_positive_control_protocol.yaml').read_bytes()
    files['article.xml']=ARTICLE
    files['origin.json']=b'{}'
    manifest=dict(version=VERSION,urls=sorted(URLS),files={k:dict(bytes=len(v),sha256=hashlib.sha256(v).hexdigest()) for k,v in files.items()})
    if change=='evidence':manifest['files']['article.xml']['sha256']='0'*64
    if change=='hash':files['sources/Subject1.mat']=b'bad'
    if change=='missing':files.pop('origin.json')
    if change=='size':manifest['files']['origin.json']['bytes']=99
    path=tmp_path/'transfer.zip'
    with zipfile.ZipFile(path,'w') as z:
        for name,value in files.items():z.writestr(name,value)
        z.writestr('manifest.json',json.dumps(manifest))
        if change=='traversal':z.writestr('../outside','unsafe')
        if change=='duplicate':
            with pytest.warns(UserWarning):z.writestr('origin.json','{}')
        if change=='symlink':
            entry=zipfile.ZipInfo('link');entry.create_system=3;entry.external_attr=(stat.S_IFLNK|0o777)<<16
            z.writestr(entry,'/tmp/outside')
    return path


def test_checked_inventory(tmp_path):
    path=package(tmp_path)
    manifest=checked_archive(path)
    assert len(manifest['files'])==34


@pytest.mark.parametrize('change',['hash','missing','size','traversal','duplicate','symlink','evidence'])
def test_bad_package_refused_before_restore_output(tmp_path,change):
    path=package(tmp_path,change)
    with pytest.raises(ValueError):restore(path,tmp_path/'out')
    assert not (tmp_path/'out').exists()


def test_package_budget(tmp_path):
    path=package(tmp_path)
    with pytest.raises(ValueError):checked_archive(path,max_bytes=20)


def test_self_consistent_fake_sources_cannot_grant_approval(tmp_path,monkeypatch):
    # Isolate the raw-source gate; document semantics are tested elsewhere.
    monkeypatch.setattr('harmo.timing_package.acquire_article',lambda root,article,download:article)
    path=package(tmp_path)
    # Even hashes consistent with the manifest do not establish registered identity.
    with pytest.raises(ValueError,match='integrity_failure'):
        restore(path,tmp_path/'out')
    status=json.loads((tmp_path/'out/restore.status.json').read_text())
    assert status['status']=='failed' and status['stage']=='unpack'
    assert not (tmp_path/'out/workflow').exists()
    assert not (tmp_path/'out/inputs.receipt.json').exists()


def test_nested_reference_expansion_rejected():
    from harmo.timing_package import checked_reference
    buffer=io.BytesIO()
    with zipfile.ZipFile(buffer,'w',compression=zipfile.ZIP_DEFLATED) as z:
        for k in ('left_phase','right_phase','left_values','right_values'):z.writestr(k+'.npy',b'0'*65536)
    with pytest.raises(ValueError,match='expansion'):checked_reference(buffer.getvalue())
