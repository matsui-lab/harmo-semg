"""Frozen-data and editability checks for the revised six-panel candidate."""
import csv
import hashlib
import json
from pathlib import Path
import xml.etree.ElementTree as ET
from zipfile import ZipFile

ROOT = Path(__file__).resolve().parents[1]
STEM = 'figure3_integrated_results_v002_2026.09.16-r2'
OUT = ROOT / 'paper/figures/candidates' / STEM


def read(path):
    return json.loads(path.read_text())


def rows(panel):
    with (OUT / f'{STEM}_{panel}_data.csv').open() as stream:
        return list(csv.DictReader(stream))


def test_sources_and_candidate_boundary():
    evidence = read(OUT / f'{STEM}_evidence.json')
    assert evidence['status'] == 'candidate_not_adopted'
    assert evidence['boundaries']['new_experiments'] is False
    for source in evidence['sources']:
        assert hashlib.sha256((ROOT / source['path']).read_bytes()).hexdigest() == source['sha256']
    caption = (OUT / f'{STEM}_caption.md').read_text()
    assert 'historical-format inputs' in caption
    assert 'expert-judgment' in caption
    assert 'separate, bounded evaluations' in caption


def test_conversion_coverage_and_representation_totals():
    a, b, c = rows('A'), rows('B'), rows('C')
    assert sum(int(r['n']) for r in a) == 78
    assert len({r['study'] for r in a}) == 8
    assert len({r['format'] for r in a}) == 6
    assert sum(int(r['before']) for r in b) == 24
    assert sum(int(r['after']) for r in b) == 43
    assert [int(r['n']) for r in c] == [30, 0, 2, 26, 34, 4]


def test_decision_panel_matches_all_120_assessments():
    d = rows('D')
    source = read(ROOT / 'paper/results/additional_framework_v001_2026.09.12-r1/run4/T3_support_comparison.json')
    assert len(source) == 120
    assert len(d) == 6
    assert sum(int(r['n']) for r in d) == 120
    for cell in d:
        subset = [r for r in source if r['condition'] == cell['condition']
                  and r['decision']['intended_use'] == cell['purpose']]
        assert len(subset) == int(cell['n']) == 20
        assert all(r['sample_and_time_reference_unchanged'] for r in subset)
        assert {r['detail']['assertion']['status'] for r in subset} == {cell['status']}
        assert {r['decision']['status'] for r in subset} == {cell['release']}
    assert [r['release'] for r in d[:3]] == ['allow', 'deny', 'allow']
    assert all(r['status'] == 'unassessed' and r['release'] == 'quarantine' for r in d[3:])


def test_physiological_values_include_both_pattern_failures():
    source = read(ROOT / 'paper/results/validation/physiology_positive_control.json')['metrics']
    e = rows('E')
    actual = {(r['study'], r['subject'], r['muscle']): r for r in e}
    assert len(e) == len(actual) == len(source) == 40
    for r in source:
        plotted = actual[r['study_id'], r['subject_id'], r['muscle']]
        assert abs(float(plotted['centroid']) - r['activation_centroid_percent_gait_cycle']) < 1e-10
        assert (plotted['pass'] == 'TRUE') == r['pattern_pass']
    assert sum(r['pass'] == 'FALSE' for r in e) == 2
    mechanics = read(ROOT / 'paper/results/validation/opensim_quality_summary.json')
    counts = mechanics['analysis_denominator']['by_speed']
    assert sum(r['frozen_trials'] for r in counts) == 336
    assert sum(r['source_available'] for r in counts) == 330
    assert sum(r['source_absent'] for r in counts) == 6
    assert sum(r['completely_assessed'] for r in counts) == 0


def test_native_editable_text_and_plot_shapes():
    ns = {'p': 'http://schemas.openxmlformats.org/presentationml/2006/main',
          'a': 'http://schemas.openxmlformats.org/drawingml/2006/main'}
    with ZipFile(OUT / f'{STEM}_editable.pptx') as archive:
        slide = ET.fromstring(archive.read('ppt/slides/slide1.xml'))
    assert len(slide.findall('.//p:pic', ns)) == 0
    assert len(slide.findall('.//p:sp', ns)) > 250
    text = ' '.join(t.text or '' for t in slide.findall('.//a:t', ns))
    assert 'Evidence-dependent reuse decisions' in text
    assert 'Release denied' in text and 'Output held' in text
    assert all(t in text for t in ['Retained', 'Withdrawn', 'Restored'])
