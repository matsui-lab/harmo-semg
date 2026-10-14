"""Validate catalog-planner executions before registered comparison or export."""
import json
import tempfile
from pathlib import Path
import numpy as np
from harmo.timing_planner import execute, sha

RELEASE='catalog-timing-execution/1'


def checked_execution(path, expected_hash):
    path=Path(path)
    if sha(path)!=expected_hash:raise ValueError('execution_receipt_changed')
    receipt=json.loads(path.read_text())
    samples=path.parent/'timing.npz'
    if sha(samples)!=receipt['output_sha256']:raise ValueError('execution_output_changed')
    with tempfile.TemporaryDirectory(prefix='harmo-plan-check-') as temp:
        rebuilt=execute(receipt['plan'],Path(temp)/'recomputed')
        if rebuilt['trace']!=receipt['trace']:raise ValueError('execution_trace_changed')
        with np.load(samples,allow_pickle=False) as saved, np.load(Path(temp)/'recomputed/timing.npz',allow_pickle=False) as fresh:
            if set(saved.files)!=set(fresh.files) or any(not np.array_equal(saved[k],fresh[k]) for k in fresh.files):
                raise ValueError('execution_output_not_reproduced')
            return saved['values'].copy()


def verify_lineage(path):
    lineage=json.loads(Path(path).read_text())
    if lineage['version']!=RELEASE or len(lineage['executions'])!=10:
        raise ValueError('invalid_subject_execution_lineage')
    for item in lineage['executions']:checked_execution(item['path'],item['sha256'])


def verified_rows(replay, reference_rows, output):
    replay=Path(replay).resolve();output=Path(output).resolve()
    summary=json.loads((replay/'summary.json').read_text())
    if len(summary['plans'])!=100:raise ValueError('expected_100_plans')
    grouped={};lineages={};seen=set()
    for item in summary['plans']:
        key=(item['subject'],item['muscle']);identity=(item['record_id'],item['channel_id'])
        if identity in seen:raise ValueError('duplicate_execution')
        seen.add(identity)
        path=replay/(identity[0]+'-'+identity[1])/'execution/execution.json'
        digest=sha(path);receipt=json.loads(path.read_text());spec=receipt['plan']['input']
        if (spec['catalog_origin']['record_id'],spec['catalog_origin']['channel_id'])!=identity or spec['facts']['muscle']['value']!=item['muscle']:
            raise ValueError('execution_identity_conflict')
        values=checked_execution(path,digest)
        grouped.setdefault(key,[]).append(values)
        lineages.setdefault(item['subject'],[]).append(dict(path=str(path),sha256=digest))
    expected={(r['subject_id'],r['muscle']) for r in reference_rows}
    if set(grouped)!=expected:raise ValueError('subject_muscle_denominator_changed')
    result=[]
    for row in reference_rows:
        key=(row['subject_id'],row['muscle']);cycles=grouped[key]
        if len(cycles)!=5:raise ValueError('expected_five_cycles')
        values=np.median(np.vstack(cycles),axis=0)
        if not np.array_equal(values,row['values']):raise ValueError('planned_curve_differs_from_registered_reference')
        result.append(dict(row,values=values.tolist()))
    output.mkdir(parents=True,exist_ok=False)
    for subject,executions in lineages.items():
        path=output/(subject+'.json')
        path.write_text(json.dumps(dict(version=RELEASE,executions=executions),indent=2))
        for row in result:
            if row['subject_id']==subject:row['execution_provenance']=str(path)
    return result
