"""Added E bars are absolute differences of the two study medians."""
import csv
import hashlib
import json
from pathlib import Path
from statistics import median
from zipfile import ZipFile
import xml.etree.ElementTree as ET

ROOT = Path(__file__).resolve().parents[1]
BASE = ROOT / 'paper/figures/candidates'
OLD = 'figure3_integrated_results_v004_2026.09.16-r4'
NEW = 'figure3_integrated_results_v005_2026.09.16-r5'


def test_each_bar_matches_full_precision_source_medians():
    source = json.loads((ROOT / 'paper/results/validation/physiology_positive_control.json').read_text())['metrics']
    with (BASE / NEW / f'{NEW}_E_median_differences.csv').open() as stream:
        bars = list(csv.DictReader(stream))
    assert len(bars) == 2
    for bar, rounded in zip(bars, (0.52, 1.17)):
        values = [[r['activation_centroid_percent_gait_cycle'] for r in source
                   if r['muscle'] == bar['muscle'] and r['study_id'] == study]
                  for study in ('study_38', 'study_120')]
        assert [len(v) for v in values] == [10, 10]
        medians = [median(v) for v in values]
        assert abs(float(bar['study38_median']) - medians[0]) < 1e-10
        assert abs(float(bar['gait120_median']) - medians[1]) < 1e-10
        delta = abs(medians[0] - medians[1])
        assert abs(float(bar['absolute_median_difference_pp']) - delta) < 1e-10
        assert round(delta, 2) == rounded


def test_other_panels_and_all_observations_retained():
    for suffix in ['A_data.csv', 'B_data.csv', 'C_data.csv', 'D_data.csv', 'D_table.json']:
        assert (BASE / NEW / f'{NEW}_{suffix}').read_bytes() == (BASE / OLD / f'{OLD}_{suffix}').read_bytes()
    def data(stem):
        with (BASE / stem / f'{stem}_E_data.csv').open() as stream:
            return [{k: r[k] for k in ('study', 'subject', 'muscle', 'centroid', 'pass')} for r in csv.DictReader(stream)]
    assert data(NEW) == data(OLD)
    assert len(data(NEW)) == 40
    assert sum(r['pass'] == 'FALSE' for r in data(NEW)) == 2
    evidence = json.loads((BASE / NEW / f'{NEW}_evidence.json').read_text())
    for source in evidence['sources']:
        assert hashlib.sha256((ROOT / source['path']).read_bytes()).hexdigest() == source['sha256']


def test_editable_difference_plot_and_existing_native_table():
    ns = {'p': 'http://schemas.openxmlformats.org/presentationml/2006/main',
          'a': 'http://schemas.openxmlformats.org/drawingml/2006/main'}
    with ZipFile(BASE / NEW / f'{NEW}_editable.pptx') as archive:
        slide = ET.fromstring(archive.read('ppt/slides/slide1.xml'))
    text = ' '.join(t.text or '' for t in slide.findall('.//a:t', ns))
    assert len(slide.findall('.//a:tbl', ns)) == 1
    assert len(slide.findall('.//p:pic', ns)) == 0
    assert 'Difference between study medians' in text
    assert '0.52' in text and '1.17' in text
    assert 'Absolute difference (percentage points of gait cycle)' in text
