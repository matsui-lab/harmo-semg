#!/usr/bin/env python3
"""Make acquisition formats and deterministic extraction visible in Fig2."""
import hashlib
import json
import subprocess
from pathlib import Path
from reportlab.pdfbase import pdfmetrics
from reportlab.pdfbase.ttfonts import TTFont
from pypdf import PdfReader, PdfWriter, Transformation
import generate_implementation_figure as b

ROOT = b.ROOT
PREV = ROOT/'paper/figures/candidates/implementation_real_waveforms_v007_2026.09.12-r4'
STEM = 'implementation_sources_formats_v009_2026.09.12-r6'
OUT = ROOT/'paper/figures/candidates'/STEM
b.W, b.H, b.Y_SCALE = 280, 210, 1

def t(d, x, y, s, size=8.5, bold=False):
    d.text(x, y, s, size, bold)

def arrow(d, x, y, xx, yy):
    d.line(x, y, xx, yy, width=.3, arrow=True)

def box(d, x, y, w, h, colour=None, dashed=False):
    d.box(x, y, w, h, dashed=dashed, width=.25)
    if colour:
        d.line(x, y, x+w, y, colour=colour, width=.8)

def draw(d):
    t(d, 5, 8, 'Public EMG reuse: where each input comes from and how it is processed', 12, True)
    for x, title in [(5, 'SOURCE + FILE FORMAT'), (105, 'READ, EXTRACT AND TRANSFORM'), (205, 'GENERATED DATA / METADATA')]:
        t(d, x, 18, title, 9, True)

    box(d, 5, 24, 90, 49, b.BLUE)
    t(d, 8, 29, 'Figshare | MAT', 10, True)
    t(d, 8, 34, 'Study 38: Subject1.mat (file 17544206)', 8.5)
    t(d, 8, 38, 'Tibialis Anterior; EMGSide: RX; EMGFreq: 1000 Hz', 8)
    box(d, 105, 27, 88, 43)
    t(d, 108, 32, 'MAT reader: named channel + cycle fields', 9, True)
    t(d, 108, 38, 'EmgVarName -> EMG row; Task / Foot -> cycles', 8.5)
    d.line(108, 41, 190, 41, dashed=True)
    t(d, 108, 46, 'Protocol (YAML): first 5 same-side Walking cycles', 8.5)
    t(d, 108, 51, '20-350 Hz; rectify; Hampel; RMS 125 ms', 8.5)
    t(d, 108, 56, 'Cycle-max -> 101-point phase -> median', 8.5)
    t(d, 205, 31, 'Study 38 | 101-point curve (CSV)', 9, True)
    arrow(d, 96, 50, 104, 50)
    arrow(d, 194, 50, 203, 50)

    box(d, 5, 80, 90, 49, b.PEACH)
    t(d, 8, 85, 'Figshare | ZIP -> MAT', 10, True)
    t(d, 8, 90, 'Gait120_011_to_020.zip (file 53167643)', 8.5)
    t(d, 8, 94, 'Member: S011/EMG/ProcessedData.mat', 8)
    box(d, 105, 83, 88, 43)
    t(d, 108, 88, 'ZIP member -> MAT table decoder', 9, True)
    t(d, 108, 94, 'LevelWalking / trial / step; TibialisAnterior column', 8.5)
    t(d, 108, 99, '10 tables; each already contains 101 phase points', 8.5)
    d.line(108, 102, 190, 102, dashed=True)
    t(d, 108, 107, 'Protocol (YAML): retain released envelope', 8.5)
    t(d, 108, 112, 'Cycle-max -> 101-point phase -> median', 8.5)
    t(d, 205, 87, 'Gait120 | 101-point curve (CSV)', 9, True)
    arrow(d, 96, 106, 104, 106)
    arrow(d, 194, 106, 203, 106)

    # The actual literature route is Europe PMC fullTextXML, not PubMed abstracts.
    box(d, 5, 137, 90, 24)
    t(d, 8, 142, 'Europe PMC | full-text JATS XML', 10, True)
    t(d, 8, 148, 'PMC12177048 / fullTextXML', 8.5)
    t(d, 8, 154, 'Article section: Experimental preparation', 8.5)
    box(d, 105, 137, 88, 24)
    t(d, 108, 142, 'Parse XML -> select section -> fixed regex', 9, True)
    t(d, 108, 148, 'Match: "different muscles of the right leg"', 8.5)
    t(d, 108, 154, 'Presence rule assigns the predefined value: right', 8.5)
    box(d, 205, 137, 70, 24)
    t(d, 208, 142, 'Article-supported side: right', 9, True)
    t(d, 208, 148, 'Scope: surface-EMG channels', 8.5)
    t(d, 208, 154, 'Retain source, section and matched-text hash', 8)
    for x, xx in [(96, 104), (194, 204)]:
        arrow(d, x, 149, xx, 149)

    box(d, 5, 167, 90, 14)
    t(d, 8, 172, 'Project repository | YAML', 9, True)
    t(d, 8, 178, 'Gait120 channels.yaml: side: Right', 8.5)
    box(d, 105, 167, 88, 14)
    t(d, 108, 172, 'Regex capture -> unique side value', 9, True)
    t(d, 108, 178, 'Compare value and declared scope with article', 8.5)
    box(d, 205, 167, 70, 14)
    t(d, 208, 172, 'Side corroborated', 9, True)
    t(d, 208, 178, 'Linked evidence record (JSON)', 8.5)
    arrow(d, 96, 174, 104, 174)
    arrow(d, 194, 174, 204, 174)
    arrow(d, 239, 161.5, 239, 166.5)

    box(d, 5, 187, 156, 9, dashed=True)
    t(d, 8, 192.8, 'Analyst request (Python -> JSON): pair 1 / S011; right tibialis anterior; activation timing', 8.5)
    box(d, 167, 187, 108, 9)
    t(d, 170, 192.8, 'Prior physiological test (JSON) + linked protocol (YAML)', 8.5)
    arrow(d, 84, 196, 84, 199)
    arrow(d, 221, 196, 221, 199)
    box(d, 5, 199.5, 270, 7)
    t(d, 8, 204.3, 'Saved decision (JSON): processing condition resolved -> timing comparison allowed; amplitude comparability not established', 9, True)
    # Computed curves and linked side evidence also enter the purpose-specific checks.
    for y in (56, 112, 174):
        d.line(275, y, 278, y)
    d.line(278, 56, 278, 203)
    arrow(d, 278, 203, 275.5, 203)
    t(d, 5, 209, 'Real waveforms; grey = cycles, colour = median. Literature extraction and qualification are saved results, not newly executed validation.', 7.5)

