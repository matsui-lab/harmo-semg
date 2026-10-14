import hashlib
import sqlite3
import json
from pathlib import Path
import io
import zipfile
import pytest
from harmo.mixed_batch import route, file_assets, run


@pytest.mark.parametrize("url", ["http://figshare.com/articles/dataset/x/1/1", "https://figshare.com.evil.org/articles/dataset/x/1/1",
    "https://user@zenodo.org/records/1", "https://doi.org/10.6084/m9.figshare.1", "https://zenodo.org/records/1?token=abc",
    "https://doi.org/10.6084/m9.figshare.c.4494755"])
def test_reject_unversioned_or_unsafe_urls(url):
    with pytest.raises(ValueError): route(url)


def test_routes_and_canonical_deduplication():
    assert route("https://springernature.figshare.com/articles/dataset/x/123/2")["canonical"] == "https://doi.org/10.6084/m9.figshare.123.v2"
    assert route("https://doi.org/10.5281/zenodo.45")["canonical"] == "https://zenodo.org/records/45"
    assert route("https://physionet.org/files/emgdb/1.0.0/")["repository"] == "physionet"


def test_shared_store_file_pipeline_and_failure_isolation(tmp_path):
    content=b"time_s,EMG\n0,1\n0.001,2\n0.002,3\n"
    md5=hashlib.md5(content).hexdigest()
    def metadata(spec):
        if spec["repository"] == "figshare":
            return dict(id=123,version=2,files=[dict(id=1,name="test.csv",size=len(content),computed_md5=md5)])
        return dict(id=45,files=[dict(key="test.csv",size=len(content),checksum="md5:"+md5,links={"self":"https://zenodo.org/records/45/files/test.csv"})])
    def acquire(asset,dest,r_library):
        dest.write_bytes(content)
        return {"pass": True}
    urls=["https://invalid.example/", "https://doi.org/10.6084/m9.figshare.123.v2",
          "https://figshare.com/articles/dataset/x/123/2", "https://zenodo.org/records/45"]
    result=run(urls,tmp_path/"out",fetch_metadata=metadata,acquire=acquire)
    assert [r["status"] for r in result["entries"]] == ["held","converted-with-use-holds","duplicate","converted-with-use-holds"]
    assert result["status"] == "partial"
    db=sqlite3.connect(result["catalog"])
    assert db.execute("SELECT count(*) FROM records").fetchone()[0] == 2
    assert db.execute("SELECT count(*) FROM assessments WHERE status='unassessed'").fetchone()[0] == 4
    db.close()


def test_budget_prevents_downloader(tmp_path):
    def metadata(spec):
        return dict(id=123,version=2,files=[dict(id=1,name="a.mat",size=10,computed_md5="0"*32)])
    def acquire(*args):
        pytest.fail("budget-exceeding download must not be called")
    r=run(["https://doi.org/10.6084/m9.figshare.123.v2"],tmp_path/"out",max_bytes=5,fetch_metadata=metadata,acquire=acquire)
    assert r["entries"][0]["status"] == "held"
    assert "exceed" in r["entries"][0]["errors"][0]["error"]


def test_metadata_version_and_filename_checks():
    spec=route("https://doi.org/10.6084/m9.figshare.123.v2")
    with pytest.raises(ValueError,match="version"):
        file_assets(spec,dict(id=123,version=1,files=[]))
    with pytest.raises(ValueError,match="filename"):
        file_assets(spec,dict(id=123,version=2,files=[dict(name="../x",id=1,size=1,computed_md5="0"*32)]))


def test_physionet_uses_same_catalog_and_keeps_partial(tmp_path):
    def phys(urls, output, library, budget, cap):
        assert output == (tmp_path / "out/catalog")
        assert cap == 1
        return dict(network_bytes=2,run_id="fixture",datasets=[dict(status="partial",records=[dict(status="converted")])])
    r=run(["https://physionet.org/content/emgdb/1.0.0/"],tmp_path/"out",max_bytes=10,max_records=1,physionet=phys)
    assert r["remaining_payload_budget"] == 8
    assert r["entries"][0]["status"] == "partial"


