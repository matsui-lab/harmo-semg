"""Retain the registered example and attach an explicitly separate GUI inset."""
import base64, hashlib, json, shutil, subprocess
from pathlib import Path
from reportlab.pdfgen import canvas
from reportlab.pdfbase import pdfmetrics
from reportlab.pdfbase.ttfonts import TTFont
from pypdf import PdfReader, PdfWriter, Transformation
ROOT=Path(__file__).resolve().parents[2]
STEM='figure2_implementation_gui_v013_2026.09.16-r2'
OUT=ROOT/'paper/figures/candidates'/STEM
BUILD=ROOT/'tmp'/STEM
MM=72/25.4; PX=96/25.4; W=280; H=283
OLD=ROOT/'paper/figures/candidates/implementation_reassessment_v012_2026.09.16-r1'
PDF=OLD/(OLD.name+'.pdf')
SCREEN=Path('/private/tmp/harmo-workspace-v003-desktop.png')

def main():
    OUT.mkdir(parents=True,exist_ok=True); BUILD.mkdir(parents=True,exist_ok=True)
    screenshot=OUT/'review_screen_source.png'
    if not screenshot.exists(): shutil.copy2(SCREEN,screenshot)
    for name,f in [('Arial','Arial.ttf'),('Arial-Bold','Arial Bold.ttf')]:
        pdfmetrics.registerFont(TTFont(name,'/System/Library/Fonts/Supplemental/'+f))
    scene=json.loads((ROOT/'tmp/figure2_pptx_v012/scene.json').read_text())
    scene['height']=H*PX
    c=canvas.Canvas(str(BUILD/'inset.pdf'),pagesize=(W*MM,H*MM),invariant=1)
    c.setAuthor('Yusuke Matsui'); c.setTitle('Registered processing and record meaning review')
    def text(x,y,s,size=9,bold=False):
        c.setFont('Arial-Bold' if bold else 'Arial',size);c.setFillColorRGB(.067,.067,.067);c.drawString(x*MM,(H-y)*MM,s)
        scene['objects'].append(dict(kind='text',x=x*PX,y=y*PX,text=s,size=size*4/3,bold=bold,font='Arial',anchor='start',colour='#111111'))
    def line(x,y,xe,ye=None):
        ye=y if ye is None else ye;c.setStrokeColorRGB(.62,.65,.68);c.setLineWidth(.25*MM);c.line(x*MM,(H-y)*MM,xe*MM,(H-ye)*MM)
        scene['objects'].append(dict(kind='path',points=[[x*PX,y*PX],[xe*PX,ye*PX]],colour='#9EA6AD',width=.25*PX,dashed=False))
    line(5,211,275)
    text(5,218,'Record meaning review in the local browser',11,True)
    text(170,227,'Shown: Study 38 / Subject1.mat',9,True)
    text(170,235,'Candidate: tibialis anterior',9)
    text(170,243,'Check the excerpt and its applicability',9)
    text(170,251,'Confirm the meaning, edit it, or hold',9)
    line(170,256,275)
    text(170,263,'Confirmed meaning enters reassessment',9,True)
    text(170,270,'Processing and scientific checks still apply',9)
    text(170,277,'Screen illustration; no approval was submitted',8)
    # Three unchanged crops from one saved screen, with gaps indicating omissions.
    # This is presentation clipping, not a recreated or altered interface.
    width,height=1056,1272
    data='data:image/png;base64,'+base64.b64encode(screenshot.read_bytes()).decode()
    for cy,ch,y in [(195,83,224),(846,98,244),(1008,53,267)]:
        cx,cw=182,692; x=8; w=147; scale=w/cw; h=ch*scale
        c.saveState();p=c.beginPath();p.rect(x*MM,(H-y-h)*MM,w*MM,h*MM);c.clipPath(p,stroke=0,fill=0)
        c.drawImage(str(screenshot),(x-cx*scale)*MM,(H-y-(height-cy)*scale)*MM,width=width*scale*MM,height=height*scale*MM)
        c.restoreState()
        scene['objects'].append(dict(kind='image',data=data,alt='Unchanged crop from saved real-data review screen',x=x*PX,y=y*PX,w=w*PX,h=h*PX,
            crop=dict(left=cx/width,top=cy/height,right=(width-cx-cw)/width,bottom=(height-cy-ch)/height)))
    c.showPage();c.save()
    w=PdfWriter();page=w.add_blank_page(W*MM,H*MM)
    page.merge_transformed_page(PdfReader(PDF).pages[0],Transformation().translate(0,(H-210)*MM))
    page.merge_page(PdfReader(BUILD/'inset.pdf').pages[0])
    w.add_metadata({'/Author':'Yusuke Matsui','/Title':'Registered processing and record meaning review'})
    with (OUT/(STEM+'.pdf')).open('wb') as f:w.write(f)
    (BUILD/'scene.json').write_text(json.dumps(scene,indent=2))
    sources=[PDF,screenshot,ROOT/'tests/semantic_workspace_ui.cjs',ROOT/'src/harmo/semantic_workspace.html',ROOT/'paper/results/local_browser_distribution_v001_2026.09.16-r1/REPORT_ja_v001.md']
    manifest=dict(status='candidate_not_adopted',version='v013_2026.09.16-r2',figure=2,
        registered_example_unchanged=True,gui_is_separate_illustration=True,human_approval_submitted=False,
        screenshot_note='Saved full real-data review screen; unchanged candidate, excerpt and action crops. Not the synthetic distribution demo.',
        sources=[dict(path=str(p.relative_to(ROOT)),sha256=hashlib.sha256(p.read_bytes()).hexdigest())for p in sources])
    (OUT/(STEM+'_evidence.json')).write_text(json.dumps(manifest,indent=2))
    subprocess.run(['pdftoppm','-singlefile','-scale-to','1900','-png',str(OUT/(STEM+'.pdf')),str(OUT/STEM)],check=True)
    print(OUT)
if __name__=='__main__':main()
