import json
from pathlib import Path
import pytest
from test_public_export import setup_catalog
from harmo.automatic_workflow import assess_catalog


def test_supported_contracts_export_without_manual_reference(tmp_path):
    setup_catalog(tmp_path)
    report = assess_catalog(tmp_path/'catalog.sqlite',tmp_path/'out',['activation-timing','amplitude-pooling'])
    assert report['exported'] > 0
    assert len(report['records']) == 2
    assert report['status'] == 'evaluated'
    assert any(j['requirements'] for j in report['jobs'])


def test_missing_evidence_does_not_become_approval(tmp_path):
    setup_catalog(tmp_path,trusted=False)
    report = assess_catalog(tmp_path/'catalog.sqlite',tmp_path/'out',['activation-timing'])
    assert report['exported'] == 0
    assert all(r['status']=='held' for r in report['records'])
    assert report['jobs']


def test_budget_reports_unprocessed_without_false_completion(tmp_path):
    setup_catalog(tmp_path)
    report = assess_catalog(tmp_path/'catalog.sqlite',tmp_path/'out',['activation-timing'],max_comparisons=1)
    assert report['status']=='incomplete' and report['unprocessed_pairs']
    assert report['exported']==0


def test_invalid_record_does_not_stop_other_reporting(tmp_path):
    setup_catalog(tmp_path)
    (tmp_path/'2/recording.bundle.json').write_text('{}')
    report = assess_catalog(tmp_path/'catalog.sqlite',tmp_path/'out',['activation-timing'])
    assert len(report['records'])==2
    assert any('invalid_record_contract' in x for r in report['records'] for x in r['reasons'])
    assert report['exported']==0


def test_exclude_preexisting_unrequested_datasets(tmp_path):
    setup_catalog(tmp_path)
    report = assess_catalog(tmp_path/'catalog.sqlite',tmp_path/'out',['activation-timing'],datasets={'another-study'})
    assert not report['records'] and report['exported']==0


def test_url_command_connects_to_existing_intake(tmp_path,monkeypatch):
    from harmo import mixed_batch,cli
    setup_catalog(tmp_path)
    run = tmp_path/'run'; run.mkdir()
    monkeypatch.setattr(mixed_batch,'run',lambda *a:dict(run_dir=str(run),catalog=str(tmp_path/'catalog.sqlite'),status='completed-with-use-holds',entries=[dict(dataset='test/1.0.0')]))
    urls = tmp_path/'urls.json'; urls.write_text('["https://example.org/"]')
    with pytest.raises(SystemExit) as result:
        cli.main(['harmonize','--urls',str(urls),'--output',str(tmp_path),'--purpose','activation-timing','--purpose','amplitude-pooling'])
    assert result.value.code==0
    assert json.loads((run/'automatic.summary.json').read_text())['assessment']['exported']>0


def test_bad_document_receipts_are_isolated(tmp_path):
    from harmo.automatic_workflow import collect_saved_documents
    (tmp_path/'repository.metadata.json').write_text('[]')
    (tmp_path/'acquisition.json').write_text('{}')
    report = collect_saved_documents(dict(entries=[dict(source_report=str(tmp_path),dataset='test')]))
    assert not report['documents']
    assert len(report['held']) == 2


def test_document_failure_is_not_completed(tmp_path,monkeypatch):
    from harmo import automatic_workflow as workflow,mixed_batch
    setup_catalog(tmp_path)
    run = tmp_path/'run'; run.mkdir()
    monkeypatch.setattr(mixed_batch,'run',lambda *a:dict(run_dir=str(run),catalog=str(tmp_path/'catalog.sqlite'),status='completed-with-use-holds',entries=[dict(dataset='test/1.0.0')]))
    monkeypatch.setattr(workflow,'collect_saved_documents',lambda a:dict(documents=[],held=[dict(reason='network_failure')]))
    report = workflow.run([],tmp_path,['amplitude-pooling'])
    assert report['status'] == 'partial'


