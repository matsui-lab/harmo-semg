import hashlib
import json
import re
import zipfile
from pathlib import Path

ROOT=Path(__file__).resolve().parents[1]
STEM='figure2_implementation_gui_v016_2026.09.16-r5'
VERSION='v016_2026.09.16-r4'


def test_selected_v016_not_v017_and_hashes():
    sel=json.loads((ROOT/'paper/figures/FIGURE2_SELECTION.json').read_text())
    assert sel['version']=='v016_2026.09.16-r5'
    assert sel['manuscript_version']==VERSION
    for item in [sel['source'],sel['caption'],*sel['exports']]:
        data=(ROOT/item['path']).read_bytes()
        assert len(data)==item['bytes']
        assert hashlib.sha256(data).hexdigest()==item['sha256']
    source=ROOT/'paper/figures/candidates'/STEM/(STEM+'.pdf')
    assert source.read_bytes()==(ROOT/'paper/cmpb/Fig2.pdf').read_bytes()
    assert hashlib.sha256(source.read_bytes()).hexdigest()=='95de29389d73e8a250af40b2e44fea277b03d2da787ae1763cf440b97039ddd5'


def test_archive_and_current_snapshots():
    archive=ROOT/'paper/figures/archive/figure2_v012_2026.09.16-r1'
    old=json.loads((archive/'FIGURE2_SELECTION.json').read_text())
    for item in [old['caption'],*old['exports']]:
        p=archive/Path(item['path']).name
        if not p.exists(): p=ROOT/item['path']
        assert hashlib.sha256(p.read_bytes()).hexdigest()==item['sha256']
    for ext,path in [('md','paper/paper.md'),('tex','paper/cmpb/manuscript.tex'),('pdf','paper/cmpb/manuscript.pdf')]:
        assert (ROOT/'paper/versions'/f'harmo-semg_manuscript_{VERSION}.{ext}').read_bytes()==(ROOT/path).read_bytes()


def test_result_values_unchanged_except_references():
    before=(ROOT/'paper/versions/harmo-semg_manuscript_v015_2026.09.16-r3.md').read_text()
    after=(ROOT/'paper/paper.md').read_text()
    assert before.split('# Methods')[0]==after.split('# Methods')[0]
    expected=before.split('# Results',1)[1].replace('Figure 2 traces','Figure 2A traces').replace(
        'The real-data GUI replay processed','The real-data GUI replay illustrated in Figure 2B--E processed')
    assert expected==after.split('# Results',1)[1]


def test_captions_references_and_submission_package():
    caption=(ROOT/'paper/figures/figure2_caption.md').read_text().strip()
    registry=(ROOT/'paper/figures/README.md').read_text()
    assert re.search(r'^## Figure 2\..*?(?=^## Figure 3\.)',registry,re.M|re.S)[0].strip()==caption
    assert 'No human confirmations' in caption and 'comparisons remain held' in caption
    assert 'Figure 6' in caption and 'Figure 3E' not in caption
    tex=(ROOT/'paper/cmpb/manuscript.tex').read_text()
    assert tex.count(r'\label{fig:2}')==1 and 'Figure 2 (continued)' in tex
    with zipfile.ZipFile(ROOT/'paper/cmpb/cmpb_submission.zip') as z:
        assert z.read('Fig2.pdf')==(ROOT/'paper/cmpb/Fig2.pdf').read_bytes()
    manifest=json.loads((ROOT/'paper/cmpb/manifest.json').read_text())
    for source in manifest['sources']:
        assert hashlib.sha256((ROOT/source['path']).read_bytes()).hexdigest()==source['sha256']