def main():
    OUT.mkdir(parents=True, exist_ok=True)
    for name, file in [('Arial', 'Arial.ttf'), ('Arial-Bold', 'Arial Bold.ttf')]:
        pdfmetrics.registerFont(TTFont(name, '/System/Library/Fonts/Supplemental/'+file))
    report = json.loads((ROOT/'paper/results/validation/literature_semantic_enrichment.json').read_text())
    claim = next(c for c in report['claims'] if c['claim_id']=='study120-article-emg-side')
    assert claim['extraction_method']=='frozen-regex:presence' and claim['value']==['right']
    case = json.loads((PREV/'extracted_case.json').read_text())
    assert case['gait120']['cycle_count']==10 and len(case['study38']['selected'])==5
    d = b.Drawing(OUT/f'{STEM}_layout.pdf')
    draw(d)
    d.finish(OUT/f'{STEM}_layout.svg')
    page = PdfReader(OUT/f'{STEM}_layout.pdf').pages[0]
    scale = 64/55
    for name, x, y in [('source38', 17, 40), ('source120', 17, 96), ('median38', 205, 40), ('median120', 205, 96)]:
        chart = PdfReader(PREV/f'{name}.pdf').pages[0]
        page.merge_transformed_page(chart, Transformation().scale(scale).translate(x*b.MM, (b.H-y-28*scale)*b.MM))
    writer = PdfWriter(); writer.add_page(page)
    writer.add_metadata({'/Title': 'EMG data sources, file formats and implemented extraction', '/Author': 'Yusuke Matsui'})
    with (OUT/f'{STEM}.pdf').open('wb') as f:
        writer.write(f)
    subprocess.run(['pdftoppm', '-singlefile', '-r', '350', '-png', str(OUT/f'{STEM}.pdf'), str(OUT/STEM)], check=True)
    inputs = [ROOT/p for p in ['paper/literature_semantic_enrichment.yaml', 'paper/physiology_positive_control_protocol.yaml',
                              'paper/results/validation/literature_semantic_enrichment.json',
                              'paper/results/validation/comparability_resolution_study.json',
                              'src/harmo/evidence/semantic_claims.py', 'src/harmo/physiology_control.py',
                              'scripts/paper/run_comparability_resolution_study.py',
                              'studies/study_120/definitions/channels.yaml']]
    inputs += [PREV/'extracted_case.json', *[PREV/f'{n}.pdf' for n in ['source38','source120','median38','median120']], Path(__file__)]
    (OUT/f'{STEM}_evidence.json').write_text(json.dumps({
        'status': 'candidate_not_adopted', 'version': 'v009_2026.09.12-r6', 'width_mm': b.W, 'height_mm': b.H,
        'scope': 'Source/format/implementation annotations added to previously verified data. No re-execution of literature extraction or qualification.',
        'sources': [{'path': str(f.relative_to(ROOT)), 'sha256': hashlib.sha256(f.read_bytes()).hexdigest()} for f in inputs]
    }, indent=2)+'\n')
    print(OUT/f'{STEM}.png')

if __name__ == '__main__':
    main()
