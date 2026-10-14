"""Source and manuscript-boundary checks for the three-figure proposal."""
import csv
import hashlib
import json
from pathlib import Path
from zipfile import ZipFile
import xml.etree.ElementTree as ET

ROOT=Path(__file__).resolve().parents[1]
F2='figure2_implementation_gui_v013_2026.09.16-r2'
F3='figure3_integrated_results_v001_2026.09.16-r1'
def folder(stem):return ROOT/'paper/figures/candidates'/stem
def read(p):return json.loads(p.read_text())

def test_sources_remain_identical():
    for stem in (F2,F3):
        evidence=read(folder(stem)/(stem+'_evidence.json'))
        assert evidence['status']=='candidate_not_adopted'
        for s in evidence['sources']:
            assert hashlib.sha256((ROOT/s['path']).read_bytes()).hexdigest()==s['sha256']

def test_all_participant_muscle_values_retained():
    source=read(ROOT/'paper/results/validation/physiology_positive_control.json')['metrics']
    with (folder(F3)/(F3+'_E_data.csv')).open() as f:rows=list(csv.DictReader(f))
    assert len(rows)==len(source)==40
    actual={(r['study'],r['subject'],r['muscle']):r for r in rows}
    assert len(actual)==40
    for r in source:
        q=actual[r['study_id'],r['subject_id'],r['muscle']]
        assert abs(float(q['centroid'])-r['activation_centroid_percent_gait_cycle'])<1e-10
        assert (q['pass']=='TRUE')==r['pattern_pass']
    assert sum(r['pass']=='FALSE' for r in rows)==2

def test_conversion_and_coverage_totals():
    with (folder(F3)/(F3+'_A_data.csv')).open() as f:a=list(csv.DictReader(f))
    with (folder(F3)/(F3+'_B_data.csv')).open() as f:b=list(csv.DictReader(f))
    assert sum(int(r['n']) for r in a)==78
    assert len({r['study'] for r in a})==8
    assert len({r['format'] for r in a})==6
    assert sum(int(r['before']) for r in b)==24
    assert sum(int(r['after']) for r in b)==43

def test_actual_screenshot_crops_and_editable_objects():
    p=folder(F2)/(F2+'_editable_v003.pptx')
    ns={'p':'http://schemas.openxmlformats.org/presentationml/2006/main','a':'http://schemas.openxmlformats.org/drawingml/2006/main'}
    with ZipFile(p) as z:r=ET.fromstring(z.read('ppt/slides/slide1.xml'))
    pics=r.findall('.//p:pic',ns)
    for pic,cy,ch in zip(pics[-3:],(195,846,1008),(83,98,53)):
        crop=pic.find('.//a:srcRect',ns)
        assert int(crop.attrib['l'])==round(182/1056*100000)
        assert int(crop.attrib['t'])==round(cy/1272*100000)
        assert int(crop.attrib['b'])==round((1272-cy-ch)/1272*100000)
    with ZipFile(folder(F3)/(F3+'_editable.pptx')) as z:r=ET.fromstring(z.read('ppt/slides/slide1.xml'))
    assert len(r.findall('.//p:sp',ns))>200
    assert len(r.findall('.//p:pic',ns))==0

def test_manuscript_not_silently_adopted():
    manuscript=(ROOT/'paper/versions/harmo-semg_manuscript_v015_2026.09.16-r3.md').read_text()
    assert F2 not in manuscript and F3 not in manuscript
    assert read(folder(F2)/(F2+'_evidence.json'))['human_approval_submitted'] is False
