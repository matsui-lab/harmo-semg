import json
from harmo.public_export import export_catalog
from test_public_export import setup_catalog


def test_unit_conflict_blocks_amplitude_not_other_purpose_gate(tmp_path):
    request=setup_catalog(tmp_path)
    review=dict(records={})
    for rid in ('1','2'):
        bundle=json.loads((tmp_path/rid/'recording.bundle.json').read_text())
        review['records'][rid]=dict(sample_sha256=bundle['samples']['checksum'],record_hold=False,
            channels=[dict(channel_id=c['channel_id'],hold=True) for c in bundle['channels']])
    baseline=export_catalog(tmp_path/'catalog.sqlite',request,tmp_path/'baseline')
    assessed=export_catalog(tmp_path/'catalog.sqlite',request,tmp_path/'checked',unit_review=review)
    assert any(e['purpose']=='amplitude-pooling' and e['status']=='exported' for e in baseline['entries'])
    assert all(e['status']=='held' for e in assessed['entries'] if e['purpose']=='amplitude-pooling')
    assert [(e['channel'],e['status']) for e in assessed['entries'] if e['purpose']=='activation-timing']==[(e['channel'],e['status']) for e in baseline['entries'] if e['purpose']=='activation-timing']


def test_missing_review_fails_closed_for_amplitude(tmp_path):
    request=setup_catalog(tmp_path)
    result=export_catalog(tmp_path/'catalog.sqlite',request,tmp_path/'out',unit_review=dict(records={}))
    assert all(e['status']=='held' for e in result['entries'] if e['purpose']=='amplitude-pooling')


def test_review_from_other_sample_version_is_not_reused():
    from harmo.catalog_unit_review import amplitude_holds
    review=dict(records={'r':dict(sample_sha256='old',record_hold=False,channels=[dict(channel_id='c',hold=False)])})
    assert amplitude_holds(review,'r','c','new')==['unit_review_unresolved_or_stale']
    assert not amplitude_holds(review,'r','c','old')


def test_enabled_cli_review_reaches_assessment(tmp_path,monkeypatch):
    from harmo import mixed_batch,catalog_unit_review,cli
    setup_catalog(tmp_path);run=tmp_path/'run';run.mkdir()
    monkeypatch.setattr(mixed_batch,'run',lambda *a:dict(run_dir=str(run),catalog=str(tmp_path/'catalog.sqlite'),status='completed-with-use-holds',entries=[dict(dataset='test/1.0.0')]))
    calls=[]
    def review(*args):calls.append(args);return dict(records={})
    monkeypatch.setattr(catalog_unit_review,'review_catalog',review)
    urls=tmp_path/'urls.json';urls.write_text('["https://physionet.org/content/emgdb/1.0.0/"]')
    import pytest
    with pytest.raises(SystemExit):cli.main(['harmonize','--urls',str(urls),'--output',str(tmp_path),'--purpose','amplitude-pooling','--local-llm-model','test'])
    assert calls and json.loads((run/'automatic.summary.json').read_text())['assessment']['exported']==0


def test_real_header_binding_and_source_review_persist_without_mutating_catalog(tmp_path):
    import sqlite3,shutil,hashlib
    from harmo.catalog_unit_review import review_catalog
    setup_catalog(tmp_path)
    stage=tmp_path/'stage';dest=stage/'converted'/'one'
    shutil.copytree(tmp_path/'1',dest);(stage/'raw').mkdir()
    for path in dest.glob('recording*.json'):
        data=json.loads(path.read_text())
        data['samples']['uri']=str(dest/'normalized.npz')
        path.write_text(json.dumps(data))
    (stage/'description.html').write_text('<main><p>Surface EMG is expressed in µV.</p></main>')
    header=stage/'raw'/'one.hea'
    header.write_text('one 2 1000 100\none.dat 16 10000/mV 16 0 0 0 0 TA\none.dat 16 10000/mV 16 0 0 0 0 GM\n')
    digest=hashlib.sha256(header.read_bytes()).hexdigest()
    with sqlite3.connect(tmp_path/'catalog.sqlite') as db:
        db.execute('UPDATE records SET name=?,location=?,metadata=? WHERE id=?',('one.hea',str(dest),json.dumps(dict(source_sha256={'one.hea':digest})),'1'))
    before=(tmp_path/'catalog.sqlite').read_bytes()
    def api(path,payload=None):
        if path=='chat':return dict(message=dict(content=json.dumps(dict(status='proposed',value='µV',alternatives=[],evidence_ids=['doc-0']))))
        return {}
    report=review_catalog(tmp_path/'catalog.sqlite',tmp_path/'review',{'test/1.0.0'},'fake',transport=api)
    assert all(c['decision']['status']=='conflict' for c in report['records']['1']['channels'])
    assert len(report['records']['1']['channels'])==2
    assert (tmp_path/'review/review.queue.json').exists()
    assert (tmp_path/'catalog.sqlite').read_bytes()==before

    from harmo.unit_review_store import save_review,load_review,decide,store_path
    import pytest
    assert save_review(tmp_path/'catalog.sqlite',report)==2
    persisted=load_review(tmp_path/'catalog.sqlite')
    assert persisted['records']['1']['channels'][0]['decision']['status']=='conflict'
    cid=persisted['records']['1']['channels'][0]['channel_id']
    with pytest.raises(ValueError,match='conflict_or_hold'):
        decide(tmp_path/'catalog.sqlite','1',cid,'confirm','test reviewer','synthetic test')
    decide(tmp_path/'catalog.sqlite','1',cid,'hold','test reviewer','synthetic conflict')
    save_review(tmp_path/'catalog.sqlite',report)
    assert load_review(tmp_path/'catalog.sqlite')['records']['1']['channels'][0]['human_review']['action']=='hold'
    with sqlite3.connect(store_path(tmp_path/'catalog.sqlite')) as db:
        assert db.execute('SELECT COUNT(*) FROM events').fetchone()[0]==5
    (stage/'description.html').write_text('<main><p>Changed definition.</p></main>')
    assert load_review(tmp_path/'catalog.sqlite')['records']['1']['record_hold']
    with pytest.raises(ValueError,match='current_bound'):
        decide(tmp_path/'catalog.sqlite','1',cid,'confirm','test reviewer','stale')


def test_persistent_review_enforced_without_explicit_argument(tmp_path):
    from harmo.unit_review_store import save_review
    request=setup_catalog(tmp_path)
    baseline=export_catalog(tmp_path/'catalog.sqlite',request,tmp_path/'baseline')
    assert any(e['status']=='exported' for e in baseline['entries'] if e['purpose']=='amplitude-pooling')
    # Unsupported fixtures cannot bind WFDB provenance: stored review fails closed.
    save_review(tmp_path/'catalog.sqlite',dict(records={'1':dict(channels=[])}))
    result=export_catalog(tmp_path/'catalog.sqlite',request,tmp_path/'stored')
    assert all(e['status']=='held' for e in result['entries'] if e['purpose']=='amplitude-pooling')
