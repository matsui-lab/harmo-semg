#!/usr/bin/env python3
"""Concrete test objects, comparators and saved diagnostic excerpts."""
import hashlib
import json
import subprocess
from pathlib import Path
from reportlab.pdfbase import pdfmetrics
from reportlab.pdfbase.ttfonts import TTFont
from pypdf import PdfReader, PdfWriter, Transformation
import generate_implementation_figure as b

ROOT=b.ROOT
STEM='validation_design_v002_2026.09.12-r2'
OUT=ROOT/'paper/figures/candidates'/STEM
b.W,b.H,b.Y_SCALE=320,240,1


def txt(d,x,y,s,size=9,bold=False):
    d.text(x,y,s,size,bold)


def arr(d,x,y,z,yy=None):
    d.line(x,y,z,y if yy is None else yy,width=.3,arrow=True)


def block(d,x,y,w,lines,colour=None):
    h=5+5*len(lines)
    d.box(x,y,w,h,width=.25)
    if colour: d.line(x,y,x+w,y,colour=colour,width=.8)
    for i,s in enumerate(lines):txt(d,x+2,y+5+5*i,s,9,i==0)


def card(d,x,y,num,title,subtitle):
    d.box(x,y,152,67,colour='#AEB8BF',width=.25)
    d.line(x,y,x+152,y,colour=b.BLUE,width=.85)
    txt(d,x+4,y+6,num+'  '+title,11,True)
    txt(d,x+4,y+12,subtitle,9)


def load(name):return json.loads((ROOT/'paper/results/validation'/f'{name}.json').read_text())


