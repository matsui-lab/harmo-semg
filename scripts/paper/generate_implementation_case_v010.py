#!/usr/bin/env python3
"""Integrated dataflow with acquisition sources attached to their consumers."""
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
PREV9 = ROOT/'paper/figures/candidates/implementation_sources_formats_v009_2026.09.12-r6'
STEM = 'implementation_integrated_flow_v010_2026.09.12-r7'
OUT = ROOT/'paper/figures/candidates'/STEM
b.W, b.H, b.Y_SCALE = 280, 210, 1

def t(d, x, y, s, size=8.5, bold=False):
    d.text(x, y, s, size, bold)

def route(d, points, dashed=False, arrow=True):
    for i, (a, z) in enumerate(zip(points, points[1:])):
        d.line(*a, *z, width=.3, dashed=dashed, arrow=arrow and i==len(points)-2)

def box(d, x, y, w, h, colour=None, dashed=False):
    d.box(x, y, w, h, dashed=dashed, width=.25)
    if colour:
        d.line(x, y, x+w, y, colour=colour, width=.8)

def draw(d):
    t(d, 5, 8, 'From public files and article evidence to a use-specific decision', 12, True)
    t(d, 5, 21, 'PUBLIC RECORDS', 9, True)
    t(d, 149, 21, 'COMPUTED CURVES', 9, True)
    # The analyst protocol enters both numerical processing branches.
    box(d, 73, 15, 65, 12, dashed=True)
    t(d, 76, 20, 'Local protocol | YAML', 9, True)
    t(d, 76, 25, 'Cycle selection, filters and scaling', 8)
    box(d, 70, 30, 71, 100, dashed=True)
    route(d, [(105,27),(105,30)])

    box(d, 5, 30, 57, 45, b.BLUE)
    t(d, 8, 35, 'Figshare | MAT', 9.5, True)
    t(d, 8, 40, 'Study 38 / Subject1.mat', 8.5)
    box(d, 73, 33, 65, 42)
    t(d, 76, 38, 'Read fields; select cycles', 9, True)
    t(d, 76, 44, 'EmgVarName: Tibialis Anterior', 8)
    t(d, 76, 49, 'EMGSide: RX; EMGFreq: 1000 Hz', 8)
    t(d, 76, 54, 'Walking; Foot = EMGSide; first 5', 8)
    d.line(76, 57, 135, 57, dashed=True)
    t(d, 76, 62, '20-350 Hz; rectify; Hampel; RMS', 8)
    t(d, 76, 67, 'Cycle-max; 101-point phase; median', 8)
    box(d, 149, 30, 60, 45, b.BLUE)
    t(d, 152, 36, 'Study 38 | CSV + record JSON', 8.5, True)
    route(d, [(62.5,53),(72,53)])
    route(d, [(138.5,53),(148,53)])

    box(d, 5, 82, 57, 45, b.PEACH)
    t(d, 8, 87, 'Figshare | ZIP > MAT', 9.5, True)
    t(d, 8, 92, 'S011/EMG/ProcessedData.mat', 8)
    box(d, 73, 81, 65, 46)
    t(d, 76, 88, 'Extract member; decode table', 9, True)
    t(d, 76, 94, 'LevelWalking / trial / step', 8)
    t(d, 76, 99, 'Named column: TibialisAnterior', 8)
    t(d, 76, 104, '10 cycles; already 101 points each', 8)
    d.line(76, 107, 135, 107, dashed=True)
    t(d, 76, 112, 'Retain released envelope', 8)
    t(d, 76, 117, 'Cycle-max; 101-point phase; median', 8)
    box(d, 149, 82, 60, 45, b.PEACH)
    t(d, 152, 88, 'Gait120 | CSV + record JSON', 8.5, True)
    route(d, [(62.5,105),(72,105)])
    route(d, [(138.5,105),(148,105)])

    # Two source inputs join at extraction/reconciliation, not a remote legend.
    box(d, 5, 139, 57, 23)
    t(d, 8, 145, 'Europe PMC | JATS XML', 9, True)
    t(d, 8, 151, 'PMC12177048 / fullTextXML', 8)
    t(d, 8, 157, 'Experimental preparation', 8)
    box(d, 5, 174, 57, 17)
    t(d, 8, 180, 'Project repository | YAML', 9, True)
    t(d, 8, 186, 'channels.yaml: side: Right', 8)
    box(d, 73, 138, 65, 53)
    t(d, 76, 144, 'Extract and reconcile', 9, True)
    t(d, 76, 150, 'XML: select section; fixed regex', 8)
    t(d, 76, 155, '"different muscles of the right leg"', 8)
    t(d, 76, 160, 'Presence rule assigns: right', 8)
    d.line(76, 164, 135, 164, dashed=True)
    t(d, 76, 170, 'YAML: capture side: Right', 8)
    t(d, 76, 176, 'Compare value + declared scope', 8)
    t(d, 76, 184, 'Keep source, section and text hash', 8)
    route(d, [(62.5,151),(72,151)])
    route(d, [(62.5,183),(72,183)])
    box(d, 149, 148, 60, 35)
    t(d, 152, 154, 'Linked context | JSON', 9, True)
    t(d, 152, 160, 'Gait120 surface-EMG: right', 8.5)
    t(d, 152, 166, 'Article + configuration agree', 8)
    t(d, 152, 172, 'Side corroborated', 8.5, True)
    t(d, 152, 178, 'Traceable supporting evidence', 8)
    route(d, [(138.5,166),(148,166)])

    # Actual request and validation inputs enter the evaluator directly.
    box(d, 224, 25, 51, 31, dashed=True)
    t(d, 227, 31, 'Analyst | Python > JSON', 8.5, True)
    t(d, 227, 37, 'Right tibialis anterior', 8.5)
    t(d, 227, 43, 'Activation timing', 8.5)
    t(d, 227, 50, 'Pair: participant 1 / S011', 8)
    box(d, 224, 70, 51, 26)
    t(d, 227, 76, 'Prior test | JSON', 9, True)
    t(d, 227, 82, 'Linked protocol | YAML', 8.5)
    t(d, 227, 90, 'Physiological control: passed', 8)
    box(d, 224, 117, 51, 57)
    t(d, 227, 123, 'Evaluate declared use', 9, True)
    t(d, 227, 132, 'Muscle + side', 8.5)
    t(d, 227, 138, 'Gait-phase reference', 8.5)
    t(d, 227, 144, 'Processing compatibility', 8.5)
    d.line(227, 148, 272, 148, dashed=True)
    t(d, 227, 154, 'Link prior validation;', 8.5)
    t(d, 227, 160, 'record processing support;', 8.5)
    t(d, 227, 166, 'repeat comparison checks', 8)
    route(d, [(209.5,54),(216,54),(216,130),(223,130)])
    route(d, [(209.5,106),(216,106)], arrow=False)
    route(d, [(249.5,96),(249.5,116)])
    route(d, [(275,41),(278,41),(278,130),(275.5,130)])
    # Dashed support link is not a direct article-report input to the resolver API.
    route(d, [(209.5,166),(219,166),(219,157),(223,157)], dashed=True)
    box(d, 224, 185, 51, 19)
    t(d, 227, 191, 'Saved decision | JSON', 9, True)
    t(d, 227, 197, 'Timing: allowed', 8.5, True)
    t(d, 227, 202, 'Amplitude: not established', 8)
    route(d, [(249.5,174),(249.5,184)])
    t(d, 5, 201, 'Solid arrows: data / processing inputs. Dashed context link: supporting evidence, not automatic qualification.', 8)
    t(d, 5, 208, 'Actual waveforms; grey = cycles, colour = median. Article extraction and decisions are saved results, not new validation.', 7.5)

