import hashlib
import json
import io
import zipfile
from pathlib import Path
import numpy as np
import pytest
from harmo.mixed_batch import route,run
from harmo.mendeley_repository import metadata,file_assets
from harmo.walking_plan import prepare
from harmo.walking_execute import execute

ID='11111111-2222-3333-4444-555555555555'


def mendeley_file(name='x.csv', payload=b'test'):
    return dict(id=ID,filename=name,size=len(payload),content_details=dict(size=len(payload),
        sha256_hash=hashlib.sha256(payload).hexdigest(),
        download_url='https://data.mendeley.com/public-files/datasets/abcdefghij/files/'+ID+'/file_downloaded'))


def test_pinned_mendeley_routes():
    a=route('https://data.mendeley.com/datasets/abcdefghij/2')
    assert a==route('https://doi.org/10.17632/abcdefghij.2')
    for bad in ('https://data.mendeley.com/datasets/abcdefghij','https://data.mendeley.com.evil.org/datasets/abcdefghij/2'):
        with pytest.raises(ValueError):route(bad)


def test_metadata_public_snapshot_version_and_bounded_lists():
    spec=route('https://doi.org/10.17632/abcdefghij.2')
    snapshot=dict(id='abcdefghij',version=2,doi='10.17632/abcdefghij.2',is_confidential=False)
    def fetch(url):
        if '/snapshot/' in url:return snapshot
        if '/folders/' in url:return []
        return [mendeley_file()]
    d=metadata(spec,fetch);a=file_assets(spec,d)[0]
    assert a['checksum_algorithm']=='sha256' and a['download_url'].endswith('?version=2')
    snapshot['version']=3
    with pytest.raises(ValueError,match='identity'):metadata(spec,fetch)


@pytest.mark.parametrize('change',['url','name','checksum','duplicate'])
def test_mendeley_rejects_unbound_payloads(change):
    spec=route('https://doi.org/10.17632/abcdefghij.2');f=mendeley_file()
    d=dict(id=spec['id'],version=2,files=[f])
    if change=='url':f['content_details']['download_url']='http://127.0.0.1/secret'
    if change=='name':f['filename']='../x.csv'
    if change=='checksum':f['content_details'].pop('sha256_hash')
    if change=='duplicate':d['files'].append(f)
    with pytest.raises(ValueError):file_assets(spec,d)


@pytest.mark.parametrize('repository',['figshare','mendeley','zenodo'])
@pytest.mark.parametrize('archived',[False,True])
def test_repository_CSV_to_same_walking_processor(tmp_path,repository,archived):
    t=np.arange(15000)/1500
    emg=(2+np.sin(2*np.pi*t))*np.sin(2*np.pi*100*t)
    foot=(((t%1)>.2)&((t%1)<.5)).astype(int)
    payload=('time_s,RT EMG,RT FOOTSWITCH\n'+'\n'.join(f'{x:.17g},{e:.17g},{f}' for x,e,f in zip(t,emg,foot))+'\n').encode()
    filename='walk.csv'
    # Authors use the original filename, not Harmo's internal file-ID path.
    relative=filename
    description='<table><tr><th>Record</th><th>Channel</th><th>Sensor type</th><th>Muscle</th><th>Side</th><th>Task</th></tr><tr><td>'+relative+'</td><td>RT EMG</td><td>surface-emg</td><td>tibialis anterior</td><td>right</td><td>walking</td></tr></table>'
    if archived:
        buffer=io.BytesIO()
        with zipfile.ZipFile(buffer,'w') as z:z.writestr(filename,payload)
        payload=buffer.getvalue();filename='signals.zip'
    if repository=='figshare':
        url='https://doi.org/10.6084/m9.figshare.123.v1'
        doc=dict(id=123,version=1,description=description,files=[dict(id=1,name=filename,size=len(payload),computed_md5=hashlib.md5(payload).hexdigest())])
    elif repository=='mendeley':
        url='https://doi.org/10.17632/abcdefghij.1'
        doc=dict(id='abcdefghij',version=1,description=description,files=[mendeley_file(filename,payload)])
    else:
        url='https://zenodo.org/records/123'
        doc=dict(id=123,description=description,files={'entries':{filename:dict(size=len(payload),
            checksum='md5:'+hashlib.md5(payload).hexdigest(),links={'content':'https://zenodo.org/records/123/files/'+filename})}})
    def acquire(a,dest,library):dest.write_bytes(payload);return dict(pass_=True,**{'pass':True})
    intake=run([url],tmp_path/'store',fetch_metadata=lambda _:doc,acquire=acquire)
    assert intake['entries'][0]['converted']==1
    summary=tmp_path/'automatic.summary.json';summary.write_text(json.dumps(dict(intake=intake)))
    plan=prepare(summary)
    assert plan['records'][0]['status']=='diagnostic_ready',plan['records'][0]['blockers']
    result=execute(plan,tmp_path/'processed')
    assert len(result['curves'])==1 and not result['scientific_approval']
    from harmo.semantic_review import prepare as packet
    r=packet(summary,tmp_path/'review');assert r['records']==1
    data=json.loads((tmp_path/'review/packet.json').read_text())
    assert data['cases'][0]['sources'][-1]['kind']=='derived-record-schema'
    record=intake['entries'][0]['ingestion']['records'][0]
    original=Path(intake['entries'][0]['source_report'])/'raw'/record['source'];original.write_bytes(b'changed')
    with pytest.raises(ValueError,match='payload_changed|member_changed'):prepare(summary)


def test_unselected_duplicate_filename_does_not_become_semantic_alias(tmp_path):
    from harmo.repository_context import join_documents
    (tmp_path/'repository.metadata.json').write_text(json.dumps({'files':[{'filename':'x.csv'},{'filename':'x.csv'}]}))
    (tmp_path/'acquisition.json').write_text(json.dumps([{'name':'x.csv','local_name':'file-1/x.csv'}]))
    entry=dict(repository='mendeley',dataset='d',source_report=str(tmp_path),
               ingestion={'records':[{'source':'file-1/x.csv','name':'file-1/x.csv::record'}]})
    docs=[dict(dataset='d',result={'observations':[{'record':'x.csv'}]})]
    assert join_documents(docs,[entry])==docs


def test_archive_unsupported_is_held_before_download(tmp_path):
    doc=dict(id='abcdefghij',version=1,files=[mendeley_file('data.rar')])
    result=run(['https://doi.org/10.17632/abcdefghij.1'],tmp_path/'store',fetch_metadata=lambda _:doc,
               acquire=lambda *a:pytest.fail('unsupported archive must not download'))
    assert result['entries'][0]['converted']==0
    assert result['entries'][0]['unsupported_before_download'][0]['name']=='data.rar'
    summary=tmp_path/'automatic.summary.json';summary.write_text(json.dumps({'intake':result}))
    plan=prepare(summary)
    from harmo.general_workflow import queue
    work=queue(plan,execute(plan,tmp_path/'processed'))
    assert work['item_count']==1 and work['items'][0]['reason']=='unsupported_archive_container'
