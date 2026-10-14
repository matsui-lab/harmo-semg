"""Extend Figure 2 with unchanged saved GUI crops across the local workflow."""
import base64
import hashlib
import json
import shutil
import subprocess
from pathlib import Path

from PIL import Image
from pypdf import PdfReader, PdfWriter, Transformation
from reportlab.pdfbase import pdfmetrics
from reportlab.pdfbase.ttfonts import TTFont
from reportlab.pdfgen import canvas

ROOT = Path(__file__).resolve().parents[2]
STEM = 'figure2_implementation_gui_v015_2026.09.16-r4'
OUT = ROOT / 'paper/figures/candidates' / STEM
BUILD = ROOT / 'tmp' / STEM
PREV = ROOT / 'paper/figures/candidates/figure2_implementation_gui_v014_2026.09.16-r3'
OLD = ROOT / 'paper/figures/candidates/implementation_reassessment_v012_2026.09.16-r1'
MM, PX = 72 / 25.4, 96 / 25.4
W, H = 280, 357
TITLE = 'Registered EMG reuse: processing and evidence-based reassessment'


def main():
    OUT.mkdir(parents=True, exist_ok=True)
    BUILD.mkdir(parents=True, exist_ok=True)
    for name, f in [('Arial', 'Arial.ttf'), ('Arial-Bold', 'Arial Bold.ttf')]:
        pdfmetrics.registerFont(TTFont(name, '/System/Library/Fonts/Supplemental/' + f))
    originals = {
        'review': ROOT / 'paper/figures/candidates/figure2_implementation_gui_v013_2026.09.16-r2/review_screen_source.png',
        'processing': Path('/private/tmp/harmo-result-before-after.png'),
        'comparison': Path('/private/tmp/harmo-integration-desktop.png'),
        'library': Path('/private/tmp/harmo-entity-library.png'),
    }
    screens = {}
    for key, source in originals.items():
        target = OUT / (key + '_screen_source.png')
        if not target.exists():
            shutil.copy2(source, target)
        assert target.read_bytes() == source.read_bytes()
        screens[key] = target

    scene = json.loads((ROOT / 'tmp/figure2_pptx_v012/scene.json').read_text())
    scene['height'] = H * PX
    scene['objects'] = [o for o in scene['objects'] if o.get('text') != TITLE]
    c = canvas.Canvas(str(BUILD / 'gui.pdf'), pagesize=(W * MM, H * MM), invariant=1)
    crops = []

    def text(x, y, value, size=8, bold=False):
        c.setFillColorRGB(.067, .067, .067)
        c.setFont('Arial-Bold' if bold else 'Arial', size)
        c.drawString(x * MM, (H - y) * MM, value)
        scene['objects'].append(dict(kind='text', x=x*PX, y=y*PX, text=value,
            size=size*4/3, bold=bold, font='Arial', anchor='start', colour='#111111'))

    def line(x, y, xe, ye=None):
        ye = y if ye is None else ye
        c.setStrokeColorRGB(.62, .65, .68)
        c.setLineWidth(.18 * MM)
        c.line(x*MM, (H-y)*MM, xe*MM, (H-ye)*MM)
        scene['objects'].append(dict(kind='path', points=[[x*PX,y*PX],[xe*PX,ye*PX]],
            colour='#9EA6AD', width=.18*PX, dashed=False))

    def crop(key, x, y, cy, ch, cx=0, cw=None, width=63):
        source = screens[key]
        iw, ih = Image.open(source).size
        cw = iw if cw is None else cw
        assert 0 <= cy < cy+ch <= ih and 0 <= cx < cx+cw <= iw
        scale = width / cw
        height = ch * scale
        c.saveState()
        p = c.beginPath()
        p.rect(x*MM, (H-y-height)*MM, width*MM, height*MM)
        c.clipPath(p, stroke=0, fill=0)
        c.drawImage(str(source), (x-cx*scale)*MM, (H-y-(ih-cy)*scale)*MM,
                    width=iw*scale*MM, height=ih*scale*MM)
        c.restoreState()
        scene['objects'].append(dict(kind='image', data='data:image/png;base64,'+
            base64.b64encode(source.read_bytes()).decode(),
            alt='Unchanged saved real-data GUI crop: '+key,
            x=x*PX, y=y*PX, w=width*PX, h=height*PX,
            crop=dict(left=cx/iw, top=cy/ih, right=(iw-cx-cw)/iw, bottom=(ih-cy-ch)/ih)))
        crops.append(dict(source=source.name, source_box=[cx,cy,cw,ch], destination_mm=[x,y,width,height]))
        return y + height

    text(5, 8, 'A', 14, True)
    text(13, 8, TITLE, 12, True)
    line(5, 211, 275)
    text(5, 218, 'Local browser: from record review to a reusable library', 11, True)
    text(5, 224, 'Saved Study 38 screens; a separate diagnostic run, not the registered two-study result in A.', 8)
    headings = [('B','Review meaning','Source context',6),
                ('C','Inspect processing','Processing',75),
                ('D','Compare records','Curves and decisions',144),
                ('E','Browse saved data','Retained outputs',213)]
    for letter, title, mapping, x in headings:
        text(x, 233, letter, 12, True)
        text(x+7, 233, title, 9, True)
        text(x, 239, mapping, 8)
        line(x, 241, x+63)
    for x in [70,139,208]:
        line(x, 235, x+3)
        line(x+3, 235, x+1.7, 234)
        line(x+3, 235, x+1.7, 236)

    # Review: question, metadata, measured signal, actual excerpt and actions.
    y = crop('review',6,244,158,114,182,692)
    text(35,y+2.8,'...',9)
    y = crop('review',6,y+4.5,383,192,182,692)
    y = crop('review',6,y+1,609,221,182,692)
    y = crop('review',6,y+1,849,117,182,692)
    y = crop('review',6,y+1,1008,53,182,692)
    text(6, y+5, '... repeat for other review items', 7.5)

    # Applied processing view is post-execution; no invented pre-run screen.
    y = crop('processing',75,244,140,182)
    y = crop('processing',75,y+2,341,66)
    y = crop('processing',75,y+2,438,637)

    # Keep both diagnostic plots and the explicit held peak difference.
    y = crop('comparison',144,244,176,127)
    text(173,y+3,'...',9)
    y = crop('comparison',144,y+5,816,677)

    # Save action, storage organization, intended-use choice and held rows.
    y = crop('comparison',213,244,66,73)
    text(242,y+3,'...',9)
    y = crop('library',213,y+5,43,132,25,800)
    y = crop('library',213,y+2,196,203,25,800)
    y = crop('library',213,y+2,419,317,25,800)
    text(242,y+3,'...',9)

    descriptions = [
        (6, ['Candidate + evidence + waveform', 'Confirm, edit, or hold each meaning']),
        (75, ['Original attributes and applied values', 'Input signal → processed cycle']),
        (144, ['Overlay and same-phase difference', 'Timing and amplitude remain on hold']),
        (213, ['Shared records and derived signals', 'Use-specific assessments kept separately']),
    ]
    line(5, 335, 275)
    for x, rows in descriptions:
        for i, value in enumerate(rows):
            text(x,340+i*5,value,7.6,i==0)
    text(5,353,'Screens are cropped, not recreated. “...” omits intervening or repeated content. No human approval was submitted.',8)
    c.showPage()
    c.save()

    # Reuse all scientific drawing operations in A; only reposition its title.
    source_pdf = OLD / (OLD.name+'.pdf')
    source_writer = PdfWriter(clone_from=source_pdf)
    page = source_writer.pages[0]
    stream = page.get_contents()
    removed = [op for op in stream.operations if op[1] == b'Tj' and str(op[0][0]) == TITLE]
    assert len(removed) == 1
    stream.operations = [op for op in stream.operations if op not in removed]
    page.replace_contents(stream)
    writer = PdfWriter()
    target = writer.add_blank_page(W*MM,H*MM)
    target.merge_transformed_page(page,Transformation().translate(0,(H-210)*MM))
    target.merge_page(PdfReader(BUILD/'gui.pdf').pages[0])
    writer.add_metadata({'/Author':'Yusuke Matsui','/Title':'Figure 2. Implementation and local-browser workflow'})
    with (OUT/(STEM+'.pdf')).open('wb') as f:
        writer.write(f)
    (BUILD/'scene.json').write_text(json.dumps(scene,indent=2))
    previous_caption = (PREV/(PREV.name+'_caption.md')).read_text()
    caption = previous_caption.split('(B) The local review')[0]
    caption = caption.replace('Registered processing and user review of record meaning',
                              'Registered implementation and the local-browser workflow')
    caption += '''(B–E) Saved real-data screenshots illustrate the main local-browser stages,
ordered by the corresponding functions in A: source context, processing,
derived curves and decisions, and retained outputs. These screens concern a
separate diagnostic Study 38 project, not the execution history of A.
(B) A proposed muscle identity is reviewed against record metadata, repository
and article information, a measured waveform and its supporting excerpt
(Study 38 article PMC6897988). Users can confirm, edit or hold a meaning;
the same interaction recurs for other review items. No human approval was
submitted in the recorded test. (C) The post-execution view shows original
attributes alongside applied values, the applied processing operations, and
the input and derived cycle. Displaying an applied value is not evidence of
human confirmation or scientific validity. (D) The diagnostic run generated
16 curves from two records. An overlay and a same-phase difference help inspect
the signals; timing and amplitude comparisons remain on hold. In particular,
the interface does not report a definitive peak difference where boundary
effects or peak correspondence remain unresolved. These normalized waveforms
do not establish comparable muscle-activity magnitude. (E) The save action
and library view show shared source records, derived signals, and separate
use-specific assessments. The displayed amplitude assessments remain held;
source-record assessments do not authorize comparison of derived signals.

Screenshots are unchanged crops of saved real-data test screens, not recreated
interfaces. Ellipses indicate omitted intervening sections or repeated items;
they are figure annotations, not additional executed decisions. The stages
illustrate available interactions, not a completed sequence of approvals.
This figure does not establish usability or improved decision accuracy.
Original service logos identify sources, not endorsement.
'''
    (OUT/(STEM+'_caption.md')).write_text(caption)
    sources = [source_pdf,*screens.values(),ROOT/'tests/semantic_integration_ui.cjs',
               ROOT/'tests/semantic_workspace_ui.cjs',
               ROOT/'paper/results/semantic_integration_v008_2026.09.15-r8/session.json']
    manifest = dict(status='candidate_not_adopted',version='v015_2026.09.16-r4',figure=2,
        registered_example_unchanged=True,gui_is_separate_illustration=True,
        human_approval_submitted=False,scientific_comparison_on_hold=True,
        panels={a:b for a,b,_,_ in headings},screenshots_unaltered=True,crops=crops,
        sources=[dict(path=str(p.relative_to(ROOT)),sha256=hashlib.sha256(p.read_bytes()).hexdigest()) for p in sources])
    (OUT/(STEM+'_evidence.json')).write_text(json.dumps(manifest,indent=2))
    subprocess.run(['pdftoppm','-singlefile','-scale-to','2500','-png',
                    str(OUT/(STEM+'.pdf')),str(OUT/STEM)],check=True)
    print(OUT)


if __name__ == '__main__':
    main()
