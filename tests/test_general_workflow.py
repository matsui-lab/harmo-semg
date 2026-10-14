"""Synthetic fixtures test software behavior, not physiological validity."""
import json
from dataclasses import replace
from pathlib import Path
import sqlite3

import numpy as np
import pytest

from harmo.walking_plan import prepare, review_template, checked_review, sha, digest
from harmo.walking_execute import execute
from harmo.general_workflow import run, checked_jobs
from harmo.evidence.models import ChannelDescriptor, Timebase
from harmo.public_batch import catalog
from test_evidence_contracts import bundle, passing_trust


def fixture(root, dataset='new-unregistered-study', folders=('walk', 'walk')):
    root.mkdir(parents=True, exist_ok=True)
    stage = root/'source'; stage.mkdir()
    raw = stage/'raw'; raw.mkdir()
    description = stage/'description.html'
    description.write_text('<p>Surface EMG of the anterior tibialis muscles was recorded.</p>'
        '<p>The files under the walk folder containing walking while counting.</p>'
        '<p>The files under the other folder containing walking without counting.</p>')
    (stage/'source.evidence.json').write_text(json.dumps(dict(source_uri='https://physionet.org/content/'+dataset+'/1.0.0/', source_sha256=sha(description))))
    db = sqlite3.connect(root/'catalog.sqlite'); catalog(db)
    records = []
    for index, folder in enumerate(folders):
        identity='record-'+str(index); name=folder+'/'+identity+'.hea'
        (raw/folder).mkdir(exist_ok=True)
        header=raw/name
        header.write_text(f'{identity} 2 1500 15000\n'+
            f'{identity}.dat 16 100(0)/uV 0 0 0 0 0 RT TIB.ANT.\n'+
            f'{identity}.dat 16 100(0)/V 0 0 0 0 0 RT FOOTSWITCH\n')
        data=header.with_suffix('.dat'); data.write_bytes(b'synthetic-source-'+bytes([index]))
        directory=root/identity; directory.mkdir()
        b=bundle(); b.recording_id=identity; b.study_id=dataset
        b.timebase=Timebase(sampling_rate_hz=1500, sample_count=15000)
        b.channels=[ChannelDescriptor('ch-0','RT TIB.ANT.','uV','unknown'),
                    ChannelDescriptor('ch-1','RT FOOTSWITCH','V','unknown')]
        t=np.arange(15000)/1500
        foot=((t%1) >= .2) & ((t%1) < .5)
        emg=(2+np.sin(2*np.pi*(t-index*.1)))*np.sin(2*np.pi*100*t)
        signal=directory/'normalized.npz'; np.savez(signal,time=t,values=np.column_stack([emg,foot.astype(float)]))
        b.samples=replace(b.samples, uri=str(signal), format='npz', checksum=sha(signal), shape=[15000,2])
        (directory/'recording.bundle.json').write_text(b.to_json())
        (directory/'trust.report.json').write_text(passing_trust(identity).to_json())
        (directory/'record.json').write_text(json.dumps(dict(metadata=dict(
            source_sha256={name:sha(header),str(data.relative_to(raw)):sha(data)},
            signals=[dict(label=c.name,unit=c.unit) for c in b.channels]))))
        db.execute('INSERT INTO records VALUES(?,?,?,?,?,?)', (identity,dataset+'/1.0.0',name,'converted',str(directory),'{}'))
        records.append(dict(id=identity,name=name,status='converted',location=str(directory)))
    db.commit(); db.close()
    summary=root/'automatic.summary.json'
    summary.write_text(json.dumps(dict(intake=dict(catalog=str(root/'catalog.sqlite'),
        entries=[dict(repository='physionet', dataset=dataset+'/1.0.0', source_report=str(stage), details=dict(records=records))]))))
    return summary


def test_unregistered_study_runs_without_ID_branch(tmp_path):
    summary=fixture(tmp_path/'a')
    plan=prepare(summary)
    assert all(r['status']=='diagnostic_ready' for r in plan['records'])
    result=execute(plan,tmp_path/'result')
    assert len(result['curves'])==2 and len(result['comparisons'])==1
    assert not result['scientific_approval'] and not result['amplitude_pooling_allowed']
    assert all(c['event_identity']=='unverified-electrical-state' for c in result['curves'])


def test_study_rename_preserves_numerical_outputs(tmp_path):
    a=execute(prepare(fixture(tmp_path/'a','arbitrary-alpha')),tmp_path/'out-a')
    b=execute(prepare(fixture(tmp_path/'b','arbitrary-beta')),tmp_path/'out-b')
    assert [c['median'] for c in a['curves']]==[c['median'] for c in b['curves']]


def test_task_contexts_not_implicitly_compared(tmp_path):
    summary=fixture(tmp_path/'a',folders=('walk','other'))
    # Existing conservative grammar rejects 'without'; use a positive distinct context.
    source=tmp_path/'a/source/description.html'
    source.write_text(source.read_text().replace('walking without counting','walking while listening'))
    receipt=source.parent/'source.evidence.json'; d=json.loads(receipt.read_text());d['source_sha256']=sha(source);receipt.write_text(json.dumps(d))
    result=execute(prepare(summary),tmp_path/'result')
    assert len(result['curves'])==2 and not result['comparisons']


