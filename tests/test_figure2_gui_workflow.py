import hashlib
import json
from pathlib import Path
from zipfile import ZipFile
import xml.etree.ElementTree as ET

ROOT = Path(__file__).resolve().parents[1]
STEM = 'figure2_implementation_gui_v015_2026.09.16-r4'
OUT = ROOT / 'paper/figures/candidates' / STEM


def test_registered_implementation_preserved():
    old = json.loads((ROOT/'tmp/figure2_pptx_v012/scene.json').read_text())
    new = json.loads((ROOT/'tmp'/STEM/'scene.json').read_text())
    title = 'Registered EMG reuse: processing and evidence-based reassessment'
    expected = [o for o in old['objects'] if o.get('text') != title]
    assert new['objects'][:len(expected)] == expected


def test_evidence_and_gui_bounds():
    evidence = json.loads((OUT/(STEM+'_evidence.json')).read_text())
    assert evidence['status'] == 'candidate_not_adopted'
    assert not evidence['human_approval_submitted']
    assert evidence['scientific_comparison_on_hold']
    assert set(evidence['panels']) == set('BCDE')
    for source in evidence['sources']:
        assert hashlib.sha256((ROOT/source['path']).read_bytes()).hexdigest() == source['sha256']
    assert {c['source'] for c in evidence['crops']} == {
        'review_screen_source.png', 'processing_screen_source.png',
        'comparison_screen_source.png', 'library_screen_source.png'}
    for crop in evidence['crops']:
        x, y, w, h = crop['destination_mm']
        assert x >= 5 and x+w <= 277 and y >= 244 and y+h < 335


def test_editable_labels_and_caption_scope():
    caption = (OUT/(STEM+'_caption.md')).read_text()
    for label in ['(A)', '(B)', '(C)', '(D)', '(E)']:
        assert label in caption
    assert 'post-execution' in caption and 'remain on hold' in caption
    with ZipFile(OUT/(STEM+'_editable_v002.pptx')) as archive:
        root = ET.fromstring(archive.read('ppt/slides/slide1.xml'))
        values = [e.text for e in root.findall('.//{http://schemas.openxmlformats.org/drawingml/2006/main}t')]
        assert all(values.count(label) == 1 for label in 'ABCDE')


def test_manuscript_not_silently_adopted():
    assert (ROOT/'paper/versions/harmo-semg_manuscript_v015_2026.09.16-r3.md').is_file()
