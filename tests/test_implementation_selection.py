"""Check implementation adoption and protect result content during renumbering."""
import hashlib
import json
import re
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def test_implementation_is_selected_without_redrawing():
    selection = json.loads((ROOT / 'paper/figures/FIGURE2_SELECTION.json').read_text())
    assert selection['figure'] == 2
    assert selection['version'] == 'v011_2026.09.12-r8'
    assert selection['width_mm'] / selection['height_mm'] == 4 / 3
    assert selection['status'] == 'selected_for_manuscript'
    for item in [selection['source'], selection['caption'], *selection['exports']]:
        data = (ROOT / item['path']).read_bytes()
        assert len(data) == item['bytes']
        assert hashlib.sha256(data).hexdigest() == item['sha256']
    pdf = next(item for item in selection['exports'] if item['path'].endswith('.pdf'))
    assert (ROOT / 'paper/cmpb/Fig2.pdf').read_bytes() == (ROOT / pdf['path']).read_bytes()


def test_seven_figures_and_real_source_methods_references():
    paper = (ROOT / 'paper/paper.md').read_text()
    methods = paper.split('# Methods',1)[1].split('# Results',1)[0]
    assert not re.search(r'Figure 2[A-D]', methods)
    for term in ('Europe PMC (PMC12177048)', 'JATS XML', 'serialized it as JSON',
                 'previously passing'):
        assert term in methods
    caption = (ROOT / 'paper/figures/figure2_caption.md').read_text()
    assert 'not a direct article-report input' in caption
    assert 'not new independent validation' in caption
    assert 'synthetic example' not in methods
    captions = (ROOT / 'paper/figures/README.md').read_text()
    assert re.findall(r'^## Figure (\d+)\.', captions, re.M) == list('1234567')
    for n in range(1,8):
        maintained = (ROOT / f'paper/figures/figure{n}_caption.md').read_text().strip()
        section = re.search(rf'^## Figure {n}\..*?(?=^## Figure |\Z)', captions, re.M|re.S)[0].strip()
        assert maintained == section
    tex = (ROOT / 'paper/cmpb/manuscript.tex').read_text()
    assert re.findall(r'\\label\{fig:(\d+)\}', tex) == list('1234567')
    with zipfile.ZipFile(ROOT / 'paper/cmpb/cmpb_submission.zip') as z:
        assert all(f'Fig{n}.pdf' in z.namelist() for n in range(1,8))


def test_results_unchanged_except_figure_numbers():
    previous = (ROOT / 'paper/versions/harmo-semg_manuscript_v004_2026.09.09-r3.md').read_text()
    current = (ROOT / 'paper/paper.md').read_text()
    def results(s):
        return s.split('# Results',1)[1].split('# Discussion',1)[0]
    expected = re.sub(r'(?<=Figure )[2-6]', lambda m: str(int(m[0])+1), results(previous))
    for n in range(6,1,-1):
        expected = expected.replace(f'(figures/figure{n}_',f'(figures/figure{n+1}_')
    added = ('Figure 2 traces the input fields and processing of one tibialis anterior\n'
             'example from this cohort. Its displayed curves illustrate the method; the\n'
             'cohort-level timing outcomes are those reported in Figure 6, not additional\n'
             'evidence of biological equivalence between the two example participants.\n\n')
    assert results(current).count(added) == 1
    assert results(current).replace(added, '') == expected


def test_content_excerpt_values_match_saved_results():
    out = ROOT/'paper/figures/candidates/implementation_content_insets_v011_2026.09.12-r8'
    e = json.loads((out/'verified_excerpts.json').read_text())
    pair = json.loads((ROOT/'paper/results/validation/comparability_resolution_study.json').read_text())['comparisons'][0]
    assert e['decision'] == {k:pair[k] for k in e['decision']}
    assert e['claim'] == {'value':['right'],'scope_level':'channel-group'}
    assert e['fields']['eligible_count'] == 15
    assert len(e['fields']['selected']) == 5
