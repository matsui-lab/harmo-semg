"""Label existing panels without changing scientific content or screenshot crops."""
import hashlib, json, subprocess
from pathlib import Path
from pypdf import PdfReader, PdfWriter
from reportlab.pdfgen import canvas
from reportlab.pdfbase import pdfmetrics
from reportlab.pdfbase.ttfonts import TTFont

ROOT=Path(__file__).resolve().parents[2]
PREV='figure2_implementation_gui_v013_2026.09.16-r2'
STEM='figure2_implementation_gui_v014_2026.09.16-r3'
OLD=ROOT/'paper/figures/candidates'/PREV
OUT=ROOT/'paper/figures/candidates'/STEM
BUILD=ROOT/'tmp'/STEM
MM=72/25.4; PX=96/25.4
TITLES=[('A','Registered EMG reuse: processing and evidence-based reassessment',8,12),
        ('B','Record meaning review in the local browser',218,11)]

def main():
    OUT.mkdir(parents=True,exist_ok=True);BUILD.mkdir(parents=True,exist_ok=True)
    reader=PdfReader(OLD/(PREV+'.pdf'));page=reader.pages[0]
    stream=page.get_contents();removed=[];ops=[]
    for operands,operator in stream.operations:
        if operator==b'Tj' and str(operands[0]) in [t[1] for t in TITLES]:
            removed.append(str(operands[0]));continue
        ops.append((operands,operator))
    assert sorted(removed)==sorted(t[1] for t in TITLES),removed
    stream.operations=ops;page.replace_contents(stream)
    pdfmetrics.registerFont(TTFont('Arial-Bold','/System/Library/Fonts/Supplemental/Arial Bold.ttf'))
    c=canvas.Canvas(str(BUILD/'labels.pdf'),pagesize=(280*MM,283*MM),invariant=1)
    c.setFillColorRGB(.067,.067,.067)
    for letter,title,y,size in TITLES:
        c.setFont('Arial-Bold',14);c.drawString(5*MM,(283-y)*MM,letter)
        c.setFont('Arial-Bold',size);c.drawString(13*MM,(283-y)*MM,title)
    c.showPage();c.save();page.merge_page(PdfReader(BUILD/'labels.pdf').pages[0])
    writer=PdfWriter();writer.add_page(page);writer.add_metadata({'/Author':'Yusuke Matsui','/Title':'Figure 2. Registered processing and user review'})
    with (OUT/(STEM+'.pdf')).open('wb') as f:writer.write(f)
    scene=json.loads((ROOT/'tmp'/PREV/'scene.json').read_text())
    originals=[o for o in scene['objects'] if o.get('text') in removed]
    assert len(originals)==2
    scene['objects']=[o for o in scene['objects'] if o not in originals]
    for letter,title,y,size in TITLES:
        for x,text,pts in [(5,letter,14),(13,title,size)]:
            scene['objects'].append(dict(kind='text',x=x*PX,y=y*PX,text=text,size=pts*4/3,bold=True,font='Arial',anchor='start',colour='#111111'))
    (BUILD/'scene.json').write_text(json.dumps(scene,indent=2))
    caption=(OLD/(PREV+'_caption.md')).read_text().replace('The upper example follows','(A) The registered example follows').replace('The lower inset shows','(B) The local review interface shows').replace('illustrated above','illustrated in A').replace('upper example.','registered example in A.')
    (OUT/(STEM+'_caption.md')).write_text(caption)
    evidence=json.loads((OLD/(PREV+'_evidence.json')).read_text())
    evidence.update(version='v014_2026.09.16-r3',panels={'A':'Registered processing and reassessment','B':'Local review of record meaning'},change_scope='Panel labels and caption references only')
    evidence['sources'] += [dict(path=str((OLD/(PREV+'.pdf')).relative_to(ROOT)),sha256=hashlib.sha256((OLD/(PREV+'.pdf')).read_bytes()).hexdigest())]
    (OUT/(STEM+'_evidence.json')).write_text(json.dumps(evidence,indent=2))
    subprocess.run(['pdftoppm','-singlefile','-scale-to','1900','-png',str(OUT/(STEM+'.pdf')),str(OUT/STEM)],check=True)
    print(OUT)
if __name__=='__main__':main()
