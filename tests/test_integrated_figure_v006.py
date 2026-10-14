"""The five-panel candidate uses the frozen acceptance limit, not uncertainty."""
import csv
import hashlib
import json
from pathlib import Path
from statistics import median
from zipfile import ZipFile
import xml.etree.ElementTree as ET

ROOT = Path(__file__).resolve().parents[1]
BASE = ROOT / 'paper/figures/candidates'
OLD = 'figure3_integrated_results_v005_2026.09.16-r5'
NEW = 'figure3_integrated_results_v006_2026.09.16-r6'

def rows(stem, suffix):
    with (BASE / stem / f'{stem}_{suffix}.csv').open() as stream:
        return list(csv.DictReader(stream))

def test_medians_and_prespecified_limit_match_source():
    source = json.loads((ROOT / 'paper/results/validation/physiology_positive_control.json').read_text())
    bars = rows(NEW, 'E_median_differences')
    assert len(bars) == 2
    for bar, expected in zip(bars, (0.52, 1.17)):
        values = [[r['activation_centroid_percent_gait_cycle'] for r in source['metrics']
                   if r['muscle'] == bar['muscle'] and r['study_id'] == study]
                  for study in ('study_38', 'study_120')]
        assert [len(v) for v in values] == [10, 10]
        delta = abs(median(values[0]) - median(values[1]))
        result = next(r for r in source['cross_study_centroid_concordance'] if r['muscle'] == bar['muscle'])
        assert abs(float(bar['absolute_median_difference_pp']) - delta) < 1e-10
        assert abs(result['absolute_cross_study_difference_percent'] - delta) < 1e-10
        assert round(delta, 2) == expected
        assert float(bar['prespecified_limit_pp']) == result['threshold_percent'] == 15
        assert delta < result['threshold_percent']

def test_retained_science_and_source_hashes():
    for suffix in ['A_data.csv', 'B_data.csv', 'C_data.csv', 'D_data.csv', 'D_table.json']:
        assert (BASE / NEW / f'{NEW}_{suffix}').read_bytes() == (BASE / OLD / f'{OLD}_{suffix}').read_bytes()
    fields = ('study', 'subject', 'muscle', 'centroid', 'pass')
    new = [{k:r[k] for k in fields} for r in rows(NEW, 'E_data')]
    old = [{k:r[k] for k in fields} for r in rows(OLD, 'E_data')]
    assert new == old and len(new) == 40
    assert sum(r['pass'] == 'FALSE' for r in new) == 2
    evidence = json.loads((BASE / NEW / f'{NEW}_evidence.json').read_text())
    assert evidence['checks']['panels'] == list('ABCDE')
    for source in evidence['sources']:
        assert hashlib.sha256((ROOT / source['path']).read_bytes()).hexdigest() == source['sha256']

def test_editable_limit_and_f_removed_with_body_retained():
    ns = {'p':'http://schemas.openxmlformats.org/presentationml/2006/main',
          'a':'http://schemas.openxmlformats.org/drawingml/2006/main'}
    with ZipFile(BASE / NEW / f'{NEW}_editable.pptx') as archive:
        slide = ET.fromstring(archive.read('ppt/slides/slide1.xml'))
    texts = [t.text or '' for t in slide.findall('.//a:t', ns)]
    joined = ' '.join(texts)
    assert len(slide.findall('.//a:tbl', ns)) == 1
    assert not slide.findall('.//p:pic', ns)
    assert 'Prespecified limit: 15 pp' in joined
    assert '0.52' in joined and '1.17' in joined
    assert 'F' not in texts and 'Mechanics boundary' not in joined
    assert all(letter in texts for letter in 'ABCDE')
    caption = (BASE / NEW / f'{NEW}_caption.md').read_text()
    assert '(F)' not in caption and 'not measurement uncertainty' in caption
    body = (ROOT / 'paper/paper.md').read_text()
    assert 'Missing calibration evidence triggered mechanics denial' in body
    assert '0/330 were fully assessed' in body
