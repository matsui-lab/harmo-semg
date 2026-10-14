import json
from pathlib import Path
from zipfile import ZipFile
import xml.etree.ElementTree as ET

ROOT=Path(__file__).resolve().parents[1]
OLD='figure2_implementation_gui_v013_2026.09.16-r2'
NEW='figure2_implementation_gui_v014_2026.09.16-r3'
TITLES={'Registered EMG reuse: processing and evidence-based reassessment','Record meaning review in the local browser'}

def test_only_headings_and_labels_changed():
    a=json.loads((ROOT/'tmp'/OLD/'scene.json').read_text())
    b=json.loads((ROOT/'tmp'/NEW/'scene.json').read_text())
    assert a['width']==b['width'] and a['height']==b['height']
    assert [o for o in a['objects'] if o.get('text') not in TITLES] == [o for o in b['objects'] if o.get('text') not in TITLES|{'A','B'}]
    assert [o['text'] for o in b['objects'] if o.get('text') in {'A','B'}]==['A','B']

def test_caption_and_powerpoint_labels():
    folder=ROOT/'paper/figures/candidates'/NEW
    caption=(folder/(NEW+'_caption.md')).read_text()
    assert '(A) The registered example' in caption and '(B) The local review interface' in caption
    with ZipFile(folder/(NEW+'_editable.pptx')) as z:
        root=ET.fromstring(z.read('ppt/slides/slide1.xml'))
        text=[e.text for e in root.findall('.//{http://schemas.openxmlformats.org/drawingml/2006/main}t')]
        assert text.count('A')==text.count('B')==1

def test_original_manuscript_retained():
    assert (ROOT/'paper/versions/harmo-semg_manuscript_v015_2026.09.16-r3.md').is_file()
