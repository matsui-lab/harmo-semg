"""Recompute the stored timing summary and bootstrap from archived participant data."""
import importlib.util
import json
from pathlib import Path
import numpy as np

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location('processing', ROOT/'scripts/paper/scientific_reuse_common_processing_v001.py')
M = importlib.util.module_from_spec(spec)
spec.loader.exec_module(M)
r = json.loads((ROOT/'paper/results/scientific_reuse_common_processing_v001_2026.09.28-r1/report.json').read_text())
protocol = M.load_physiology_protocol(ROOT/'paper/physiology_positive_control_protocol.yaml')
muscles = r['muscles']
left = {}
for row in r['lencioni_curves']:
    left[(row['subject_id'], row['muscle'])] = M._subject_metric(row, protocol['muscles'][row['muscle']])[M.CENTROID]
right = {(row['participant'], row['muscle']): row for row in r['gait120_participants']}
ls = [str(p) for p in protocol['required_studies']['study_38']['subjects']]
rs = protocol['required_studies']['study_120']['subjects']
assert len(ls) == len(rs) == 10
actual = M.summarize([[left[p, m] for m in muscles] for p in ls],
    [[[right[p, m]['metrics'][c][M.CENTROID] for m in muscles] for c in r['conditions']] for p in rs],
    iterations=r['bootstrap']['iterations'], seed=r['bootstrap']['seed'])
def compare(a, b):
    if isinstance(a, dict):
        assert a.keys() == b.keys()
        for key in a: compare(a[key], b[key])
    else:
        np.testing.assert_allclose(a, b, rtol=0, atol=1e-12)
compare(actual, r['summary'])
print(json.dumps({'status': 'passed', 'participants': 20, 'bootstrap_iterations': r['bootstrap']['iterations'],
    'study_differences': actual['study_differences'],
    'scope': 'Saved participant-data replay, not raw-data reanalysis or independent replication.'}, indent=2))
