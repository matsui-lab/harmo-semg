"""Panel D simplification must preserve every frozen result."""
import hashlib
import json
from pathlib import Path
from zipfile import ZipFile
import xml.etree.ElementTree as ET

ROOT = Path(__file__).resolve().parents[1]
BASE = ROOT / 'paper/figures/candidates'
OLD = 'figure3_integrated_results_v002_2026.09.16-r2'
NEW = 'figure3_integrated_results_v003_2026.09.16-r3'


def test_all_plotted_data_unchanged():
    for panel in 'ABCDE':
        assert (BASE / OLD / f'{OLD}_{panel}_data.csv').read_bytes() == (
            BASE / NEW / f'{NEW}_{panel}_data.csv').read_bytes()
    evidence = json.loads((BASE / NEW / f'{NEW}_evidence.json').read_text())
    assert evidence['status'] == 'candidate_not_adopted'
    assert evidence['checks']['assessments'] == 120
    for source in evidence['sources']:
        assert hashlib.sha256((ROOT / source['path']).read_bytes()).hexdigest() == source['sha256']


def test_simplified_labels_remain_editable():
    ns = {'p': 'http://schemas.openxmlformats.org/presentationml/2006/main',
          'a': 'http://schemas.openxmlformats.org/drawingml/2006/main'}
    with ZipFile(BASE / NEW / f'{NEW}_editable.pptx') as archive:
        slide = ET.fromstring(archive.read('ppt/slides/slide1.xml'))
    text = ' '.join(t.text or '' for t in slide.findall('.//a:t', ns))
    assert len(slide.findall('.//p:pic', ns)) == 0
    assert len(slide.findall('.//p:sp', ns)) > 250
    for label in ['Same 20 waveform pairs in every condition', 'Available',
                  'Removed', 'Restored', 'Timing output', 'Allowed', 'Withheld',
                  'Amplitude pooling: withheld in all three conditions']:
        assert label in text
    assert '\\n' not in text
    assert 'Comparable' not in text and 'Unassessed' not in text


def test_caption_preserves_conditional_state_and_scope():
    caption = (BASE / NEW / f'{NEW}_caption.md').read_text()
    assert 'permission to release' in caption
    assert 'conditionally comparable' in caption
    assert 'unchanged strict policy denied release' in caption
    assert 'historical-format inputs' in caption
    assert 'expert-judgment' in caption
