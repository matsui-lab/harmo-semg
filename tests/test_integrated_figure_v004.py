"""Result-table counts and native PowerPoint table match frozen assessments."""
import csv
import hashlib
import json
from pathlib import Path
from zipfile import ZipFile
import xml.etree.ElementTree as ET

ROOT = Path(__file__).resolve().parents[1]
BASE = ROOT / 'paper/figures/candidates'
OLD = 'figure3_integrated_results_v003_2026.09.16-r3'
NEW = 'figure3_integrated_results_v004_2026.09.16-r4'


def test_all_source_data_unchanged():
    for panel in 'ABCDE':
        assert (BASE / OLD / f'{OLD}_{panel}_data.csv').read_bytes() == (
            BASE / NEW / f'{NEW}_{panel}_data.csv').read_bytes()
    evidence = json.loads((BASE / NEW / f'{NEW}_evidence.json').read_text())
    assert evidence['status'] == 'candidate_not_adopted'
    for source in evidence['sources']:
        assert hashlib.sha256((ROOT / source['path']).read_bytes()).hexdigest() == source['sha256']


def test_table_counts_release_permission_not_matching_values():
    table = json.loads((BASE / NEW / f'{NEW}_D_table.json').read_text())
    with (BASE / NEW / f'{NEW}_D_data.csv').open() as stream:
        rows = list(csv.DictReader(stream))
    values = [value for row in table[1:] for value in row[1:]]
    assert values == ['20 / 20', '0 / 20', '20 / 20', '0 / 20', '0 / 20', '0 / 20']
    for value, record in zip(values, rows):
        expected = int(record['n']) if record['release'] == 'allow' else 0
        assert value == f"{expected} / {record['n']}"
    caption = (BASE / NEW / f'{NEW}_caption.md').read_text()
    assert 'conditionally' in caption and 'strict policy denied release' in caption
    assert 'not numerical disagreement' in caption


def test_one_native_table_with_exact_cells():
    ns = {'p': 'http://schemas.openxmlformats.org/presentationml/2006/main',
          'a': 'http://schemas.openxmlformats.org/drawingml/2006/main'}
    with ZipFile(BASE / NEW / f'{NEW}_editable.pptx') as archive:
        slide = ET.fromstring(archive.read('ppt/slides/slide1.xml'))
    tables = slide.findall('.//a:tbl', ns)
    assert len(tables) == 1
    assert len(slide.findall('.//p:pic', ns)) == 0
    cells = [['\n'.join(t.text or '' for t in c.findall('.//a:t', ns))
              for c in row.findall('a:tc', ns)]
             for row in tables[0].findall('a:tr', ns)]
    expected = json.loads((BASE / NEW / f'{NEW}_D_table.json').read_text())
    assert [[s.replace('\n', ' ') for s in row] for row in cells] == [
        [s.replace('\n', ' ') for s in row] for row in expected]
