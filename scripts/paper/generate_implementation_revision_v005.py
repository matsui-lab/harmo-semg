#!/usr/bin/env python3
"""Three-band, diagram-led implementation candidate; does not adopt artwork."""
import json
import hashlib
import re
import subprocess
from pathlib import Path
from reportlab.pdfbase import pdfmetrics
from reportlab.pdfbase.ttfonts import TTFont
import generate_implementation_figure as b

ROOT=b.ROOT
STEM='implementation_three_band_v005_2026.09.12-r2'
OUT=ROOT/'paper/figures/candidates'/STEM
OLD=ROOT/'paper/figures/candidates/implementation_four_panel_v003_2026.09.11-r3/implementation_four_panel_v003_2026.09.11-r3_examples.json'
b.Y_SCALE=1
INK=b.INK
BLUE='#91BACC'

def text(d,x,y,s,size=8,bold=False,anchor='start'):
    d.text(x,y,s,size,bold,anchor)

def arrow(d,x,y,xx,yy):
    d.line(x,y,xx,yy,colour=b.RULE,width=.28,arrow=True)

def heading(d,y,n,title):
    d.circle(6,y-1,2.5,BLUE)
    text(d,6,y,n,9,True,'middle')
    text(d,12,y,title,10,True)

def miniwave(d,values,x,y,w,h,points=False):
    xy=[(x+i/(len(values)-1)*w,y-float(v)*h) for i,v in enumerate(values)]
    d.line(x,y,x+w,y,width=.18)
    for a,c in zip(xy,xy[1:]): d.line(*a,*c,colour=BLUE,width=.45)
    if points:
        for xx,yy in xy: d.circle(xx,yy,.45,BLUE)