def draw(d,e):
    txt(d,5,8,'What was tested, against what, and how it was judged',14,True)
    txt(d,5,15,'Eight-study corpus and purpose-specific subsets; held-out HDF5 release tested separately. Saved examples illustrate the tests.',9.5)
    x,y=5,22
    card(d,x,y,'1','Same input, repeated conversion','78 settings / 8 studies; one source member per setting [A, D, F]')
    block(d,x+4,y+23,26,['Same','source file'])
    arr(d,x+30,y+30,x+41,y+22);arr(d,x+30,y+30,x+41,y+40)
    block(d,x+43,y+16,29,['Convert','Run 1'],b.BLUE)
    block(d,x+43,y+36,29,['Convert','Run 2'],b.PEACH)
    arr(d,x+73,y+23,x+81);arr(d,x+73,y+43,x+81)
    txt(d,x+84,y+22,'Compare values + source identity',9,True)
    txt(d,x+84,y+28,'Saved corpus-output hash prefixes:',8)
    txt(d,x+84,y+35,e['hashes'][0][:12]+'...   Run 1',9)
    txt(d,x+84,y+42,e['hashes'][1][:12]+'...   Run 2',9)
    txt(d,x+84,y+48,'Repeat across 2 environments x 3 runs',8)
    d.line(x+4,y+53,x+148,y+53,colour=b.GREY)
    txt(d,x+4,y+59,'Held-out: 13 participants x 16 HDF5 trials',8.7,True)
    txt(d,x+4,y+64,'New reader -> same converter; check core edits and missing-cell retention',8.5)

    x,y=163,22
    card(d,x,y,'2','Same recording, different file formats','Study 31: 16 participants, fixed speed V25 / trial T1 [B]')
    for xx,label in [(x+4,'ASCII'),(x+54,'MAT'),(x+104,'C3D')]:
        block(d,xx,y+18,43,[label,'Same participant / trial'])
    txt(d,x+4,y+38,'Match channels; compare values, timestamps and events',9,True)
    d.table(x+4,y+42,[50,33,33,28],['Saved pilot: EMG','Max. error','Tolerance','Decision'],[
        ['Participant 1: ASCII-MAT',f"{e['mat']['max_numeric_error']:.0f}",f"{e['mat']['numeric_tolerance']:.1e}",'Equivalent'],
        ['Participant 1: ASCII-C3D',f"{e['c3d']['max_numeric_error']:.2e}",f"{e['c3d']['numeric_tolerance']:.2e}",'Not equivalent']],row_h=5,size=8.4)
    txt(d,x+4,y+63,'Time: <=0.5 sample; events: <=1 sample; absent sources remain explicit',8.5)

    x,y=5,93
    card(d,x,y,'3','Known metadata, deliberately changed fields','Controlled fixture: 8 single faults + unchanged control [C]')
    block(d,x+4,y+18,32,['Expected unit','V'])
    block(d,x+45,y+18,30,['Unchanged','V'],b.BLUE)
    block(d,x+45,y+36,30,['Unit fault','mV'],b.PEACH)
    arr(d,x+36,y+25,x+43);arr(d,x+36,y+25,x+43,y+43)
    arr(d,x+76,y+25,x+84);arr(d,x+76,y+43,x+84)
    d.box(x+86,y+18,29,33,width=.25)
    txt(d,x+88,y+30,'Same unit check',8.5,True)
    txt(d,x+88,y+36,'Expected: V',8.5)
    arr(d,x+116,y+25,x+123)
    txt(d,x+124,y+24,'No alarm',9,True)
    txt(d,x+124,y+43,'Fault flagged',9,True)
    arr(d,x+116,y+43,x+123)
    txt(d,x+4,y+56,'Also change side, muscle, rate, time, coordinates, channel or source hash.',8.4)
    txt(d,x+4,y+63,'Real-source evidence and purpose tests are described in the caption.',8.4)

    x,y=163,93
    card(d,x,y,'4','Complete records, not selected samples','Study 38: all eligible same-side Walking records / 10 participants [H]')
    block(d,x+4,y+19,39,['Frozen source cohort','No truncation','No resampling'])
    arr(d,x+44,y+29,x+52)
    txt(d,x+55,y+22,'Write signal + record tables twice',9,True)
    d.table(x+55,y+26,[34,18,18,18],['Check','Plan','Run 1','Run 2'],[
        ['Records',130,e['full'][0]['record_count'],e['full'][1]['record_count']],
        ['Channels / record',8,8,8],
        ['Timepoints',138638,138638,138638]],row_h=5,size=8.5)
    txt(d,x+4,y+53,'Compare cohort counts, scientific content and table bytes',9,True)
    txt(d,x+4,y+59,'Then query: count / participant filter / muscle summary / join',8.8)
    txt(d,x+4,y+64,'25 timed repetitions per query after 3 warm-ups; local machine only',8.5)

    x,y=5,164
    card(d,x,y,'5','Does processed EMG retain expected timing?','Study 38 + Gait120: 10 participants each; tibialis anterior + medial gastrocnemius [G]')
    txt(d,x+4,y+18,'Actual example: Study 38 / participant 1 / tibialis anterior',8.5,True)
    # The ggplot2 waveform is merged into this reserved area after drawing.
    txt(d,x+86,y+25,'1  Peak in expected window?',9,True)
    txt(d,x+89,y+31,'0-20% or 60-100% of cycle',8.5)
    txt(d,x+86,y+39,'2  Active > reference phase?',9,True)
    txt(d,x+89,y+45,f"Saved log2 contrast: {e['phys']['log2_phase_contrast']:.2f}",8.5)
    txt(d,x+4,y+59,'Apply to every participant -> compare study medians',9,True)
    txt(d,x+4,y+64,'Pattern >=70%; centroid gap <=15% of cycle. Timing only, not amplitude pooling.',8.3)

    x,y=163,164
    card(d,x,y,'6','Can missing evidence still permit mechanics?','Study 31: 16 participants x 7 speeds x 3 trials = 336 identities [E]')
    block(d,x+4,y+19,40,['Dynamic trial','Participant 1','V1 / T1'])
    arr(d,x+45,y+29,x+52)
    block(d,x+54,y+19,46,['Calibration prerequisite','Static trial OR','traceable scaled model'])
    arr(d,x+101,y+29,x+109)
    txt(d,x+113,y+24,'Unavailable',9,True)
    txt(d,x+113,y+31,'in release',9)
    d.line(x+125,y+34,x+125,y+39,arrow=True)
    txt(d,x+4,y+46,'Saved trial ledger: mechanics metrics = null / unassessed',9,True)
    txt(d,x+4,y+53,'Check that absent calibration cannot be treated as validated mechanics.',8.5)
    txt(d,x+4,y+60,'Saved release decision: deny',10,True)
    txt(d,5,238,'Independent tests, not a serial cohort. Saved excerpts are illustrative diagnostics, not new validation; full outcomes remain in the Results figures.',8.7)


