"""Replay frozen extractions with verified file evidence; no new LLM calls."""
import hashlib
import json
from pathlib import Path
from harmo.unit_reconciliation import reconcile_units,verify_wfdb_unit


def main():
    previous=Path('paper/results/sourcewise_units_v001_2026.09.14-r1/report.json')
    old=json.loads(previous.read_text())
    packet=json.loads(Path('paper/results/llm_transfer_packet_v001_2026.09.14-r1/model_input.json').read_text())
    root=Path('downloads/physionet_batch_v001_2026.09.13-r1/runs/ff2e47a15406490db9a17e5e68f8102e')
    rows=[]
    for ci,version in [(0,'1.0.0'),(1,'1.0.1')]:
        case=packet['cases'][ci]
        claims=[r for r in old['rows'] if r['case_index']==ci and r['field']=='physical_unit']
        source=next(s for s in case['sources'] if s['source_id']=='wfdb-header')
        proof=verify_wfdb_unit(root/case['dataset']/version/'raw'/f'{case["record_name"]}.hea',
            source['source_sha256'],case['selected_channel_index'],case['targets'][3]['channel'],claims[0]['target_id'])
        rows.append(dict(dataset=case['dataset'],claims=claims,
            before=old['reconciliation'][ci],after=reconcile_units(claims,claims[0]['target_id'],['description','header'],file_unit_evidence=proof)))
    assert rows[0]['after']['status']=='file_supported'
    assert rows[1]['after']['status']=='conflict'
    out=Path('paper/results/unit_adoption_policy_v001_2026.09.14-r1');out.mkdir(parents=True,exist_ok=False)
    (out/'report.json').write_text(json.dumps(dict(previous_report_sha256=hashlib.sha256(previous.read_bytes()).hexdigest(),
        model_rerun=False,automatic_approvals=0,scope='Two previously inspected examples; not independent accuracy evaluation.',rows=rows),indent=2,ensure_ascii=False))
    print(json.dumps([{ 'dataset':r['dataset'],'before':r['before']['status'],'after':r['after']['status'],'unit':r['after']['candidate_unit'],'hold':r['after']['hold']} for r in rows]))


if __name__=='__main__':main()
