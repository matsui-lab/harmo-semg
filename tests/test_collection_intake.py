import hashlib
import pytest
from harmo.mixed_batch import route, run

URL='https://doi.org/10.6084/m9.figshare.c.99.v1'
ARTICLE='https://doi.org/10.6084/m9.figshare.123.v2'
DATA=b'time_s,EMG\n0,1\n0.001,2\n0.002,3\n'


def metadata(spec):
    if spec['repository'] == 'figshare-collection':
        return dict(id=99,version=1,articles_count=1)
    return dict(id=123,version=2,files=[dict(id=1,name='data.csv',size=len(DATA),
        computed_md5=hashlib.md5(DATA).hexdigest())])


def listing(*args):
    return dict(PhysioIO='fixture',articles=[dict(article_id='123',doi=ARTICLE.split('doi.org/')[1])])


def acquire(asset,dest,library):
    dest.write_bytes(DATA)
    return {'pass':True}


def test_collection_connection_dedup_and_accounting(tmp_path):
    assert route('https://figshare.com/collections/title/99/1')['canonical'] == URL
    r=run([URL,ARTICLE],tmp_path/'out',fetch_metadata=metadata,list_collection=listing,acquire=acquire,max_bytes=len(DATA))
    assert r['status']=='completed-with-use-holds'
    assert r['remaining_payload_budget']==0
    assert r['entries'][0]['status']=='completed-with-use-holds'
    assert r['entries'][1]['converted']==1
    assert r['entries'][2]['status']=='duplicate'
    assert r['entries'][2]['duplicate_of']==1


def test_failed_duplicate_keeps_parent_partial(tmp_path):
    r=run([URL,ARTICLE],tmp_path/'out',fetch_metadata=metadata,list_collection=listing,acquire=acquire,max_bytes=1)
    assert r['entries'][0]['status']=='partial'
    assert r['entries'][2]['status']=='duplicate'
    assert r['status']=='partial'


def test_collection_only_schedules_child_download(tmp_path):
    r=run([URL],tmp_path/'out',fetch_metadata=metadata,list_collection=listing,acquire=acquire)
    assert len(r['entries'])==2
    assert r['entries'][1]['parent_collection_index']==0
    assert r['entries'][1]['converted']==1
    assert r['status']=='completed-with-use-holds'


@pytest.mark.parametrize('case', ['historical','missing','unversioned','wrong_id','changed','count_cap','duplicate'])
def test_collection_fail_closed(tmp_path,case):
    calls=[]
    def meta(spec):
        d=metadata(spec)
        if case=='historical' and spec['api'].endswith('/99'): d['version']=2
        if case=='count_cap': d['articles_count']=1000
        if case=='duplicate': d['articles_count']=2
        return d
    def members(*args):
        d=listing(); calls.append(1)
        if case=='missing': d['articles']=[]
        if case=='unversioned': d['articles'][0]['doi']='10.6084/m9.figshare.123'
        if case=='wrong_id': d['articles'][0]['article_id']='124'
        if case=='changed' and len(calls)>1: d['articles'][0]['doi']='10.6084/m9.figshare.123.v3'
        if case=='duplicate': d['articles']*=2
        return d
    def forbidden(*args): pytest.fail('rejected collection must not download')
    r=run([URL],tmp_path/'out',fetch_metadata=meta,list_collection=members,acquire=forbidden)
    assert len(r['entries'])==1
    assert r['entries'][0]['errors']
    assert r['status']=='partial'