def event_review(tmp_path, plan, action='confirm', value='initial-contact'):
    source=tmp_path/'manual.txt'; source.write_text('Synthetic fixture: upper-state rising edge marks initial contact.')
    review=review_template(plan); review['reviewer']='Synthetic test reviewer'
    for d in review['decisions']:
        if d['field']=='event_identity':
            d.update(action=action,value=value,scope_reason='Synthetic fixture exact record and channel scope',
                     evidence=[dict(path=str(source),sha256=sha(source),quote=source.read_text(),pointer='line:1')])
    path=tmp_path/'event-review.json';path.write_text(json.dumps(review))
    return path


def test_event_review_reaches_outputs_but_not_scientific_gate(tmp_path):
    plan=prepare(fixture(tmp_path/'a')); path=event_review(tmp_path,plan)
    result=execute(plan,tmp_path/'out',path)
    assert all(c['event_identity']=='initial-contact' for c in result['curves'])
    assert all(not c['detector_validated_against_ground_truth'] for c in result['curves'])
    assert not result['scientific_approval']


def test_different_electrical_encodings_join_on_reviewed_meaning(tmp_path):
    summary=fixture(tmp_path/'a')
    for index in (0,1):
        directory=tmp_path/'a'/('record-'+str(index)); signal=directory/'normalized.npz'
        with np.load(signal) as z: t=z['time'].copy(); values=z['values'].copy()
        low=((t%1)>=.2)&((t%1)<.5);high=((t%1)>=.6)&((t%1)<.9)
        values[:,1]=(low+8*high) if index==0 else (8*low+high)
        np.savez(signal,time=t,values=values)
        path=directory/'recording.bundle.json';d=json.loads(path.read_text());d['samples']['checksum']=sha(signal);path.write_text(json.dumps(d))
    plan=prepare(summary);path=event_review(tmp_path,plan)
    d=json.loads(path.read_text())
    for e in d['decisions']:
        if e['field']=='event_identity':
            initial=(e['record_id']=='record-0')==(e['anchor']=='lower-component')
            e['value']='initial-contact' if initial else 'toe-off'
    path.write_text(json.dumps(d));result=execute(plan,tmp_path/'out',path)
    assert len(result['comparisons'])==2
    assert all(c['left_anchor']!=c['right_anchor'] for c in result['comparisons'])
    assert {c['comparison_reference'] for c in result['comparisons']}=={'reviewed:initial-contact','reviewed:toe-off'}
    assert not result['scientific_approval']
    for e in d['decisions']:
        if e['field']=='event_identity':e['value']='initial-contact'
    path.write_text(json.dumps(d))
    with pytest.raises(ValueError,match='multiple_electrical_anchors'):checked_review(plan,path)


@pytest.mark.parametrize('action',['hold','reject'])
def test_explicit_event_hold_stops_processing(tmp_path,action):
    plan=prepare(fixture(tmp_path/'a')); path=event_review(tmp_path,plan,action)
    result=execute(plan,tmp_path/'out',path)
    assert not result['curves'] and result['status']=='held'


@pytest.mark.parametrize('change',['quote','hash','plan','reviewer','target','duplicate'])
def test_invalid_event_proof_is_rejected(tmp_path,change):
    plan=prepare(fixture(tmp_path/'a')); path=event_review(tmp_path,plan)
    d=json.loads(path.read_text()); e=next(x for x in d['decisions'] if x['field']=='event_identity')
    if change=='quote':e['evidence'][0]['quote']='not in the source'
    if change=='hash':e['evidence'][0]['sha256']='0'*64
    if change=='plan':d['plan_id']='0'*64
    if change=='reviewer':d['reviewer']=''
    if change=='target':e['record_id']='other-record'
    if change=='duplicate':d['decisions'].append(e)
    path.write_text(json.dumps(d))
    with pytest.raises(ValueError):checked_review(plan,path)


def test_reviewed_envelope_does_not_get_raw_filter(tmp_path):
    plan=prepare(fixture(tmp_path/'a')); path=event_review(tmp_path,plan)
    d=json.loads(path.read_text()); proof=next(x for x in d['decisions'] if x['field']=='event_identity')['evidence']
    for e in d['decisions']:
        if e['field']=='input_representation': e.update(action='confirm',value='envelope',evidence=proof,scope_reason='Synthetic processed input')
    path.write_text(json.dumps(d))
    assert not execute(plan,tmp_path/'out',path)['curves']


def test_source_change_after_plan_prevents_output(tmp_path):
    plan=prepare(fixture(tmp_path/'a'))
    (tmp_path/'a/source/raw/walk/record-0.dat').write_bytes(b'changed')
    with pytest.raises(ValueError,match='bound_input_changed'):execute(plan,tmp_path/'out')
    assert not (tmp_path/'out').exists()


