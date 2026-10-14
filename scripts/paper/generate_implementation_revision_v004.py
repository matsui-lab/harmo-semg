#!/usr/bin/env python3
"""Build an unselected Methods-aligned figure candidate from verified examples."""
import hashlib
import json
import subprocess
from pathlib import Path

from reportlab.pdfbase import pdfmetrics
from reportlab.pdfbase.ttfonts import TTFont
import generate_implementation_figure as base

ROOT = base.ROOT
VERSION = 'v004_2026.09.12-r1'
STEM = f'implementation_four_panel_{VERSION}'
OLD = 'implementation_four_panel_v003_2026.09.11-r3'
OUT = ROOT / 'paper/figures/candidates' / STEM
EXAMPLES = ROOT / 'paper/figures/candidates' / OLD / f'{OLD}_examples.json'


class Commands:
    def __init__(self):
        self.commands = []

    def __getattr__(self, name):
        def record(*args, **kwargs):
            self.commands.append((name, args, kwargs))
        return record


def draw(d, e):
    commands = Commands()
    base.draw(commands, e)
    panel = None
    replacements = {
        'Author-specified setup': 'Researcher: source mapping',
        'File pattern + channel definitions': 'Files + labels + units + anatomy',
        'Output: values + definitions + source digest': 'Samples + time + channel descriptors',
        'Input: bounded cycles + validated event references': 'Input: pre-segmented cycles + validated events',
        'Declared processing; interpolation illustrated below': 'Researcher: processing specification',
        'Optional: centering, filtering, envelope, normalization': 'Illustrated operation: linear phase interpolation',
        'Reassess comparison AND release policy': 'Re-run comparison rules AND release policy',
        'No event detection; no amplitude-pooling claim.': 'No event detection; no automatic permission to pool.',
    }
    for name, args, kwargs in commands.commands:
        if name == 'panel':
            panel = args[2]
        if panel not in ('A', 'D'):
            continue
        args = list(args)
        if name == 'text':
            args[2] = replacements.get(args[2], args[2])
        if panel == 'A' and name == 'box' and args[:4] == [3,88,88,8]:
            args[3] = 11
        getattr(d, name)(*args, **kwargs)
    d.text(5,98,'Source identity + transformation history',7.5)

    d.panel(96,0,'B','Verify statements; attach scoped evidence')
    d.box(101,13,85,12,True)
    d.text(143.5,18,'Researcher: extraction plan',8,True,'middle')
    d.text(143.5,23,'Section + pattern + assigned value + scope',7.5,False,'middle')
    d.box(101,30,85,12)
    d.text(103,35,'Article: Study 31 / processed EMG',8,True)
    d.text(103,40,'Reported band-pass limits: 20 Hz and 450 Hz',7.5)
    d.line(143.5,25,143.5,30,arrow=True,dashed=True)
    d.line(143.5,42,143.5,46,arrow=True)
    d.box(101,46,85,9,colour=base.BLUE,width=.35)
    d.text(143.5,52,'1  Verify the predefined statement is present',8,True,'middle')
    d.line(143.5,55,143.5,59,arrow=True)
    d.box(101,59,85,9,colour=base.BLUE,width=.35)
    d.text(143.5,65,'2  Assign the registered value: [20, 450] Hz',8,True,'middle')
    d.line(143.5,68,143.5,72,arrow=True)
    d.box(101,72,85,12,colour=base.BLUE,width=.35)
    d.text(143.5,77,'3  Compare overlapping repository evidence',8,True,'middle')
    d.text(143.5,82,'Here: no overlapping value; retain article scope',7.5,False,'middle')
    d.line(143.5,84,143.5,87,arrow=True)
    d.box(101,87,85,8,colour=base.BLUE,width=.35)
    d.text(143.5,92,'Output: value + scope + source + decision',8,True,'middle')
    d.text(143.5,99,'Evidence attached does not mean use approved',7.5,True,'middle')

    d.panel(0,101,'C','Select rules from a structured request')
    d.box(5,114,86,18,True)
    d.text(7,119,'Analyst specifies',8,True)
    d.text(7,124,'Records: A / B     Channels: right tibialis anterior',7.5)
    d.text(7,129,'Purpose: activation timing  (not a free-text question)',7.5)
    d.line(48,132,48,136,arrow=True)
    d.box(5,136,86,13,colour=base.BLUE,width=.35)
    d.text(48,141,'Check evidence, anatomy / side and task',8,True,'middle')
    d.text(48,146,'Timing rules: event reference + processing + time grid',7.5,False,'middle')
    d.line(48,149,48,153,arrow=True)
    d.table(5,153,[39,47],['First applicable finding','Comparison state'],
        [['Explicit incompatibility','Not comparable'],
         ['Unresolved requirement','Unassessed'],
         ['Outstanding condition','Conditionally comparable'],
         ['All requirements met','Comparable']],row_h=5.1,size=7.5)
    d.line(5,168.3,5,173.4,base.BLUE,1.1)
    d.line(48,178.5,48,182,arrow=True)
    d.box(5,182,86,10,colour=base.BLUE,width=.35)
    d.text(48,186,'Apply release policy and required evidence',8,True,'middle')
    d.text(48,190.5,'Deny / hold / conditional release / release',7.5,False,'middle')
    d.text(48,198,'Output: decision + reasons + conditions + source links',7.5,True,'middle')


def main():
    OUT.mkdir(parents=True, exist_ok=True)
    pdfmetrics.registerFont(TTFont('Arial','/System/Library/Fonts/Supplemental/Arial.ttf'))
    pdfmetrics.registerFont(TTFont('Arial-Bold','/System/Library/Fonts/Supplemental/Arial Bold.ttf'))
    e = json.loads(EXAMPLES.read_text())
    assert e['claim']['extraction_method'] == 'frozen-regex:presence'
    assert e['claim']['value'] == [20,450]
    d = base.Drawing(OUT / f'{STEM}.pdf')
    draw(d,e)
    d.finish(OUT / f'{STEM}.svg')
    subprocess.run(['pdftoppm','-singlefile','-r','300','-png',str(OUT/f'{STEM}.pdf'),str(OUT/STEM)],check=True)
    (OUT/f'{STEM}_examples.json').write_bytes(EXAMPLES.read_bytes())
    sources = [EXAMPLES, ROOT/'paper/paper.md', Path(__file__), Path(base.__file__),
               ROOT/'paper/literature_semantic_enrichment.yaml', ROOT/'src/harmo/evidence/policy.py']
    metadata = dict(version=VERSION,status='candidate_not_adopted',width_mm=base.W,height_mm=base.H,
                    minimum_text_pt=min(x['size_pt'] for x in d.texts),text_count=len(d.texts),
                    example_note='Reuses v003 stored empirical examples and previously computed synthetic interpolation; no new experiment.',
                    sources=[dict(path=str(p.relative_to(ROOT)),sha256=hashlib.sha256(p.read_bytes()).hexdigest()) for p in sources])
    (OUT/f'{STEM}_evidence.json').write_text(json.dumps(metadata,indent=2)+'\n')
    print(json.dumps(metadata,indent=2))


if __name__ == '__main__':
    main()
