#!/usr/bin/env python3
"""Extract source metadata and compose verified real-waveform implementation figure."""
import argparse,csv,hashlib,json,subprocess,sys
from pathlib import Path
import numpy as np
from scipy.io import loadmat
from reportlab.pdfbase import pdfmetrics
from reportlab.pdfbase.ttfonts import TTFont
from pypdf import PdfReader,PdfWriter,Transformation
import generate_implementation_figure as b

ROOT=b.ROOT
sys.path.insert(0,str(ROOT/'src'))
from harmo.physiology_control import _expand_template,_numeric_mat_value,load_physiology_protocol
STEM='implementation_real_waveforms_v007_2026.09.12-r4'
OUT=ROOT/'paper/figures/candidates'/STEM
VER=ROOT/'paper/figures/candidates/waveform_verification_v001_2026.09.12-r1'
b.Y_SCALE=1
BLUE='#A8CADB';PEACH='#EDCAA5'

def text(d,x,y,s,size=8,bold=False,anchor='start'):d.text(x,y,s,size,bold,anchor)
def arrow(d,x,y,xx,yy):d.line(x,y,xx,yy,width=.3,arrow=True)

def card(d,x,y,w,h,title,lines,colour=BLUE):
    d.box(x,y,w,h);d.line(x,y,x+w,y,colour=colour,width=.8)
    text(d,x+2,y+4.8,title,8,True)
    for i,line in enumerate(lines):text(d,x+2,y+10+i*4.4,line,7.5)

def draw(d,e):
    text(d,5,7,'A real EMG reuse case: source fields, waveforms and decisions',10.5,True)
    card(d,5,14,61,23,'Study 38 / Subject1.mat',
         [f'EmgVarName: {e["study38"]["channel"]}',
          f'EMGSide: {e["study38"]["side"]}; EMGFreq: {e["study38"]["sampling_rate_hz"]:g} Hz',
          'Task: Walking; Foot: RX; 5 cycles'])
    card(d,70,14,64,23,'Gait120 / S011 / ProcessedData.mat',
         [f'Column: {e["gait120"]["channel"]}',
          'LevelWalking: Trial01 / Step01 ...',
          f'{e["gait120"]["cycle_count"]} source-accepted cycles'],PEACH)
    card(d,139,14,46,23,'Matched context',
         ['Tibialis anterior / right','Gait120 article: "right leg"','Stored evidence: corroborated'])
    arrow(d,134.5,27,138,27)
    text(d,5,43,'Actual source signals',9,True)
    text(d,67,43,'Declared processing',9,True)
    text(d,131,43,'101-point input curves',9,True)
    # Vector plots occupy x=5/130, y=47/76, w=55, h=28 mm.
    d.box(67,51,55,20,colour=BLUE,width=.4)
    text(d,69,56,'20-350 Hz; rectify; Hampel',7.5)
    text(d,69,60.5,'RMS 125 ms; cycle-max',7.5)
    text(d,69,65,'101-point phase; median',7.5)
    d.box(67,80,55,20,colour=PEACH,width=.4)
    text(d,69,85,'Keep source envelope',7.5)
    text(d,69,89.5,'Cycle-max; 101-point phase',7.5)
    text(d,69,94,'Median across 10 cycles',7.5)
    for y in (61,90):arrow(d,61,y,66,y);arrow(d,123,y,129,y)
    d.line(3,107,187,107,colour=b.GREY,width=.3)
    d.box(5,110,180,8,True)
    text(d,8,115.5,'Analyst request: participant 1 / S011; right tibialis anterior; activation timing',8,True)
    card(d,5,122,51,14,'Before: conditional', ['Processing support missing'],PEACH)
    card(d,64,122,63,14,'Link protocol + validation result', ['Record compatibility; re-evaluate'])
    card(d,135,122,50,14,'After: timing allowed', ['Amplitude: not established'])
    arrow(d,57,129,63,129);arrow(d,128,129,134,129)
    text(d,5,141,'Waveforms re-extracted; decision history from saved run. Individual cycles: grey; median: coloured.',7.5)

