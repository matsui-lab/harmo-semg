import pytest
from harmo.mixed_batch import route, file_assets, run


def test_invenio_and_legacy_plan_equivalent():
    spec=route('https://zenodo.org/records/123')
    url='https://zenodo.org/api/records/123/files/emg.csv/content'
    row=dict(key='emg.csv',size=15,checksum='md5:'+'a'*32)
    legacy=dict(id=123,files=[dict(row,links={'self':url})])
    current=dict(id='123',files={'entries':{'emg.csv':dict(row,links={'content':url})}})
    assert file_assets(spec,legacy)==file_assets(spec,current)


@pytest.mark.parametrize('change', ['key','checksum','url','size'])
def test_invenio_manifest_rejects_unsafe_values(change):
    row=dict(key='emg.csv',size=15,checksum='md5:'+'a'*32,
             links={'content':'https://zenodo.org/api/records/123/files/emg.csv/content'})
    if change=='key': row['key']='other.csv'
    if change=='checksum': row['checksum']='bad'
    if change=='url': row['links']['content']='https://evil.example/file'
    if change=='size': row['size']=-1
    with pytest.raises(ValueError):
        file_assets(route('https://zenodo.org/records/123'),dict(id=123,files={'entries':{'emg.csv':row}}))


def test_invenio_to_catalog_keeps_use_unassessed(tmp_path):
    import hashlib
    import sqlite3
    data=b'time_s,EMG\n0,1\n0.001,2\n0.002,3\n'
    def metadata(spec):
        return dict(id='123',files={'entries':{'emg.csv':dict(size=len(data),
            checksum='md5:'+hashlib.md5(data).hexdigest(),
            links={'content':'https://zenodo.org/api/records/123/files/emg.csv/content'})}})
    def acquire(asset,dest,library):
        dest.write_bytes(data)
        return {'pass':True}
    r=run(['https://zenodo.org/records/123'],tmp_path/'out',fetch_metadata=metadata,acquire=acquire)
    assert r['entries'][0]['converted']==1
    with sqlite3.connect(r['catalog']) as db:
        assert db.execute("SELECT count(*) FROM assessments WHERE status='unassessed'").fetchone()[0]==2