def main():
    OUT.mkdir(parents=True,exist_ok=True)
    for n,f in [('Arial','Arial.ttf'),('Arial-Bold','Arial Bold.ttf')]:
        pdfmetrics.registerFont(TTFont(n,'/System/Library/Fonts/Supplemental/'+f))
    fmt=load('format_fidelity')['comparisons'];repro=load('reproducibility')
    select=lambda fmtname:next(r for r in fmt if r['subject_id']=='sub-01' and r['signal']=='emg' and r['reference_format']=='ascii' and r['candidate_format']==fmtname)
    phys=next(r for r in load('physiology_positive_control')['metrics'] if r['study_id']=='study_38' and r['subject_id']=='1' and r['muscle']=='tibialis anterior')
    e={'hashes':repro['runs'][0]['normalized_record_sha256'],
       'mat':select('mat'),'c3d':select('c3d'),
       'full':load('full_record_materialization_benchmark')['runs'],'phys':phys,
       'mechanics_trial':load('opensim_quality')[0],
       'mechanics_export':load('g6_mechanics_disposition')['default_mechanics_export']}
    assert e['hashes'][0]==e['hashes'][1]
    assert next(r for r in load('fault_detection')['fault_detection'] if r['fault']=='unit')['detected']
    assert load('fault_detection')['clean_specificity']==1
    assert e['phys']['peak_timing_percent_gait_cycle']==63
    assert e['mechanics_export']=='deny' and e['mechanics_trial']['outcome']=='unassessed'
    assert e['mechanics_trial']['subject_id']=='1' and e['mechanics_trial']['speed']=='V1'
    assert e['full'][0]['timepoint_count']==e['full'][1]['timepoint_count']==138638
    curve=ROOT/'paper/figures/candidates/waveform_verification_v001_2026.09.12-r1/subject_curves.csv'
    subprocess.run(['/usr/local/bin/Rscript',str(ROOT/'scripts/paper/plot_validation_design_v002.R'),str(curve),str(OUT/'timing_example.pdf')],check=True)
    d=b.Drawing(OUT/f'{STEM}_layout.pdf');d.pdf.setTitle('Objects, comparators and checks in EMG validation');draw(d,e)
    d.finish(OUT/f'{STEM}_layout.svg')
    page=PdfReader(OUT/f'{STEM}_layout.pdf').pages[0]
    page.merge_transformed_page(PdfReader(OUT/'timing_example.pdf').pages[0],Transformation().translate(9*b.MM,(240-185-34)*b.MM))
    writer=PdfWriter();writer.add_page(page);writer.add_metadata({'/Title':'Concrete tests of EMG harmonization','/Author':'Yusuke Matsui'})
    with (OUT/f'{STEM}.pdf').open('wb') as f:writer.write(f)
    subprocess.run(['pdftocairo','-svg',str(OUT/f'{STEM}.pdf'),str(OUT/f'{STEM}.svg')],check=True)
    subprocess.run(['pdftoppm','-singlefile','-r','300','-png',str(OUT/f'{STEM}.pdf'),str(OUT/STEM)],check=True)
    (OUT/'verified_excerpts.json').write_text(json.dumps(e,indent=2)+'\n')
    sources=[f'paper/results/validation/{n}.json' for n in ['format_fidelity','reproducibility','physiology_positive_control','full_record_materialization_benchmark','opensim_quality','g6_mechanics_disposition','fault_detection']]
    sources+=['paper/paper.md','paper/physiology_positive_control_protocol.yaml','paper/full_record_materialization_protocol.yaml','src/harmo/validation_study.py',str(curve.relative_to(ROOT)),'scripts/paper/generate_validation_design_v002.py','scripts/paper/plot_validation_design_v002.R','scripts/paper/generate_implementation_figure.py']
    (OUT/f'{STEM}_evidence.json').write_text(json.dumps({'version':'v002_2026.09.12-r2','status':'candidate_not_adopted','width_mm':320,'height_mm':240,'sources':[{'path':p,'sha256':hashlib.sha256((ROOT/p).read_bytes()).hexdigest()} for p in sources]},indent=2)+'\n')
    print(OUT/f'{STEM}.png')


if __name__=='__main__':main()
