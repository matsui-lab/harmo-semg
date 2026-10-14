"""One left-to-right implementation route with concrete, editable evidence diagrams."""
import base64
import csv
import hashlib
import json
import subprocess
from pathlib import Path

import pdfplumber
from PIL import Image
from pypdf import PdfReader, PdfWriter, Transformation
from reportlab.lib.colors import HexColor
from reportlab.pdfbase import pdfmetrics
from reportlab.pdfbase.ttfonts import TTFont
from reportlab.pdfgen import canvas

ROOT = Path(__file__).resolve().parents[2]
STEM = 'figure2_implementation_gui_v016_2026.09.16-r5'
PREV = 'figure2_implementation_gui_v015_2026.09.16-r4'
OLD = ROOT/'paper/figures/candidates'/PREV
OUT = ROOT/'paper/figures/candidates'/STEM
BUILD = ROOT/'tmp'/STEM
DATA = ROOT/'paper/figures/candidates/implementation_real_waveforms_v007_2026.09.12-r4'
ART = ROOT/'paper/figures/assets/figure1_emg_public_data_illustration.png'
LOGOS = ROOT/'paper/figures/assets/repository_logos'
MM, PX = 72/25.4, 96/25.4
W, H = 280, 357


def lower_bound(o):
    if o['kind'] in ('text','image','table'):
        return o['y']
    coords = o['points'] if o['kind']=='path' else [v[1:] for v in o['commands'] if len(v)>1]
    return min(p[1] for p in coords)


def rgb(v):
    if v is None:
        return 'none'
    if not isinstance(v,(list,tuple)):
        v=[v]*3
    return '#'+''.join(f'{round(x*255):02X}' for x in v[:3])


