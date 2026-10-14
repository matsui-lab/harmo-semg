import hashlib
import json
from types import SimpleNamespace
import pytest
from harmo.repository_bridge import repository_request, download_asset, collection_articles


def document(a=None):
    a = a or asset()
    return dict(id=123, version=2, files=[dict(id=456, name=a['name'], size=4,
        computed_md5=asset()['checksum'], download_url=a['download_url'])])


def asset():
    return dict(source_record_doi="10.6084/m9.figshare.123.v2",download_url="https://ndownloader.figshare.com/files/456",
        name="sample.mat",size_bytes=4,checksum_algorithm="md5",checksum=hashlib.md5(b"data").hexdigest(),container="file")


def test_version_and_institutional_figshare():
    a=asset()
    assert repository_request(a)["version"] == 2
    a["source_record_doi"]="10.26180/24515092.v4"
    assert repository_request(a)==dict(repository="figshare",id="24515092",version=4,name="sample.mat")


def test_zenodo_and_mendeley():
    a=asset(); a["source_record_doi"]="10.5281/zenodo.123"
    assert repository_request(a)["repository"]=="zenodo"
    a.update(source_record_doi="10.17632/example.1",download_url="https://data.mendeley.com/file")
    assert repository_request(a) is None


def test_figshare_must_pin_version():
    a=asset(); a["source_record_doi"]="10.6084/m9.figshare.123"
    with pytest.raises(ValueError): repository_request(a)


def test_bridge_validates_bytes_before_adoption(tmp_path,monkeypatch):
    monkeypatch.setattr('harmo.mixed_batch.metadata', lambda spec: document())
    def fake(command,**kwargs):
        from pathlib import Path
        directory=Path(command[4])
        request=json.loads(Path(command[3]).read_text())
        assert request["version"]==2
        (directory/request["name"]).write_bytes(b"data")
        (directory/"acquisition.json").write_text('{"PhysioIO":"test"}')
        return SimpleNamespace(returncode=0,stdout="",stderr="")
    monkeypatch.setattr("harmo.repository_bridge.subprocess.run",fake)
    destination=tmp_path/"target.mat"
    assert download_asset(asset(),destination)["pass"]
    assert destination.read_bytes()==b"data"
    with pytest.raises(FileExistsError): download_asset(asset(),destination)
    bad=asset(); bad["checksum"]="0"*32
    with pytest.raises(ValueError): download_asset(bad,tmp_path/"bad.mat")
    assert not (tmp_path/"bad.mat").exists()


@pytest.mark.parametrize('field,value', [('id',999), ('size',5), ('computed_md5','0'*32), ('name','other.mat'), ('download_url','https://example.org/file')])
def test_fixed_selection_rejects_mismatched_metadata(field, value):
    from harmo.repository_bridge import validate_figshare_selection
    doc = document(); doc['files'][0][field] = value
    with pytest.raises(ValueError):
        validate_figshare_selection(asset(), repository_request(asset()), doc)


def test_duplicate_name_uses_pinned_transport(tmp_path, monkeypatch):
    doc = document()
    doc['files'].append(dict(doc['files'][0], id=789, size=3, computed_md5='0'*32))
    monkeypatch.setattr('harmo.mixed_batch.metadata', lambda spec: doc)
    def ranges(a, destination):
        assert a['download_url'].endswith('/456')
        destination.write_bytes(b'data')
        return {"pass": True}
    monkeypatch.setattr('harmo.range_download.download_ranges', ranges)
    monkeypatch.setattr('harmo.repository_bridge.subprocess.run', lambda *a, **kw: pytest.fail('name-only downloader called'))
    result = download_asset(asset(), tmp_path/'target')
    assert result['pass']
    assert result['acquisition']['reason'] == 'duplicate_filename_fixed_id_selection'
    assert (tmp_path/'target').read_bytes() == b'data'


@pytest.mark.parametrize('mutation', ['version', 'record', 'file_id', 'duplicate_id'])
def test_fixed_selection_identity_guards(mutation):
    from harmo.repository_bridge import validate_figshare_selection
    a = asset(); doc = document()
    if mutation == 'version': doc['version'] = 3
    if mutation == 'record': doc['id'] = 999
    if mutation == 'file_id': a['file_id'] = 999
    if mutation == 'duplicate_id': doc['files'].append(dict(doc['files'][0]))
    with pytest.raises(ValueError):
        validate_figshare_selection(a, repository_request(a), doc)


def test_collection_bridge_calls_physioio_and_preserves_result(tmp_path,monkeypatch):
    def fake(command,**kwargs):
        from pathlib import Path
        assert command[3]=='99'
        assert command[-1]=='/first:/second'
        Path(command[4]).write_text('{"PhysioIO":"0.2.6","articles":[]}')
        return SimpleNamespace(returncode=0,stdout="",stderr="")
    monkeypatch.setattr('harmo.repository_bridge.subprocess.run',fake)
    out=tmp_path/'members.json'
    assert collection_articles('99',out,'/first:/second')['PhysioIO']=='0.2.6'
    with pytest.raises(FileExistsError): collection_articles('99',out)
    with pytest.raises(ValueError): collection_articles('../99',tmp_path/'bad.json')
