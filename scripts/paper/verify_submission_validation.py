"""Check saved fixed-revision results without rerunning or modifying experiments."""
import hashlib
import json
from pathlib import Path
import xml.etree.ElementTree as ET

ROOT = Path(__file__).resolve().parents[2]
OUT = ROOT/'paper/results/submission_validation_v001_2026.09.13-r1'
def read(path):
    return json.loads(path.read_text())
def stable(value):
    if isinstance(value,dict):
        return {k:stable(v) for k,v in value.items() if k not in ('created_at','generated_at','decided_at','decision_id')}
    if isinstance(value,list): return [stable(v) for v in value]
    return value

def main():
    receipt = read(OUT/'regression/receipt.json')
    corpus = read(OUT/'corpus/corpus_execution.json')
    compare = read(OUT/'corpus/frozen_comparison.json')
    timing = read(OUT/'timing_snapshot.json')
    previous = read(ROOT/'paper/results/bmhi01_timing_v001_2026.09.13-r1/linux_snapshot.json')
    live = read(OUT/'live_resume_comparison.json')
    additional = ROOT/'paper/results/additional_framework_v001_2026.09.12-r1'
    summary = read(additional/'SUMMARY_v002.json')
    repeated = []
    for row in summary['repeated']:
        left = additional/row['left_run']/row['file']
        right = additional/row['right_run']/row['file']
        repeated.append(dict(file=row['file'],
            stored_hashes_match=hashlib.sha256(left.read_bytes()).hexdigest()==row['left_sha256'] and
                                hashlib.sha256(right.read_bytes()).hexdigest()==row['right_sha256'],
            stable_equal=stable(read(left))==stable(read(right))))
    assertions = dict(installed_regression=receipt['pass'] and receipt['mode']=='installed',
        fixed_commit=receipt['commit']==corpus['environment']['code_commit']=='44a21006d54148b340dc485aa201deb4549094e7',
        full_corpus=corpus['pass'] and corpus['mapping_denominator']==78 and compare['differences']==[] and compare['compared_mappings']==78,
        timing_sources_equal=timing['sources']==previous['sources'],
        timing_decisions_equal=timing['decisions']==previous['decisions'],
        timing_arrays_equal=timing['signals']==previous['signals'],
        physiological_control_equal=timing['control']==previous['control'],
        live_resume=live['pass_'] and live['records']==126 and live['second']['downloaded_payloads']==0 and live['second']['ingestion']['inserted']==0,
        additional_repeated=all(r['stored_hashes_match'] and r['stable_equal'] for r in repeated))
    result = dict(assertions=assertions,passed=all(assertions.values()),
        test_count=len(list(ET.parse(OUT/'regression/tests.xml').iter('testcase'))),
        additional_comparisons=repeated,
        scope='Saved-artifact verification; not new independent scientific validation.')
    target = OUT/'final_consistency.json'
    if target.exists(): raise FileExistsError(target)
    target.write_text(json.dumps(result,indent=2))
    print(json.dumps(result,indent=2))
    raise SystemExit(0 if result['passed'] else 1)

if __name__ == '__main__': main()