def main():
    OUT.mkdir(parents=True,exist_ok=True)
    BUILD.mkdir(parents=True,exist_ok=True)
    for name,f in [('Arial','Arial.ttf'),('Arial-Bold','Arial Bold.ttf'),('Courier New','Courier New.ttf')]:
        pdfmetrics.registerFont(TTFont(name,'/System/Library/Fonts/Supplemental/'+f))
    oldscene=json.loads((ROOT/'tmp'/PREV/'scene.json').read_text())
    gui=[o for o in oldscene['objects'] if lower_bound(o)>=211*PX-1e-6]
    objects=[]
    c=canvas.Canvas(str(BUILD/'top.pdf'),pagesize=(W*MM,H*MM),invariant=1)
    illustration_crops=[]

    def text(x,y,value,size=8,bold=False,colour='#111111',font='Arial'):
        c.setFont('Arial-Bold' if bold else font,size)
        c.setFillColor(HexColor(colour))
        c.drawString(x*MM,(H-y)*MM,value)
        objects.append(dict(kind='text',x=x*PX,y=y*PX,text=value,size=size*4/3,
            bold=bold,font=font,anchor='start',colour=colour))

    def path(points,colour='#8E989F',width=.25,dashed=False,name='Diagram connector',fill=None):
        c.setStrokeColor(HexColor(colour))
        c.setLineWidth(width*MM)
        c.setDash([2*MM,1*MM] if dashed else [])
        p=c.beginPath()
        for i,(x,y) in enumerate(points):
            (p.moveTo if i==0 else p.lineTo)(x*MM,(H-y)*MM)
        if fill:
            p.close()
            c.setFillColor(HexColor(fill))
        c.drawPath(p,stroke=1,fill=bool(fill))
        objects.append(dict(kind='path',name=name,points=[[x*PX,y*PX] for x,y in points],
            colour=colour,width=width*PX,dashed=dashed,**({'fill':fill} if fill else {})))

    def arrow(x,y,xe,width=.65,name='Primary left-to-right route'):
        assert xe>x
        path([(x,y),(xe,y)],'#5E6C76',width,name=name)
        path([(xe-1.7,y-1.2),(xe,y),(xe-1.7,y+1.2)],'#5E6C76',width,name=name)

    def page(x,y,w,h,dashed=False):
        path([(x,y),(x+w-3,y),(x+w,y+3),(x+w,y+h),(x,y+h),(x,y)],dashed=dashed,name='File-content diagram')
        path([(x+w-3,y),(x+w-3,y+3),(x+w,y+3)],dashed=dashed,name='File fold')

    def image(source,x,y,w,crop=None):
        iw,ih=Image.open(source).size
        cx,cy,cw,ch=crop or (0,0,iw,ih)
        h=ch*w/cw
        s=w/cw
        c.saveState()
        p=c.beginPath();p.rect(x*MM,(H-y-h)*MM,w*MM,h*MM)
        c.clipPath(p,stroke=0,fill=0)
        c.drawImage(str(source),(x-cx*s)*MM,(H-y-(ih-cy)*s)*MM,width=iw*s*MM,height=ih*s*MM,mask='auto')
        c.restoreState()
        obj=dict(kind='image',data='data:image/png;base64,'+base64.b64encode(source.read_bytes()).decode(),
                 alt=source.name,x=x*PX,y=y*PX,w=w*PX,h=h*PX)
        if crop:
            obj['crop']=dict(left=cx/iw,top=cy/ih,right=(iw-cx-cw)/iw,bottom=(ih-cy-ch)/ih)
            illustration_crops.append(dict(path=str(source.relative_to(ROOT)),box=[cx,cy,cw,ch]))
        objects.append(obj)

    def table(x,y,widths,values,rh=8,size=7):
        xs=[x]
        for width in widths:
            xs.append(xs[-1]+width)
        for xx in xs:
            path([(xx,y),(xx,y+rh*len(values))],width=.18,name='Table border')
        for row in range(len(values)+1):
            path([(x,y+row*rh),(xs[-1],y+row*rh)],width=.18,name='Table border')
        # Render PDF text but retain the whole inset as one native PPT table.
        initial=len(objects)
        for row,values_row in enumerate(values):
            for col,value in enumerate(values_row):
                face='Arial-Bold' if row==0 else 'Arial'
                tw=pdfmetrics.stringWidth(value,face,size)/MM
                text(xs[col]+(widths[col]-tw)/2,y+(row+.5)*rh+size/MM*.35,value,size,row==0)
        del objects[initial:]
        # Existing border paths are removed from the scene, because the native table owns them.
        border_count=len(xs)+len(values)+1
        del objects[-border_count:]
        objects.append(dict(kind='table',x=x*PX,y=y*PX,widths=[z*PX for z in widths],
                            rh=rh*PX,values=values,font_size=size*4/3))

    text(5,8,'A',14,True)
    text(13,8,'Registered implementation: four linked stages',12,True)
    path([(5,14),(275,14),(275,27),(5,27),(5,14)],dashed=True)
    text(8,19.5,'Intended use: right tibialis anterior activation timing',10,True)
    text(8,24.5,'Study 38 participant 1 and Gait120 S011',8.5)
    text(209,24.5,'Analyst-defined use and protocol',8)
    for i,(x,title) in enumerate([(6,'Record identity'),(75,'Waveform processing'),(144,'Use-specific decision'),(213,'Linked outputs')],1):
        text(x,38,str(i),15,True)
        text(x+8,37.5,title,9.5,True)
        path([(x,42),(x+63,42)],width=.3)
    for x in [69,138,207]:
        arrow(x,36,x+4)
    for x in [71.5,140.5,209.5]:
        path([(x,47),(x,193)],colour='#D1D6DA',width=.15,name='Stage separator')

    # 1. A generic acquisition illustration plus actual source filenames and scoped evidence.
    image(ART,6,49,24,(40,30,485,800))
    image(LOGOS/'figshare-logo.png',34,46,4)
    text(40,50,'Figshare',8.5,True)
    page(33,54,35,17)
    text(36,59,'MAT',8,True)
    text(36,64,'Study 38',7.5)
    text(36,68,'Subject1.mat',7.2)
    page(33,75,35,18)
    text(36,80,'ZIP / MAT',8,True)
    text(36,85,'Gait120 / S011',7.5)
    text(36,89,'ProcessedData.mat',6.8)
    text(6,96,'EMG acquisition illustration',6.8)
    image(LOGOS/'europe-pmc-basic-rgb.png',6,101,6)
    text(14,105,'Europe PMC / JATS XML',8,True)
    page(6,109,62,28)
    text(9,114,'PMC12177048',7.5)
    text(9,119,'Experimental preparation',7.5)
    text(9,125,'<p>... different muscles',7,font='Courier New')
    text(9,130,'of the right leg ...</p>',7,font='Courier New')
    path([(19.5,132),(38.5,132)],'#B78D62',.8,name='Matched phrase')
    page(6,142,62,15,dashed=True)
    text(9,147,'Project YAML (analyst-defined)',7.5,True)
    text(9,153,'side: Right',7.5,font='Courier New')
    text(6,164,'Fixed phrase + value / scope match',7.5,True)
    page(6,169,62,24)
    text(9,174,'Scoped context (JSON)',8,True)
    text(9,180,'right / surface EMG channel group',7.2)
    text(9,186,'Gait120, including S011',7.2)
    text(9,191,'Meaning only; no reuse permission',6.8)

    # 2. Actual input and output curves, plotted again from the same frozen values.
    page(75,47,63,20,dashed=True)
    text(78,52,'Registered protocol (YAML)',8,True)
    text(78,58,'bandpass_hz: [20, 350]',7,font='Courier New')
    text(78,63,'Selection, envelope, scaling, median',7)
    text(75,74,'Study 38: first 5 of 15 eligible cycles',7.5,True)
    text(76,80,'Raw cycle',7.3)
    text(111,80,'5-cycle median',7.3)
    arrow(104.5,94,109.5,.35,name='Within-stage signal transformation')
    text(75,112,'Filter / rectify / Hampel / RMS',7.3)
    text(75,121,'Gait120: 10 LevelWalking cycles',7.5,True)
    text(76,127,'Released envelope',6.9)
    text(111,127,'10-cycle median',7.3)
    arrow(104.5,141,109.5,.35,name='Within-stage signal transformation')
    text(75,159,'Retain the released envelope',7.3)
    # The cycle grid is a data-layout diagram, not a synthetic waveform.
    for i in range(11):
        path([(78+i*5.6,169),(78+i*5.6,173)],'#9DBCCB',.45,name='Common phase-grid diagram')
    path([(78,171),(134,171)],'#9DBCCB',.35,name='Common phase-grid diagram')
    text(75,179,'Cycle maximum / 101 phase points',7.4,True)
    text(75,185,'Pointwise participant median',7.4)
    text(75,191,'Dimensionless; amplitude not pooled',7)

    # 3. A concrete before/after criteria matrix, not an inferred biological result.
    text(144,52,'Saved comparison and reassessment',8,True)
    table(144,57,[27,18,18],[['Check','Before','After'],
          ['Phase grid','Present','Present'],['Processing support','Missing','Linked'],
          ['Pair status','Conditional','Comparable']],rh=9,size=7)
    text(144,101,'Support linked for reassessment',7.8,True)
    page(144,106,29,29,dashed=True)
    text(147,112,'YAML',8,True)
    text(147,119,'Registered',7.5)
    text(147,124,'protocol',7.5)
    text(147,130,'Processing group',6.5)
    text(174,122,'+',11,True)
    page(180,106,27,29)
    text(183,112,'JSON',8,True)
    text(183,119,'Physiological',7)
    text(183,124,'control',7.5)
    text(183,130,'Passed',7.5,True)
    path([(145,149),(147,151),(151,146)],'#2877A4',.8,name='Allowed timing decision')
    text(154,150,'Timing use allowed',9,True,colour='#2877A4')
    path([(145,163),(151,163)],'#8E989F',.7,name='Unestablished amplitude decision')
    text(154,164,'Amplitude not established',8,True)
    text(144,178,'The phase grid was already shared.',7.5)
    text(144,184,'Reassessment adds processing support,',7.3)
    text(144,190,'not a new waveform-alignment step.',7.3)

    # 4. Content-bearing file diagrams. The numeric excerpt is a selected-value summary,
    # not a claim that this is the exact serialized CSV schema.
    rows=list(csv.DictReader((DATA/'waveforms.csv').open()))
    def med(panel,x):
        return next(float(r['value']) for r in rows if r['panel']==panel and r['curve']=='median' and float(r['x'])==x)
    page(213,48,63,47)
    text(216,54,'Curve values (CSV)',8.5,True)
    vals=[['Cycle (%)','Study 38','Gait120']]
    for x in [0,50,100]:
        vals.append([str(x),f"{med('Study 38: five-cycle median',x):.3f}",f"{med('Gait120: ten-cycle median',x):.3f}"])
    table(216,59,[18,19,19],vals,rh=7,size=7)
    text(216,92,'Selected median values',7)
    page(213,102,63,41)
    text(216,108,'Record and decision files (JSON)',8,True)
    text(216,115,'{',15,font='Courier New')
    text(221,115,'Input identities',7.5)
    text(221,121,'Channel / side / task',7.5)
    text(221,127,'Protocol and transformations',7.5)
    text(221,133,'Pair decision and permitted use',7.2)
    text(216,139,'}',15,font='Courier New')
    image(ART,235,148,39,(1220,240,460,400))
    text(213,189,'Linked curves, records and decisions',8,True)
    text(5,204,'Solid arrows: left-to-right processing order. Dashed outlines: analyst settings. Curves: measured data; grey = cycles, colour = median.',7.5)
    c.showPage();c.save()

    writer=PdfWriter()
    pageout=writer.add_blank_page(W*MM,H*MM)
    pageout.merge_page(PdfReader(BUILD/'top.pdf').pages[0])
    placements=[('source38',75,82),('median38',110,82),('source120',75,129),('median120',110,129)]
    curve_count=0
    for name,x,y in placements:
        plotfile=BUILD/'plots'/f'{name}.pdf'
        plot=PdfReader(plotfile).pages[0]
        height=float(plot.mediabox.height)/MM
        pageout.merge_transformed_page(plot,Transformation().translate(x*MM,(H-y-height)*MM))
        with pdfplumber.open(plotfile) as doc:
            p=doc.pages[0]
            for obj in p.curves+p.lines:
                if not obj['stroke']:
                    continue
                assert all(z[0] in ('m','l') for z in obj.get('path',[]))
                pts=[[x*PX+q[0]*4/3,y*PX+q[1]*4/3] for q in obj['pts']]
                objects.append(dict(kind='path',name=name+(' curve' if len(pts)>2 else ' axis'),
                    points=pts,width=obj['linewidth']*4/3,colour=rgb(obj['stroking_color']),dashed=False))
                curve_count+=len(pts)>2
            groups={}
            for char in p.chars:
                key=(round(char['top'],2),char['fontname'],round(char['size'],3))
                groups.setdefault(key,[]).append(char)
            for (top,font,size),chars in groups.items():
                chars.sort(key=lambda q:q['x0'])
                runs=[[]]
                for char in chars:
                    if runs[-1] and char['x0']-runs[-1][-1]['x1']>size*.7:
                        runs.append([])
                    runs[-1].append(char)
                for run in runs:
                    objects.append(dict(kind='text',x=x*PX+run[0]['x0']*4/3,
                        y=y*PX+(top+size*.8)*4/3,text=''.join(q['text'] for q in run),size=size*4/3,
                        bold='Bold' in font,font='Arial',anchor='start',colour=rgb(run[0]['non_stroking_color'])))
    oldpage=PdfReader(OLD/(PREV+'.pdf')).pages[0]
    oldpage.trimbox.lower_left=(0,0)
    oldpage.trimbox.upper_right=(W*MM,(H-211)*MM)
    oldpage.cropbox.lower_left=(0,0)
    oldpage.cropbox.upper_right=(W*MM,(H-211)*MM)
    pageout.merge_page(oldpage)
    writer.add_metadata({'/Author':'Yusuke Matsui','/Title':'Figure 2. Four-stage implementation and local-browser workflow'})
    with (OUT/(STEM+'.pdf')).open('wb') as f:
        writer.write(f)
    objects.extend(gui)
    (BUILD/'scene.json').write_text(json.dumps(dict(width=W*PX,height=H*PX,objects=objects,curves=curve_count),indent=2))

    previous_caption=(OLD/(PREV+'_caption.md')).read_text()
    gui_caption=previous_caption[previous_caption.index('(B–E)'):]
    caption='''## Figure 2. Four-stage implementation and the local-browser workflow

(A) The numbered route reads left to right: (1) identify recordings and attach
applicable documentary context, (2) process the selected waveforms, (3) reassess
the intended use, and (4) retain linked outputs. Within each stage, the items
explain that stage rather than defining another cross-panel route. This worked
example joins saved components of the registered Study 38 participant 1 and
Gait120 S011 pathway for right tibialis anterior activation timing. It is not
an unconfigured new-study or GUI execution. The acquisition and storage artwork
is schematic; it does not document either study's actual electrode placement
or imply a particular database engine. The document and output diagrams show
selected content, not literal file screenshots or complete serialized schemas.

(1) Figshare supplies a MATLAB file for Study 38 and a MATLAB member of a ZIP
release for Gait120. The abridged Europe PMC JATS XML excerpt supports a fixed
presence rule assigning right to the Gait120 surface-EMG channel group.
Agreement with the analyst-maintained project YAML definition corroborates the
value at overlapping scope. That definition is not independent repository
evidence. The saved context includes S011; it supplies meaning, not automatic
permission for scientific reuse. Dashed outlines identify analyst settings.

(2) Native fields identify the first five of 15 Study 38 Walking cycles with
reference-foot side equal to EMG side (RX denotes right), and ten accepted
Gait120 LevelWalking cycles. Study 38 processing uses mean centering,
fourth-order zero-phase 20–350 Hz Butterworth filtering, rectification,
125-ms Hampel filtering at three robust standard deviations, and 125-ms RMS
smoothing. The Gait120 pathway retains the released envelope, already sampled
at 101 phase points. Both pathways use cycle-maximum scaling, linear
interpolation to 101 phase points, rescaling after interpolation, and pointwise
participant medians. The miniatures use the same frozen measured values as
the previous figure: a source cycle and the cycles contributing to each median.
Grey traces show cycles and coloured traces show the median. Source amplitudes
are not comparable and derived scales are dimensionless. The phase-grid sketch
illustrates a common coordinate system rather than an additional measured signal.

(3) The before/after matrix shows that the phase grid already matched at the
initial comparison. Linking the registered protocol and previously passing
physiological control establishes the processing compatibility group.
Repeating comparison and release checks changes the saved pair from
conditionally comparable to comparable, with timing use allowed for both
records. Amplitude comparability remains unestablished. This transition adds
processing support, not another alignment step, independent validation or proof
of biological equivalence. (4) Curve files, recording descriptors, input
identities, processing information and the pair decision remain linked.
The CSV diagram shows median values at 0, 50 and 100% of the cycle, rounded to
three decimals for display. It does not specify the exact export schema.
Cohort-level physiological results are summarized in the proposed Figure 3E.

'''+gui_caption
    (OUT/(STEM+'_caption.md')).write_text(caption)
    evidence=json.loads((OLD/(PREV+'_evidence.json')).read_text())
    evidence.update(version='v016_2026.09.16-r5',registered_example_unchanged=False,
        registered_scientific_values_unchanged=True,gui_layout_unchanged=True,
        main_reading_direction='left_to_right',stage_count=4,curve_count=curve_count,
        illustration_crops=illustration_crops,output_table_values=vals,
        change_scope='A layout and illustrative diagrams; waveform values retained; B-E preserved')
    sources=[OLD/(PREV+'.pdf'),DATA/'waveforms.csv',DATA/'extracted_case.json',ART,
             ROOT/'paper/results/validation/comparability_resolution_study.json',
             ROOT/'scripts/paper/plot_implementation_flow_v016.R',Path(__file__)]
    evidence['sources'] += [dict(path=str(p.relative_to(ROOT)),sha256=hashlib.sha256(p.read_bytes()).hexdigest()) for p in sources]
    (OUT/(STEM+'_evidence.json')).write_text(json.dumps(evidence,indent=2))
    subprocess.run(['pdftoppm','-singlefile','-scale-to','2600','-png',str(OUT/(STEM+'.pdf')),str(OUT/STEM)],check=True)
    subprocess.run(['pdftoppm','-singlefile','-r','150','-x','0','-y','0','-W','1654','-H','1240','-png',str(OUT/(STEM+'.pdf')),str(BUILD/'upper_detail')],check=True)
    print(OUT)


if __name__=='__main__':
    main()