def main():
    OUT.mkdir(parents=True, exist_ok=True)
    for name, file in [('Arial','Arial.ttf'),('Arial-Bold','Arial Bold.ttf')]:
        pdfmetrics.registerFont(TTFont(name,'/System/Library/Fonts/Supplemental/'+file))
    ev = json.loads((PREV9/(PREV9.name+'_evidence.json')).read_text())
    for item in ev['sources']:
        assert hashlib.sha256((ROOT/item['path']).read_bytes()).hexdigest()==item['sha256']
    d=b.Drawing(OUT/f'{STEM}_layout.pdf'); draw(d); d.finish(OUT/f'{STEM}_layout.svg')
    page=PdfReader(OUT/f'{STEM}_layout.pdf').pages[0]
    for name,x,y in [('source38',6,44),('source120',6,96),('median38',151,43),('median120',151,95)]:
        chart=PdfReader(PREV/f'{name}.pdf').pages[0]
        page.merge_transformed_page(chart,Transformation().translate(x*b.MM,(b.H-y-28)*b.MM))
    writer=PdfWriter(); writer.add_page(page)
    writer.add_metadata({'/Title':'Integrated EMG source-to-decision flow','/Author':'Yusuke Matsui'})
    with (OUT/f'{STEM}.pdf').open('wb') as f: writer.write(f)
    subprocess.run(['pdftoppm','-singlefile','-r','350','-png',str(OUT/f'{STEM}.pdf'),str(OUT/STEM)],check=True)
    sources=[ROOT/item['path'] for item in ev['sources']]+[Path(__file__)]
    (OUT/f'{STEM}_evidence.json').write_text(json.dumps({
        'version':'v010_2026.09.12-r7','status':'candidate_not_adopted','width_mm':280,'height_mm':210,
        'scope':'Integrated layout only; reused verified waveforms and historical metadata/decisions. Dashed context link is supporting evidence, not a new API dependency.',
        'sources':[{'path':str(f.relative_to(ROOT)),'sha256':hashlib.sha256(f.read_bytes()).hexdigest()} for f in sources]
    },indent=2)+'\n')
    print(OUT/f'{STEM}.png')

if __name__=='__main__':main()