def main():
    ap=argparse.ArgumentParser();ap.add_argument('--source-dir',required=True,type=Path);args=ap.parse_args()
    OUT.mkdir(parents=True,exist_ok=True)
    v=json.loads((VER/'verification.json').read_text())
    p=load_physiology_protocol(ROOT/'paper/physiology_positive_control_protocol.yaml')
    sfile=args.source_dir/'Subject1.mat';gfile=args.source_dir/'S011-ProcessedData.mat'
    assert hashlib.sha256(sfile.read_bytes()).hexdigest()==v['study38_sha256']
    assert hashlib.sha256(gfile.read_bytes()).hexdigest()==v['gait120_member_sha256']
    s=loadmat(sfile,squeeze_me=True,struct_as_record=False)['s']
    labels=[str(x).strip() for x in np.atleast_1d(s.EmgVarName)]
    eligible=[(i,x) for i,x in enumerate(np.atleast_1d(s.Data).ravel()) if str(x.Task).strip()=='Walking' and str(x.Foot).strip()==str(s.EMGSide).strip()]
    selected=eligible[:5];assert [i for i,_ in selected]==v['study38_source_cycle_indices_zero_based']
    ci=labels.index('Tibialis Anterior')
    smeta=dict(channel=labels[ci],channel_index_zero_based=ci,side=str(s.EMGSide).strip(),sampling_rate_hz=float(s.EMGFreq),
               eligible_count=len(eligible),selection_rule='first five source-ordered Walking cycles with Foot == EMGSide',
               selected=[dict(source_index_zero_based=i,Task=str(x.Task).strip(),Foot=str(x.Foot).strip(),EMG_shape=list(x.EMG.shape)) for i,x in selected])
    data=loadmat(gfile)
    keys=_expand_template(data,'LevelWalking.{trial}.{step}.EMGs_interpolated',{'trial':r'Trial\d+','step':r'Step\d+'})
    gmeta=dict(channel='TibialisAnterior',cycle_count=len(keys),cycles=[])
    for key,_ in keys:
        array,columns=_numeric_mat_value(data,key)
        assert 'TibialisAnterior' in columns
        gmeta['cycles'].append(dict(key=key,shape=list(array.shape),column_index_zero_based=columns.index('TibialisAnterior'),columns=columns))
    assert len(keys)==10
    report_path=ROOT/'paper/results/validation/literature_semantic_enrichment.json'
    report=json.loads(report_path.read_text())
    claim=next(c for c in report['claims'] if c['claim_id']=='study120-article-emg-side')
    decision=next(c for c in report['decisions'] if c['claim_id']==claim['claim_id'])
    assert claim['value']==['right'] and decision['status']=='corroborated'
    # Verify the short phrase against the hash of the actual stored matched span.
    phrase='different muscles of the right leg'
    digest=hashlib.sha256(json.dumps([phrase],ensure_ascii=False,sort_keys=True,separators=(',',':')).encode()).hexdigest()
    assert digest==claim['evidence_sha256']
    resolution=json.loads((ROOT/'paper/results/validation/comparability_resolution_study.json').read_text())
    pair=resolution['comparisons'][0]
    assert pair['study_38_subject']=='1' and pair['study_120_subject']=='S011' and pair['muscle']=='tibialis anterior'
    assert pair['source_status']=='conditionally_comparable' and pair['resolved_status']=='comparable'
    assert pair['left_release_status']==pair['right_release_status']=='allow'
    assert pair['amplitude_comparability']=='not-established'
    extracted=dict(study38=smeta,gait120=gmeta,literature=dict(phrase=phrase,claim=claim,decision=decision,reextracted_this_turn=False),
                   declared_processing=p['study_38_envelope'],aggregation=p['aggregation'],saved_decision=pair,
                   workflow_boundary='Source fields freshly read; waveform tables previously verified; literature and qualification are saved evidence, not rerun decisions.')
    (OUT/'extracted_case.json').write_text(json.dumps(extracted,indent=2)+'\n')
    (OUT/'waveforms.csv').write_bytes((VER/'waveforms.csv').read_bytes())
    subprocess.run(['Rscript',str(ROOT/'scripts/paper/plot_implementation_case_v007.R'),str(OUT)],check=True)
    for name,file in [('Arial','Arial.ttf'),('Arial-Bold','Arial Bold.ttf')]:pdfmetrics.registerFont(TTFont(name,'/System/Library/Fonts/Supplemental/'+file))
    d=b.Drawing(OUT/f'{STEM}_layout.pdf');draw(d,extracted);d.finish(OUT/f'{STEM}_layout.svg')
    page=PdfReader(OUT/f'{STEM}_layout.pdf').pages[0]
    for name,x,y in [('source38',5,47),('source120',5,76),('median38',130,47),('median120',130,76)]:
        chart=PdfReader(OUT/f'{name}.pdf').pages[0]
        page.merge_transformed_page(chart,Transformation().translate(x*b.MM,(b.H-y-28)*b.MM))
    writer=PdfWriter();writer.add_page(page);writer.add_metadata({'/Title':'Real-source EMG methods example','/Author':'Yusuke Matsui'})
    with (OUT/f'{STEM}.pdf').open('wb') as f:writer.write(f)
    subprocess.run(['pdftoppm','-singlefile','-r','400','-png',str(OUT/f'{STEM}.pdf'),str(OUT/STEM)],check=True)
    sources=[report_path,ROOT/'paper/results/validation/comparability_resolution_study.json',ROOT/'paper/results/validation/physiology_positive_control.json',VER/'verification.json',VER/'waveforms.csv',Path(__file__),ROOT/'scripts/paper/plot_implementation_case_v007.R']
    (OUT/f'{STEM}_evidence.json').write_text(json.dumps(dict(status='candidate_not_adopted',version='v007_2026.09.12-r4',width_mm=b.W,height_mm=b.H,
      minimum_layout_font_pt=7.5,sources=[dict(path=str(f.relative_to(ROOT)),sha256=hashlib.sha256(f.read_bytes()).hexdigest()) for f in sources]),indent=2)+'\n')
    print(json.dumps(dict(output=str(OUT),study38=smeta,gait120_cycle_count=len(keys),literature_phrase_digest_verified=True),indent=2))

if __name__=='__main__':main()
