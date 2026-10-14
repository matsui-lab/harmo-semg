#!/usr/bin/env python3
"""Compose a provenance-explicit candidate from verified case assets."""
import hashlib
import json
import subprocess
from pathlib import Path
from reportlab.pdfbase import pdfmetrics
from reportlab.pdfbase.ttfonts import TTFont
from pypdf import PdfReader, PdfWriter, Transformation
import generate_implementation_figure as b

ROOT = b.ROOT
PREV = ROOT / 'paper/figures/candidates/implementation_real_waveforms_v007_2026.09.12-r4'
STEM = 'implementation_source_flow_v008_2026.09.12-r5'
OUT = ROOT / 'paper/figures/candidates' / STEM
b.W, b.H, b.Y_SCALE = 240, 180, 1
BLUE, PEACH = '#A8CADB', '#EDCAA5'

def text(d, x, y, s, size=8.5, bold=False):
    d.text(x, y, s, size, bold)

def arrow(d, x, y, xx, yy):
    d.line(x, y, xx, yy, width=.3, arrow=True)

def frame(d, x, y, w, h, colour=None, dashed=False):
    d.box(x, y, w, h, dashed=dashed, width=.25)
    if colour:
        d.line(x, y, x+w, y, colour=colour, width=.8)

def draw(d):
    text(d, 5, 7, 'From public EMG records to purpose-specific reuse', 11, True)
    text(d, 5, 16, 'PUBLIC FILES: fields + signals', 9, True)
    text(d, 96, 16, 'ANALYST-SPECIFIED RULES', 9, True)
    text(d, 173, 16, 'CALCULATED OUTPUTS', 9, True)

    frame(d, 5, 22, 80, 51, BLUE)
    text(d, 8, 27, 'Study 38 / Subject1.mat', 9, True)
    text(d, 8, 32, 'EmgVarName: Tibialis Anterior', 8)
    text(d, 8, 36, 'EMGSide: RX; EMGFreq: 1000 Hz', 8)
    # Real source plot at (11, 39), scaled from the existing ggplot2 PDF.
    frame(d, 95, 26, 65, 43, dashed=True)
    text(d, 98, 31, 'Select and process', 9, True)
    for y, s in [(37, 'Walking; Foot = EMGSide'),
                 (42, 'Select first 5 eligible cycles'),
                 (48, '20-350 Hz; rectify; Hampel'),
                 (53, 'RMS 125 ms; cycle-max'),
                 (58, '101-point phase; cycle median')]:
        text(d, 98, y, s)
    text(d, 174, 30, 'Study 38: computed from 5 cycles', 8.5, True)
    arrow(d, 86, 50, 94, 50)
    arrow(d, 161, 50, 170, 50)

    frame(d, 5, 79, 80, 51, PEACH)
    text(d, 8, 84, 'Gait120 / S011/EMG/ProcessedData.mat', 9, True)
    text(d, 8, 89, 'Column: TibialisAnterior', 8)
    text(d, 8, 93, 'LevelWalking: 10 tables, 101 points each', 8)
    frame(d, 95, 84, 65, 43, dashed=True)
    text(d, 98, 89, 'Read and summarize', 9, True)
    for y, s in [(96, 'Use 10 source-accepted cycles'),
                 (102, 'Retain the released envelope'),
                 (108, 'Cycle-max; 101-point phase'),
                 (114, 'Median across cycles')]:
        text(d, 98, y, s)
    text(d, 174, 87, 'Gait120: computed from 10 cycles', 8.5, True)
    arrow(d, 86, 107, 94, 107)
    arrow(d, 161, 107, 170, 107)

    # Source kinds are written on every input, not encoded only by colour.
    frame(d, 5, 135, 72, 22)
    text(d, 8, 140, 'SOURCE ARTICLE / Gait120', 8.5, True)
    text(d, 8, 146, '"different muscles of the right leg"', 8.5)
    text(d, 8, 152, 'Matches configured side: right', 8)
    frame(d, 84, 135, 76, 22, dashed=True)
    text(d, 87, 140, 'ANALYST-SPECIFIED PURPOSE', 8.5, True)
    text(d, 87, 146, 'Right tibialis anterior: activation timing', 8.5)
    text(d, 87, 152, 'Study 38 participant 1 / Gait120 S011', 8)
    frame(d, 167, 135, 68, 22)
    text(d, 170, 140, 'PRIOR PHYSIOLOGICAL TEST', 8.5, True)
    text(d, 170, 146, 'Protocol + previously passing result', 8.5)
    text(d, 170, 152, 'Supports processing compatibility', 8)
    for x in (41, 122, 201):
        arrow(d, x, 157.5, x, 162)
    # Curves are inputs to qualification, not sufficient evidence on their own.
    d.line(234, 57, 238, 57)
    d.line(234, 114, 238, 114)
    d.line(238, 57, 238, 168)
    arrow(d, 238, 168, 235.5, 168)
    frame(d, 5, 163, 230, 12)
    text(d, 8, 168, 'SAVED RE-EVALUATION: conditional -> timing comparison allowed', 9, True)
    text(d, 8, 172.5, 'Processing compatibility established with linked evidence; amplitude comparability not established.', 8)
    text(d, 5, 179, 'Actual data; grey = individual cycles, colour = median. Literature and decisions: saved records, not new validation.', 7.5)

