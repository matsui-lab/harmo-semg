"""Keep acquisition, source derivation, candidate coverage and permission separate."""
import argparse
from collections import Counter
import hashlib
import json
from pathlib import Path


def main():
    p=argparse.ArgumentParser();p.add_argument('root',type=Path);p.add_argument('llm',type=Path);p.add_argument('rules',type=Path)
    p.add_argument('--manifest-name',default='final.source.manifest.json')
    a=p.parse_args();root=a.root.resolve();rows=[]
    for slug in ('taichidb','grabmyo','hd-semg'):
        folder=root/slug;sources=list(folder.glob('workflow/runs/*/automatic.summary.json'));assert len(sources)==1
        original=json.loads(sources[0].read_text());entry=original['intake']['entries'][0]
        final=json.loads((folder/'final-assessment/assessment.json').read_text())
        conversions=[json.loads(c.read_text()) for c in folder.rglob('conversion.json')]
        checks=[c for record in conversions for c in record['calibration_checks']]
        packet=json.loads((folder/'semantic-review/packet.json').read_text())
        rows.append(dict(dataset=slug,converted_records=entry['converted'],waveform_shapes=[r['shape'] for r in conversions],
            listing_pages=len(entry['details']['selection']['visited_directories']),
            full_inventory=entry['details']['selection']['full_inventory'],index_used=bool(entry['details']['selection']['index_path']),
            linked_documents=len(original['evidence']['documents']),
            filled_metadata_fields=sum(d['status']=='filled' for r in final['records'] for d in r.get('attribute_resolution',{}).get('decisions',[])),
            comparison_jobs=len(final['jobs']),channel_purpose_evaluations=sum(len(j.get('result',{}).get('entries',[])) for j in final['jobs']),
            exported=final['exported'],record_reasons=[dict(name=r['name'],reasons=r['reasons']) for r in final['records']],
            adc_reconstruction_all=all(c['original_integer_codes_reconstructed'] for c in checks),
            roundoff_only_channels=sum(not c['exact'] for c in checks),max_adc_step_error=max(c['max_error_ADC_steps'] for c in checks),
            review_items=sum(len(c['targets']) for c in packet['cases']),review_channel_fields_omitted=packet['omitted_channel_fields'],
            actual_human_reviews=0,human_time_seconds=None))
    llm=json.loads(a.llm.read_text());rules=json.loads(a.rules.read_text())
    counts=Counter();proposals=[]
    for item in llm['rows']:
        validation=item.get('validation',{})
        if not validation.get('structurally_complete'):counts['invalid_or_failed']+=1;continue
        candidate=validation['entries'][0]['candidate'];counts[candidate['status']]+=1
        proposals.append(dict(target_id=item['target_id'],status=candidate['status'],value=candidate['value']))
    report=dict(version='general-use-development/003',held_out=False,rows=rows,
                llm=dict(model=llm['model'],targets=len(llm['rows']),counts=dict(counts),
                         rule_proposed=sum(r['status']=='proposed' for r in rules),proposals=proposals,
                         wall_seconds=sum(r.get('wall_seconds',0) for r in llm['rows']),accuracy_evaluated=False,
                         automatic_approvals=0,human_time_measured=False),
                scope='Development re-test; zero human approvals and zero scientific output permissions. Candidate coverage is not accuracy.')
    target=root/'development.summary.json'
    if target.exists():assert json.loads(target.read_text())==report
    else:
        with target.open('x') as stream:json.dump(report,stream,indent=2)
    manifest={str(p):hashlib.sha256(p.read_bytes()).hexdigest()
              for parent in ('src','schemas') for p in sorted(Path(parent).rglob('*'))
              if p.is_file() and '__pycache__' not in p.parts and p.suffix!='.pyc'}
    if Path(a.manifest_name).name!=a.manifest_name:raise ValueError('manifest_filename_required')
    with (root/a.manifest_name).open('x') as stream:json.dump(manifest,stream,indent=2)
    print(json.dumps(report,indent=2))


if __name__=='__main__':main()
