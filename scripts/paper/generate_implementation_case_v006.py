#!/usr/bin/env python3
"""Source-linked worked case, with measured metrics, without invented waveforms."""
import csv
import hashlib
import json
import re
import subprocess
from pathlib import Path
from xml.sax.saxutils import escape
from reportlab.pdfbase import pdfmetrics
from reportlab.pdfbase.ttfonts import TTFont
from pypdf import PdfReader, PdfWriter, Transformation
import generate_implementation_figure as b

ROOT=b.ROOT
STEM='implementation_real_case_v006_2026.09.12-r3'
OUT=ROOT/'paper/figures/candidates'/STEM
b.Y_SCALE=1
BLUE='#A8CADB'; PEACH='#EDCAA5'

def text(d,x,y,s,size=8,bold=False,anchor='start'):
    d.text(x,y,s,size,bold,anchor)

def arrow(d,x,y,xx,yy):
    d.line(x,y,xx,yy,arrow=True,width=.3)

def doc(d,x,y,w,h,title,lines,colour):
    d.box(x,y,w,h,colour=b.RULE)
    d.line(x,y,x+w,y,colour=colour,width=.9)
    text(d,x+2,y+5,title,8.5,True)
    d.line(x+2,y+7,x+w-2,y+7,colour=b.GREY)
    for i,line in enumerate(lines):text(d,x+2,y+11.5+i*4.5,line,7.5)

def draw(d,pair,left,right):
    text(d,5,7,'One worked case: right tibialis anterior during walking',11,True)
    text(d,5,13,'Study 38 / participant 1  +  Gait120 / S011',8.5)
    text(d,5,22,'1  Reconstruct the declared analysis from its sources',9.5,True)
    doc(d,5,27,52,25,'Study 38: Subject1.mat',
        ['Tibialis Anterior; side RX','Walking; Foot = EMGSide','5 source-ordered cycles'],BLUE)
    doc(d,5,56,52,25,'Gait120: S011',
        ['EMG/ProcessedData.mat','TibialisAnterior; right side','10 accepted walking cycles'],PEACH)
    arrow(d,58,40,64,40);arrow(d,58,68,64,68)
    d.box(65,27,56,25,colour=BLUE,width=.4)
    text(d,68,32,'Raw EMG to envelope',8,True)
    text(d,68,37,'Center; band-pass 20-350 Hz',7.5)
    text(d,68,42,'Rectify; Hampel; RMS 125 ms',7.5)
    text(d,68,47,'Cycle-max; 101 points; median',7.5)
    d.box(65,56,56,25,colour=PEACH,width=.4)
    text(d,68,61,'Retain released envelope',8,True)
    text(d,68,66,'Already processed by source',7.5)
    text(d,68,71,'Cycle-max; 101 points; median',7.5)
    text(d,68,76,'No raw-signal reprocessing',7.5)
    arrow(d,122,40,127,40);arrow(d,122,68,127,68)
    text(d,158,30,'Recorded timing output',8.5,True,'middle')
    # The R-generated vector graph is inserted here after drawing.
    text(d,158,79,'Not a waveform reconstruction',7.5,False,'middle')
    d.line(3,85,187,85,colour=b.GREY,width=.35)
    text(d,5,92,'2  Resolve the missing evidence link, then reassess',9.5,True)
    d.box(5,96,180,9,True)
    text(d,8,102,'Request: participant 1 / S011; right TA; activation timing',8,True)
    doc(d,5,111,51,19,'Before: conditional',
        ['101-point curves already exist','Processing compatibility needed'],PEACH)
    arrow(d,57,120,63,120)
    doc(d,64,111,63,19,'Attach validation evidence',
        ['Frozen protocol + physiological result','Record the supported processing group'],BLUE)
    arrow(d,128,120,134,120)
    doc(d,135,111,50,19,'After: comparable',
        ['Timing release: allowed for both','Amplitude: not established'],BLUE)
    text(d,5,138,'Existing result records; first registered pair; no new physiological validation',7.5)


