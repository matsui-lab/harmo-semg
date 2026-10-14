import csv
import hashlib
import json
from pathlib import Path
from zipfile import ZipFile
import xml.etree.ElementTree as ET

ROOT=Path(__file__).resolve().parents[1]
STEM='figure2_implementation_gui_v016_2026.09.16-r5'
PREV='figure2_implementation_gui_v015_2026.09.16-r4'
OUT=ROOT/'paper/figures/candidates'/STEM
PX=96/25.4


def lower(o):
    if o['kind'] in ('text','image','table'):
        return o['y']
    pts=o['points'] if o['kind']=='path' else [v[1:] for v in o['commands'] if len(v)>1]
    return min(p[1] for p in pts)


def test_gui_scene_preserved_exactly():
    before=json.loads((ROOT/'tmp'/PREV/'scene.json').read_text())
    after=json.loads((ROOT/'tmp'/STEM/'scene.json').read_text())
    gui=lambda scene:[o for o in scene['objects'] if lower(o)>=211*PX-1e-6]
    assert gui(before)==gui(after)
    assert after['curves']==19


def test_primary_route_has_no_vertical_or_backward_steps():
    scene=json.loads((ROOT/'tmp'/STEM/'scene.json').read_text())
    paths=[o for o in scene['objects'] if o.get('name')=='Primary left-to-right route']
    shafts=[o for o in paths if len(o['points'])==2]
    assert len(shafts)==3
    for shaft in shafts:
        a,b=shaft['points']
        assert a[0]<b[0] and a[1]==b[1]
    labels=[o for o in scene['objects'] if o.get('text') in ('1','2','3','4') and o.get('size')==20]
    assert [o['text'] for o in labels]==['1','2','3','4']
    assert len({o['y'] for o in labels})==1


def test_traceable_sources_and_output_values():
    e=json.loads((OUT/(STEM+'_evidence.json')).read_text())
    for s in e['sources']:
        assert hashlib.sha256((ROOT/s['path']).read_bytes()).hexdigest()==s['sha256']
    assert e['registered_scientific_values_unchanged'] and e['gui_layout_unchanged']
    assert not e['human_approval_submitted'] and e['scientific_comparison_on_hold']
    data=ROOT/'paper/figures/candidates/implementation_real_waveforms_v007_2026.09.12-r4/waveforms.csv'
    rows=list(csv.DictReader(data.open()))
    for phase,s38,g120 in e['output_table_values'][1:]:
        for panel,value in [('Study 38: five-cycle median',s38),('Gait120: ten-cycle median',g120)]:
            actual=next(float(r['value']) for r in rows if r['panel']==panel and r['curve']=='median' and float(r['x'])==float(phase))
            assert value==f'{actual:.3f}'


def test_editable_tables_and_labels():
    with ZipFile(OUT/(STEM+'_editable.pptx')) as archive:
        root=ET.fromstring(archive.read('ppt/slides/slide1.xml'))
        ns={'a':'http://schemas.openxmlformats.org/drawingml/2006/main'}
        tables=root.findall('.//a:tbl',ns)
        assert len(tables)==2
        assert all(len(t.findall('a:tblGrid/a:gridCol',ns))==3 for t in tables)
        texts=[t.text for t in root.findall('.//a:t',ns)]
        assert all(texts.count(x)==1 for x in 'ABCDE')
        assert 'Timing use allowed' in texts and 'Amplitude not established' in texts


def test_caption_distinguishes_artwork_and_evidence():
    caption=(OUT/(STEM+'_caption.md')).read_text()
    assert 'does not document' in caption and 'electrode placement' in caption
    assert 'not literal file screenshots' in caption
    assert 'No human approval' in caption
    assert (ROOT/'paper/versions/harmo-semg_manuscript_v015_2026.09.16-r3.md').is_file()