def draw(d,e):
    heading(d,7,'1','Standardize the recording')
    # Actual source values; mapping is supplied separately by the researcher.
    d.box(5,14,40,22)
    text(d,7,18,'Public file',8,True)
    text(d,43,18,'MAT / C3D / CSV',7.5,False,'end')
    vals=[[f'{float(e["times"][i]["time_seconds"]):.3f}',f'{float(e["samples"][i]["Voltage.RTA"]):.5f}'] for i in range(2)]
    d.table(7,20,[14,22],['t (s)','RTA (V)'],vals,4.5,7.5)
    d.box(54,13,64,13,True)
    text(d,56,17,'Researcher mapping',8,True)
    text(d,56,22,'RTA  =  right tibialis anterior; V',7.5)
    arrow(d,86,26,86,29)
    arrow(d,46,31,65,31)
    text(d,55,29,'read',7.5,False,'middle')
    d.box(66,29,39,9,colour=BLUE,width=.4)
    text(d,85.5,34.7,'Validate + link',8,True,'middle')
    arrow(d,106,33,122,33)
    d.rounded_box(123,13,62,27,colour=BLUE,width=.4)
    text(d,126,18,'Common record',8.5,True)
    d.line(125,20,183,20)
    d.table(126,22,[12,20],['t (s)','Value (V)'],vals,4.3,7.5)
    text(d,161,26,'Right TA',7.5,True)
    text(d,161,31,'Source link',7.5)
    text(d,161,36,'History',7.5)
    text(d,7,41,'Values unchanged',7.5)
    d.line(3,45,187,45,colour=b.GREY,width=.35)

    heading(d,52,'2','Link a statement to its evidence')
    d.box(5,58,59,25)
    text(d,8,62,'Study 31 article',8,True)
    for y,w in [(65,48),(67,43)]: d.line(8,y,8+w,y,colour=b.GREY,width=.4)
    text(d,8,72,'cutoff frequencies of',7.5)
    text(d,8,76,'20 Hz and 450 Hz',8,True)
    d.line(8,77.5,35,77.5,colour=BLUE,width=.9)
    d.line(8,80,54,80,colour=b.GREY,width=.4)
    d.box(71,58,48,12,True)
    text(d,73,62,'Researcher rule: presence',7.5,True)
    text(d,73,67,'20 Hz ... 450 Hz',8)
    arrow(d,95,70,95,74)
    arrow(d,65,76,71,76)
    text(d,95,79,'Verify + assign',8,True,'middle')
    arrow(d,119,76,128,76)
    d.rounded_box(130,58,55,23,colour=BLUE,width=.4)
    text(d,133,63,'Band-pass: [20, 450] Hz',8.5,True)
    text(d,133,68,'Scope: processed EMG dataset',7.5)
    text(d,133,73,'Source: article section',7.5)
    text(d,133,78,'Accepted at article scope',7.5)
    text(d,5,88,'Repository check: no overlapping value',7.5)
    text(d,185,88,'Evidence attached, not use approved',7.5,True,'end')
    d.line(3,92,187,92,colour=b.GREY,width=.35)

    heading(d,99,'3','Check the purpose; resolve only supported conditions')
    d.box(5,105,39,21,True)
    text(d,7,110,'Analyst request',8,True)
    text(d,7,115,'Records A / B; right TA',7.5)
    text(d,7,120,'Activation timing',8,True)
    text(d,7,124,'Structured fields',7.5)
    arrow(d,44,116,49,116)
    text(d,66,109,'Check',8,True,'middle')
    d.box(50,112,32,16,colour=BLUE,width=.35)
    text(d,52,116,'Evidence + anatomy',7.5)
    text(d,52,121,'Task + events',7.5)
    text(d,52,126,'Processing + time',7.5)
    arrow(d,82,119,88,119)
    # Branches summarize comparability, not automatic release permission.
    for y,s in [(106,'Incompatible'),(114,'Unassessed'),(122,'Conditional'),(130,'Comparable')]:
        d.box(90,y-4,28,6,colour=BLUE if s=='Conditional' else b.RULE,width=.4 if s=='Conditional' else .2)
        text(d,104,y,s,7.5,s=='Conditional','middle')
    d.line(88,104,88,128)
    for y in (104,112,120,128): arrow(d,88,y,90,y)
    text(d,155,107,'Bounded cycle (synthetic)',7.5,True,'middle')
    curve=e['synthetic_resolution'][0]
    miniwave(d,curve['source'],126,120,22,9,True)
    arrow(d,149,116,157,116)
    text(d,153,113,'linear',7.5,False,'middle')
    miniwave(d,curve['derived'],159,120,24,9)
    text(d,137,125,'11 samples',7.5,False,'middle')
    text(d,171,125,'101 phase points',7.5,False,'middle')
    arrow(d,118,120,124,120)
    arrow(d,171,126,171,130)
    d.box(127,131,58,8,colour=BLUE,width=.35)
    text(d,156,136,'Recheck + release policy',8,True,'middle')
    text(d,5,138,'Dashed boxes: human-specified inputs',7.5)

def main():
    OUT.mkdir(parents=True,exist_ok=True)
    for name,file in [('Arial','Arial.ttf'),('Arial-Bold','Arial Bold.ttf')]:
        pdfmetrics.registerFont(TTFont(name,'/System/Library/Fonts/Supplemental/'+file))
    e=json.loads(OLD.read_text())
    assert e['claim']['value']==[20,450]
    d=b.Drawing(OUT/f'{STEM}.pdf'); draw(d,e); d.finish(OUT/f'{STEM}.svg')
    subprocess.run(['pdftoppm','-singlefile','-r','300','-png',str(OUT/f'{STEM}.pdf'),str(OUT/STEM)],check=True)
    words=sum(len(re.findall(r'\b[\w-]+\b',x['text'])) for x in d.texts)
    meta=dict(status='candidate_not_adopted',version='v005_2026.09.12-r2',width_mm=b.W,height_mm=b.H,word_count=words,
              text_count=len(d.texts),minimum_text_pt=min(x['size_pt'] for x in d.texts),
              sources=[dict(path=str(p.relative_to(ROOT)),sha256=hashlib.sha256(p.read_bytes()).hexdigest()) for p in (OLD,Path(__file__),Path(b.__file__))])
    (OUT/f'{STEM}_evidence.json').write_text(json.dumps(meta,indent=2)+'\n')
    print(json.dumps(meta,indent=2))

if __name__=='__main__': main()