def test_source_conflict_changes_actual_output(tmp_path):
    from harmo.document_evidence import extract
    setup_catalog(tmp_path)
    positive=assess_catalog(tmp_path/'catalog.sqlite',tmp_path/'positive',['amplitude-pooling'])
    raw=b'<table><tr><th>Record</th><th>Channel</th><th>Units</th></tr><tr><td>1</td><td>TA</td><td>incompatible-unit</td></tr></table>'
    documents=[dict(dataset='test/1.0.0',result=extract(raw,'https://example.org/'))]
    negative=assess_catalog(tmp_path/'catalog.sqlite',tmp_path/'negative',['amplitude-pooling'],documents=documents)
    assert positive['exported']>0 and negative['exported']==0
    assert negative['records'][0]['documentary_observations']['conflicts']


def test_other_record_document_does_not_block(tmp_path):
    from harmo.document_evidence import extract
    setup_catalog(tmp_path)
    raw=b'<table><tr><th>Record</th><th>Channel</th><th>Units</th></tr><tr><td>other</td><td>TA</td><td>wrong-unit</td></tr></table>'
    report=assess_catalog(tmp_path/'catalog.sqlite',tmp_path/'out',['amplitude-pooling'],
        documents=[dict(dataset='test/1.0.0',result=extract(raw,'https://example.org/'))])
    assert report['exported']>0


@pytest.mark.parametrize('record',['1','2'])
def test_conflict_on_other_channel_preserves_eligible_output(tmp_path,record):
    from harmo.document_evidence import extract
    setup_catalog(tmp_path)
    raw=f'record,channel,unit\n{record},GM,incompatible-unit\n'.encode()
    report=assess_catalog(tmp_path/'catalog.sqlite',tmp_path/'out',['amplitude-pooling'],
        documents=[dict(dataset='test/1.0.0',result=extract(raw,'https://example.org/','csv'))])
    assert report['exported']>0
    entries=[e for j in report['jobs'] for e in j['result']['entries']]
    assert any(e.get('documentary_conflicts') for e in entries)
    assert all(not e.get('documentary_conflicts') for e in entries if e['status']=='exported')


@pytest.mark.parametrize('record',['1','2'])
def test_record_wide_task_conflict_blocks_all_channels(tmp_path,record):
    from harmo.document_evidence import extract
    setup_catalog(tmp_path)
    raw=f'record,task\n{record},walking\n{record},running\n'.encode()
    report=assess_catalog(tmp_path/'catalog.sqlite',tmp_path/'out',['amplitude-pooling'],
        documents=[dict(dataset='test/1.0.0',result=extract(raw,'https://example.org/','csv'))])
    assert report['exported']==0
    entries=[e for j in report['jobs'] for e in j['result']['entries']]
    assert entries and all(e.get('documentary_conflicts') for e in entries)


def test_protocol_join_conflict_reaches_export_gate(tmp_path):
    from harmo.document_evidence import extract
    setup_catalog(tmp_path)
    raw=json.dumps(dict(records=[dict(record=['1','2'],task='running',protocol_id='P')],
        protocols=[dict(protocol_id='P',task='walking')])).encode()
    report=assess_catalog(tmp_path/'catalog.sqlite',tmp_path/'out',['amplitude-pooling'],
        documents=[dict(dataset='test/1.0.0',result=extract(raw,'https://example.org/','json'))])
    assert report['exported']==0
    assert all(r['documentary_observations']['conflicts'] for r in report['records'])


def test_explicit_human_record_hold_blocks_otherwise_eligible_export(tmp_path):
    setup_catalog(tmp_path)
    observations=[dict(record='1',channel='',field='task',value='explicit-human-hold',status='review_hold')]
    report=assess_catalog(tmp_path/'catalog.sqlite',tmp_path/'held',['activation-timing'],
        documents=[dict(dataset='test/1.0.0',result=dict(observations=observations))])
    assert report['exported']==0
    assert report['records'][0]['documentary_observations']['conflicts'][0]['reason']=='explicit_human_review_hold'