def test_budget_report_and_preload_guard(tmp_path):
    summary=fixture(tmp_path/'a')
    p=prepare(summary,dict(records=1));assert p['omitted_records']==1
    p=prepare(summary,dict(cells=100));assert all('record_cell_budget_before_loading' in r['blockers'] for r in p['records'])
    p=prepare(summary,dict(curves=1));r=execute(p,tmp_path/'out');assert len(r['curves'])==1 and r['omitted_curves']==1


def test_resume_uses_checked_outputs(tmp_path):
    summary=fixture(tmp_path/'a'); jobs=[dict(summary=str(summary))]
    first=run(jobs,tmp_path/'batch');second=run(jobs,tmp_path/'batch',resume=True)
    assert first['entries'][0]['counts']['curves']==2
    assert second['entries'][0]['cache_reused']
    assert first['report']!=second['report']
    output=Path(first['entries'][0]['output']);(output/'processing/curves.csv').write_text('changed')
    third=run(jobs,tmp_path/'batch',resume=True)
    assert third['entries'][0]['status']=='failed' and 'cached_artifact_changed' in third['entries'][0]['error']


def test_failure_isolated_and_retriable(tmp_path):
    summary=fixture(tmp_path/'a');missing=tmp_path/'missing.json'
    jobs=[dict(summary=str(missing)),dict(summary=str(summary))]
    first=run(jobs,tmp_path/'batch')
    assert first['status']=='partial' and first['entries'][1]['counts']['curves']==2
    missing.write_text(summary.read_text())
    second=run(jobs,tmp_path/'batch',resume=True)
    assert second['status']=='completed-with-holds' and second['entries'][1]['cache_reused']


def test_semantic_human_hold_reaches_plan(tmp_path):
    from harmo.semantic_review import prepare as packet, apply
    summary=fixture(tmp_path/'a'); packet(summary,tmp_path/'review')
    path=tmp_path/'review/decisions.json'; d=json.loads(path.read_text()); d['reviewer']='Synthetic test reviewer'
    for e in d['decisions']:
        if e['target_id'].endswith(':task'):e.update(action='hold',scope_reason='Synthetic unresolved protocol')
    path.write_text(json.dumps(d));apply(tmp_path/'review/packet.json',path,tmp_path/'receipt')
    plan=prepare(summary,semantic_review=tmp_path/'receipt/review.receipt.json')
    assert all(r['status']=='held' for r in plan['records'])
    assert all('document_or_explicit_review_conflict' in r['blockers'] for r in plan['records'])


def test_semantic_confirmation_enables_previously_held_processing(tmp_path):
    from harmo.semantic_review import prepare as packet, apply
    summary=fixture(tmp_path/'a')
    # Replace long muscle labels with ambiguous TA; only a named human review
    # can attach the source declaration to this label in this fixture.
    for header in (tmp_path/'a/source/raw').rglob('*.hea'):
        header.write_text(header.read_text().replace('TIB.ANT.', 'TA'))
    for directory in (tmp_path/'a').glob('record-*'):
        path=directory/'recording.bundle.json';b=json.loads(path.read_text());b['channels'][0]['name']='RT TA';path.write_text(json.dumps(b))
        path=directory/'record.json';m=json.loads(path.read_text());m['metadata']['signals'][0]['label']='RT TA'
        for relative in m['metadata']['source_sha256']:
            m['metadata']['source_sha256'][relative]=sha(tmp_path/'a/source/raw'/relative)
        path.write_text(json.dumps(m))
    assert all(r['status']=='held' for r in prepare(summary)['records'])
    packet(summary,tmp_path/'review');path=tmp_path/'review/decisions.json'
    d=json.loads(path.read_text());d['reviewer']='Synthetic test reviewer'
    values=dict(sensor_type='surface-emg',anatomy='tibialis anterior',measurement_side='right')
    for e in d['decisions']:
        field=e['target_id'].split(':')[-1]
        if ':ch-0:' in e['target_id'] and field in values:
            e.update(action='confirm',value=values[field],evidence_ids=['p0','header'],scope_reason='Synthetic exact TA/source mapping')
    path.write_text(json.dumps(d));apply(tmp_path/'review/packet.json',path,tmp_path/'receipt')
    plan=prepare(summary,semantic_review=tmp_path/'receipt/review.receipt.json')
    assert len(execute(plan,tmp_path/'processed')['curves'])==2


def test_duplicate_and_private_urls_rejected_before_writes(tmp_path):
    for jobs in ([dict(url='http://127.0.0.1/x')], [dict(summary='x'),dict(summary='x')], [dict(url='https://physionet.org/content/x/',summary='x')]):
        with pytest.raises(ValueError):checked_jobs(jobs)
    with pytest.raises(ValueError):run([dict(summary='x')],tmp_path/'out',local_llm_model='a-cloud-model')
    assert not (tmp_path/'out').exists()


def test_main_CLI_is_connected(tmp_path):
    from harmo.cli import main
    summary=fixture(tmp_path/'a');jobs=tmp_path/'jobs.json';jobs.write_text(json.dumps([dict(summary=str(summary))]))
    with pytest.raises(SystemExit) as status:main(['harmonize-batch','--jobs',str(jobs),'--output',str(tmp_path/'batch')])
    assert status.value.code==2
    assert (tmp_path/'batch/batch-report-v001.json').exists()