def main():
    OUT.mkdir(parents=True, exist_ok=True)
    for name, file in [('Arial', 'Arial.ttf'), ('Arial-Bold', 'Arial Bold.ttf')]:
        pdfmetrics.registerFont(TTFont(name, '/System/Library/Fonts/Supplemental/' + file))
    evidence = json.loads((PREV / (PREV.name + '_evidence.json')).read_text())
    for item in evidence['sources']:
        assert hashlib.sha256((ROOT/item['path']).read_bytes()).hexdigest() == item['sha256']
    case = json.loads((PREV/'extracted_case.json').read_text())
    assert len(case['study38']['selected']) == 5 and case['gait120']['cycle_count'] == 10
    assert all(c['shape'][0] == 101 for c in case['gait120']['cycles'])
    d = b.Drawing(OUT/f'{STEM}_layout.pdf')
    draw(d)
    d.finish(OUT/f'{STEM}_layout.svg')
    page = PdfReader(OUT/f'{STEM}_layout.pdf').pages[0]
    scale = 64/55
    plot_h = 28*scale
    for name, x, y in [('source38', 11, 39), ('source120', 11, 96),
                       ('median38', 171, 39), ('median120', 171, 96)]:
        chart = PdfReader(PREV/f'{name}.pdf').pages[0]
        page.merge_transformed_page(chart, Transformation().scale(scale).translate(x*b.MM, (b.H-y-plot_h)*b.MM))
    writer = PdfWriter()
    writer.add_page(page)
    writer.add_metadata({'/Title': 'Sources, specified rules and computed EMG outputs', '/Author': 'Yusuke Matsui'})
    with (OUT/f'{STEM}.pdf').open('wb') as f:
        writer.write(f)
    subprocess.run(['pdftoppm', '-singlefile', '-r', '350', '-png', str(OUT/f'{STEM}.pdf'), str(OUT/STEM)], check=True)
    inputs = [PREV/'extracted_case.json', PREV/(PREV.name+'_evidence.json'),
              *[PREV/f'{n}.pdf' for n in ['source38', 'source120', 'median38', 'median120']], Path(__file__)]
    (OUT/f'{STEM}_evidence.json').write_text(json.dumps({
        'status': 'candidate_not_adopted', 'version': 'v008_2026.09.12-r5',
        'width_mm': b.W, 'height_mm': b.H,
        'scope': 'Layout revision using previously extracted and verified data; no new scientific validation.',
        'sources': [{'path': str(f.relative_to(ROOT)), 'sha256': hashlib.sha256(f.read_bytes()).hexdigest()} for f in inputs]
    }, indent=2)+'\n')
    print(OUT/f'{STEM}.png')

if __name__ == '__main__':
    main()