def test_failed_requested_selection_is_not_success(tmp_path):
    def phys(*args):
        return dict(network_bytes=0,run_id="fixture",datasets=[dict(status="completed",records=[])])
    r=run(["https://physionet.org/content/emgdb/1.0.0/"],tmp_path/"out",physionet=phys,
          selection_request={"invalid":"request"})
    assert r["entries"][0]["status"] == "completed"
    assert r["selection"]["status"] == "failed"
    assert r["status"] == "partial"


@pytest.mark.parametrize('names', [('same.csv','same.csv'), ('EMG.csv','emg.csv'), ('é.csv','e\u0301.csv')])
def test_colliding_names_receive_stable_id_paths(names):
    spec = route('https://doi.org/10.6084/m9.figshare.123.v2')
    rows = [dict(id=i,name=name,size=4,computed_md5='0'*32) for i,name in enumerate(names,1)]
    assets = file_assets(spec, dict(id=123,version=2,files=rows))
    assert [a['local_name'] for a in assets] == ['file-1/'+names[0], 'file-2/'+names[1]]
    reverse = file_assets(spec, dict(id=123,version=2,files=rows[::-1]))
    assert {a['file_id']:a['local_name'] for a in assets} == {a['file_id']:a['local_name'] for a in reverse}
    with pytest.raises(ValueError, match='file_id'):
        file_assets(spec,dict(id=123,version=2,files=[rows[0],rows[0]]))


@pytest.mark.parametrize('archive', [False, True])
@pytest.mark.parametrize('identical', [False, True])
def test_duplicate_files_survive_acquisition_conversion_and_repeat(tmp_path, archive, identical):
    csv = b'time_s,EMG\n0,1\n0.001,2\n0.002,3\n'
    payloads = {}
    for fid, data in [(1,csv), (2,csv if identical else csv.replace(b',3', b',9'))]:
        if archive:
            stream = io.BytesIO()
            with zipfile.ZipFile(stream,'w') as z: z.writestr('signal.csv', data)
            data = stream.getvalue()
        payloads[fid] = data
    name = 'same.zip' if archive else 'same.csv'
    def metadata(spec):
        return dict(id=123,version=2,files=[dict(id=fid,name=name,size=len(data),computed_md5=hashlib.md5(data).hexdigest()) for fid,data in payloads.items()])
    def acquire(a,dest,library):
        assert dest.parent.name == 'file-%s' % a['file_id']
        dest.write_bytes(payloads[a['file_id']])
        return {'pass':True}
    for _ in range(2):
        result = run(['https://doi.org/10.6084/m9.figshare.123.v2'],tmp_path/'out',fetch_metadata=metadata,acquire=acquire)
        entry = result['entries'][0]
        assert entry['status'] == 'converted-with-use-holds'
        assert entry['acquired_files'] == 2
        stage = Path(entry['source_report'])
        receipts = json.loads((stage/'acquisition.json').read_text())
        assert {a['file_id'] for a in receipts} == {1,2}
        for a in receipts:
            assert (stage/'raw'/a['local_name']).read_bytes() == payloads[a['file_id']]
        if archive:
            assert len({a['provenance'] for a in entry['archives']}) == 2
            assert {json.loads(Path(a['provenance']).read_text())['repository_file']['file_id'] for a in entry['archives']} == {1,2}
        with sqlite3.connect(result['catalog']) as db:
            assert db.execute('SELECT count(*) FROM records').fetchone()[0] == 2


def test_duplicate_name_failed_file_not_adopted(tmp_path):
    data = b'time_s,EMG\n0,1\n0.001,2\n0.002,3\n'
    def metadata(spec):
        return dict(id=123,version=2,files=[dict(id=i,name='same.csv',size=len(data),computed_md5=hashlib.md5(data).hexdigest()) for i in (1,2)])
    def acquire(a,dest,library):
        dest.write_bytes(data)
        if a['file_id'] == 2: raise ValueError('failed verification')
        return {'pass':True}
    result = run(['https://doi.org/10.6084/m9.figshare.123.v2'],tmp_path/'out',fetch_metadata=metadata,acquire=acquire)
    assert result['status'] == 'partial'
    assert result['entries'][0]['errors'][0]['file_id'] == 2
    with sqlite3.connect(result['catalog']) as db:
        assert db.execute('SELECT count(*) FROM records').fetchone()[0] == 1