def main():
    OUT.mkdir(parents=True,exist_ok=True)
    metrics_path=ROOT/'paper/results/validation/physiology_positive_control.json'
    resolution_path=ROOT/'paper/results/validation/comparability_resolution_study.json'
    metrics=json.loads(metrics_path.read_text())
    resolution=json.loads(resolution_path.read_text())
    pair=resolution['comparisons'][0]
    assert (pair['study_38_subject'],pair['study_120_subject'],pair['muscle'])==('1','S011','tibialis anterior')
    rows=[next(x for x in metrics['metrics'] if x['study_id']==s and x['subject_id']==u and x['muscle']=='tibialis anterior') for s,u in [('study_38','1'),('study_120','S011')]]
    assert [x['cycle_count'] for x in rows]==[5,10]
    assert pair['source_status']=='conditionally_comparable' and pair['resolved_status']=='comparable'
    assert pair['conditions_before']==['establish a validated processing compatibility group for activation timing']
    assert pair['conditions_after']==[] and pair['amplitude_comparability']=='not-established'
    assert pair['left_release_status']==pair['right_release_status']=='allow'
    evidence=dict(case=pair,metrics=rows,limitations=['No source waveform arrays available locally.','No waveform inferred from summary metrics.','No independent validation: original registered positive control supplies compatibility evidence.'])
    (OUT/f'{STEM}_case.json').write_text(json.dumps(evidence,indent=2)+'\n')
    with (OUT/'timing.csv').open('w',newline='') as f:
        w=csv.writer(f);w.writerow(['subject','centroid'])
        for label,row in zip(['Study 38 / 1','Gait120 / S011'],rows):w.writerow([label,row['activation_centroid_percent_gait_cycle']])
    subprocess.run(['Rscript',str(ROOT/'scripts/paper/plot_implementation_case_v006.R'),str(OUT)],check=True)
    for name,file in [('Arial','Arial.ttf'),('Arial-Bold','Arial Bold.ttf')]:pdfmetrics.registerFont(TTFont(name,'/System/Library/Fonts/Supplemental/'+file))
    d=b.Drawing(OUT/f'{STEM}_layout.pdf');draw(d,pair,*rows);d.finish(OUT/f'{STEM}_layout.svg')
    background=PdfReader(OUT/f'{STEM}_layout.pdf').pages[0]
    chart=PdfReader(OUT/'timing_plot.pdf').pages[0]
    x,y,w,h=129,34,56,42
    background.merge_transformed_page(chart,Transformation().translate(x*b.MM,(b.H-y-h)*b.MM))
    writer=PdfWriter();writer.add_page(background);writer.add_metadata({'/Title':'Real-source EMG timing case','/Author':'Yusuke Matsui'})
    with (OUT/f'{STEM}.pdf').open('wb') as stream:writer.write(stream)
    subprocess.run(['pdftoppm','-singlefile','-r','300','-png',str(OUT/f'{STEM}.pdf'),str(OUT/STEM)],check=True)
    sources=[metrics_path,resolution_path,ROOT/'paper/physiology_positive_control_protocol.yaml',ROOT/'scripts/paper/run_comparability_resolution_study.py',ROOT/'src/harmo/physiology_control.py',Path(__file__),ROOT/'scripts/paper/plot_implementation_case_v006.R']
    record=dict(version='v006_2026.09.12-r3',status='candidate_not_adopted',raw_waveforms_available=False,width_mm=b.W,height_mm=b.H,minimum_layout_text_pt=7.5,
                sources=[dict(path=str(p.relative_to(ROOT)),sha256=hashlib.sha256(p.read_bytes()).hexdigest()) for p in sources])
    (OUT/f'{STEM}_evidence.json').write_text(json.dumps(record,indent=2)+'\n')
    print(OUT)

if __name__=='__main__':main()