def test_retry_only_failed_payload_and_verify_cache(tmp_path):
    from harmo.payload_cache import location
    data = b'time_s,EMG\n0,1\n0.001,2\n0.002,3\n'
    doc = dict(id=123,version=2,files=[dict(id=i,name='same.csv',size=len(data),computed_md5=hashlib.md5(data).hexdigest()) for i in (1,2)])
    calls = []
    def acquire(a,dest,library):
        calls.append(a['file_id'])
        if calls == [1,2]: raise ValueError('interrupted')
        dest.write_bytes(data)
        return {'pass':True}
    urls = ['https://doi.org/10.6084/m9.figshare.123.v2']
    kwargs = dict(fetch_metadata=lambda _:doc, acquire=acquire)
    first = run(urls,tmp_path/'out',**kwargs)
    assert first['status'] == 'partial'
    second = run(urls,tmp_path/'out',max_bytes=len(data),**kwargs)
    assert calls == [1,2,2]
    assert second['entries'][0]['reused_payloads'] == 1
    assert second['entries'][0]['downloaded_payloads'] == 1
    third = run(urls,tmp_path/'out',max_bytes=1,**kwargs)
    assert calls == [1,2,2]
    assert third['entries'][0]['reused_payloads'] == 2
    with sqlite3.connect(third['catalog']) as db:
        assert db.execute('SELECT count(*) FROM records').fetchone()[0] == 2
    a = file_assets(route(urls[0]),doc)[0]
    payload = location(tmp_path/'out/payload-cache',a)/'payload'
    payload.write_bytes(b'corrupt')
    fourth = run(urls,tmp_path/'out',**kwargs)
    assert fourth['status'] == 'partial'
    assert fourth['entries'][0]['reused_payloads'] == 1
    assert calls == [1,2,2]
    assert payload.read_bytes() == b'corrupt'


def test_changed_payload_identity_requires_new_download(tmp_path):
    data = b'time_s,EMG\n0,1\n0.001,2\n0.002,3\n'
    calls = []
    def metadata(spec):
        return dict(id=123,version=2,files=[dict(id=1,name='signal.csv',size=len(data),computed_md5=hashlib.md5(data).hexdigest())])
    def acquire(a,dest,library):
        calls.append(a['checksum']); dest.write_bytes(data); return {'pass':True}
    urls = ['https://doi.org/10.6084/m9.figshare.123.v2']
    run(urls,tmp_path/'out',fetch_metadata=metadata,acquire=acquire)
    data = data.replace(b',3',b',9')
    second = run(urls,tmp_path/'out',fetch_metadata=metadata,acquire=acquire)
    assert len(calls) == 2 and calls[0] != calls[1]
    assert second['entries'][0]['reused_payloads'] == 0


def test_unverified_payload_never_committed(tmp_path):
    doc = dict(id=123,version=2,files=[dict(id=1,name='signal.csv',size=4,computed_md5=hashlib.md5(b'good').hexdigest())])
    def acquire(a,dest,library):
        dest.write_bytes(b'evil'); return {'pass':True}
    result = run(['https://doi.org/10.6084/m9.figshare.123.v2'],tmp_path/'out',fetch_metadata=lambda _:doc,acquire=acquire)
    assert result['status'] == 'partial'
    assert result['entries'][0]['acquired_files'] == 0
    assert not list((tmp_path/'out/payload-cache').glob('*/receipt.json'))


def test_cache_receipt_identity_and_symlink_rejected(tmp_path):
    from harmo import payload_cache
    a = dict(source_record_doi='x',file_id=1,name='x.csv',download_url='https://example.org/1',
             size_bytes=4,checksum_algorithm='md5',checksum=hashlib.md5(b'data').hexdigest(),container='file')
    source = tmp_path/'input'; source.write_bytes(b'data')
    cached = payload_cache.store(tmp_path/'cache',a,source,{'pass':True})
    assert payload_cache.lookup(tmp_path/'cache',a) == cached
    source.write_bytes(b'edit')
    assert cached.read_bytes() == b'data'
    receipt = cached.parent/'receipt.json'
    record = json.loads(receipt.read_text()); record['identity']['file_id'] = 2
    receipt.write_text(json.dumps(record))
    with pytest.raises(ValueError,match='identity'):
        payload_cache.lookup(tmp_path/'cache',a)
    link = tmp_path/'link'; link.symlink_to(tmp_path/'cache',target_is_directory=True)
    with pytest.raises(ValueError,match='symlink'):
        payload_cache.lookup(link,a)
