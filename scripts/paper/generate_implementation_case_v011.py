#!/usr/bin/env python3
"""Source logos and verified file-content insets in the integrated flow."""
import base64,csv,hashlib,json,subprocess
from pathlib import Path
from PIL import Image
from reportlab.pdfbase import pdfmetrics
from reportlab.pdfbase.ttfonts import TTFont
from reportlab.lib.colors import HexColor
from pypdf import PdfReader,PdfWriter,Transformation
import generate_implementation_case_v010 as old

b=old.b; ROOT=old.ROOT; PREV=old.PREV
STEM='implementation_content_insets_v011_2026.09.12-r8'
OUT=ROOT/'paper/figures/candidates'/STEM
LOGOS=ROOT/'paper/figures/assets/repository_logos'
t,route,box=old.t,old.route,old.box

def logo(d,path,x,y,w):
    with Image.open(path) as im: h=w*im.height/im.width
    d.pdf.drawImage(str(path),x*b.MM,(b.H-y-h)*b.MM,width=w*b.MM,height=h*b.MM,mask='auto')
    data=base64.b64encode(path.read_bytes()).decode()
    d.svg.append(f'<image x="{x}" y="{y}" width="{w}" height="{h}" href="data:image/png;base64,{data}"/>')

def code(d,x,y,lines,size=7.5,leading=3.5):
    for i,line in enumerate(lines):
        yy=y+i*leading
        d.pdf.setFillColor(HexColor(b.INK));d.pdf.setFont('Courier',size)
        d.pdf.drawString(x*b.MM,(b.H-yy)*b.MM,line)
        from xml.sax.saxutils import escape
        d.svg.append(f'<text x="{x}" y="{yy}" font-family="monospace" font-size="{size/b.MM}">{escape(line)}</text>')

def doc(d,x,y,w,h):
    # Folded page boundary, used only around actual content excerpts.
    route(d,[(x,y),(x+w-3,y),(x+w,y+3),(x+w,y+h),(x,y+h),(x,y)],arrow=False)
    route(d,[(x+w-3,y),(x+w-3,y+3),(x+w,y+3)],arrow=False)

def draw(d,e):
    t(d,5,8,'From public files and article evidence to a use-specific decision',12,True)
    t(d,5,21,'PUBLIC RECORDS',9,True);t(d,149,21,'COMPUTED CURVES',9,True)
    box(d,73,14,65,14,dashed=True)
    t(d,76,19,'Protocol | YAML excerpt',9,True)
    code(d,76,24,['bandpass_hz: [20.0, 350.0]'])
    box(d,70,30,71,100,dashed=True);route(d,[(105,28),(105,30)])

    for y,label,sub,col in [(30,'MAT','Study 38 / Subject1.mat',b.BLUE),
                             (82,'ZIP > MAT','S011/EMG/ProcessedData.mat',b.PEACH)]:
        box(d,5,y,57,45,col)
        logo(d,LOGOS/'figshare-logo.png',8,y+2,6)
        t(d,16,y+6,'Figshare | '+label,9,True)
        t(d,8,y+11,sub,8)
    box(d,73,33,65,46);t(d,76,38,'Read fields; select cycles',9,True)
    d.table(76,41,[21,38],['Field','Value'],[
        ['EmgVarName',e['fields']['channel']],['EMGSide',e['fields']['side']],['EMGFreq','1000 Hz']],row_h=4,size=7.5)
    for i in range(15):
        x=78+i*2.35
        d.line(x,59.5,x,63,colour=b.BLUE if i<5 else b.GREY,width=.65)
    t(d,116,62.5,'First 5 of 15',7.5)
    t(d,76,66.5,'Walking; Foot = EMGSide',7.5)
    t(d,76,72,'20-350 Hz; rectify; Hampel; RMS',8)
    t(d,76,77,'Cycle-max > phase > median',8)
    box(d,73,82,65,46);t(d,76,88,'Decode table; select named column',8.5,True)
    d.table(76,92,[17,42],['Index','TibialisAnterior'],
            [[r['x'],f"{float(r['value']):.6f}"] for r in e['samples']],row_h=4,size=7.5)
    t(d,76,113,'10 accepted cycles x 101 points',8)
    t(d,76,119,'Retain released envelope',8)
    t(d,76,124,'Cycle-max > phase > median',8)
    for y,col,name in [(30,b.BLUE,'Study 38'),(82,b.PEACH,'Gait120')]:
        box(d,149,y,60,45,col)
        t(d,152,y+6,name+' | CSV + record JSON',8.5,True)
    for y in (53,105):
        route(d,[(62.5,y),(72,y)]);route(d,[(138.5,y),(148,y)])

    box(d,5,135,57,35)
    logo(d,LOGOS/'europe-pmc-basic-rgb.png',9,139,8)
    t(d,22,143,'Europe PMC',9,True)
    t(d,22,148,'JATS XML | PMC12177048',7.5)
    t(d,8,154,'XML paragraph (abridged)',7.5)
    doc(d,8,156,51,12)
    code(d,10,160,['<p>... different muscles', 'of the right leg ...</p>'],7.5)
    box(d,5,177,57,22)
    t(d,8,182,'Project repository | YAML',9,True)
    code(d,8,188,['column_name: TibialisAnterior','side: Right'],7.5)
    box(d,73,136,65,63);t(d,76,142,'Extract and reconcile',9,True)
    # A small document-and-lens cue is tied to the literal matching operation.
    doc(d,77,147,8,11)
    for yy in (150,152,154):d.line(79,yy,83,yy,width=.18)
    d.pdf.setFillColor(HexColor('#FFFFFF'));d.pdf.setStrokeColor(HexColor(b.RULE))
    d.pdf.circle(85*b.MM,(b.H-155)*b.MM,2*b.MM,stroke=1,fill=0)
    d.svg.append(f'<circle cx="85" cy="155" r="2" fill="none" stroke="{b.RULE}" stroke-width=".25"/>')
    d.line(86.5,156.5,89,159,width=.3)
    t(d,92,150,'Select section',8)
    t(d,92,156,'Match fixed phrase',8)
    t(d,76,166,'Presence rule > value: right',8)
    d.line(76,170,135,170,dashed=True)
    t(d,76,176,'Capture YAML side: Right',8)
    t(d,76,182,'Compare value + declared scope',8)
    t(d,76,191,'Keep source, section and text hash',8)
    route(d,[(62.5,152),(72,152)]);route(d,[(62.5,188),(72,188)])
    box(d,149,147,60,44);t(d,152,153,'Claim | JSON excerpt',9,True)
    doc(d,152,156,54,25)
    code(d,155,161,['{','  "value": ["right"],','  "scope_level":','    "channel-group"','}'])
    t(d,152,187,'Agreement: corroborated',8.5,True)
    route(d,[(138.5,168),(148,168)])

    box(d,224,24,51,35,dashed=True)
    t(d,227,30,'Analyst | Python > JSON',8.5,True)
    t(d,227,36,'Right tibialis anterior; 1 / S011',7.5)
    code(d,227,42,['{',' "intended_use":','   "activation-timing"','}'])
    box(d,224,70,51,28)
    t(d,227,76,'Prior test | JSON excerpt',8.5,True)
    code(d,227,82,['{',' "positive_control_pass":','   true','}'])
    t(d,227,96,'+ linked YAML protocol',7.5)
    box(d,224,111,51,53);t(d,227,117,'Evaluate declared use',9,True)
    for y,s in [(126,'Muscle + side'),(132,'Gait-phase reference'),(138,'Processing compatibility')]:
        d.line(227,y-2,228,y-1,width=.3);d.line(228,y-1,230,y-4,width=.3)
        t(d,232,y,s,8)
    d.line(227,142,272,142,dashed=True)
    t(d,227,148,'Link prior test; record support;',7.5)
    t(d,227,155,'repeat comparison checks',8)
    box(d,224,175,51,31)
    t(d,227,181,'Decision | JSON excerpt',8.5,True)
    code(d,227,186,['{',' "resolved_status":','   "comparable",',' "amplitude_comparability":','   "not-established"','}'],7.5,3.5)
    route(d,[(209.5,54),(216,54),(216,124),(223,124)])
    route(d,[(209.5,106),(216,106)],arrow=False)
    route(d,[(249.5,98),(249.5,110)])
    route(d,[(275,42),(278,42),(278,124),(275.5,124)])
    route(d,[(209.5,168),(219,168),(219,151),(223,151)],dashed=True)
    route(d,[(249.5,164),(249.5,174)])
    t(d,5,208,'Verified excerpts; grey = cycles, colour = median. Dashed context link: supporting evidence. Saved decisions, not new validation.',7.5)

def main():
    OUT.mkdir(parents=True,exist_ok=True)
    for name,file in [('Arial','Arial.ttf'),('Arial-Bold','Arial Bold.ttf')]:
        pdfmetrics.registerFont(TTFont(name,'/System/Library/Fonts/Supplemental/'+file))
    case=json.loads((PREV/'extracted_case.json').read_text())
    claim=case['literature']['claim'];decision=case['saved_decision']
    assert claim['value']==['right'] and claim['scope_level']=='channel-group'
    assert decision['resolved_status']=='comparable' and decision['amplitude_comparability']=='not-established'
    result_path=ROOT/'paper/results/validation/physiology_positive_control.json'
    assert json.loads(result_path.read_text())['positive_control_pass'] is True
    xml=Path('/tmp/harmo-waveform-check.Zu4b6q/Gait120-fullText.xml')
    assert hashlib.sha256(xml.read_bytes()).hexdigest()==claim['source_sha256']
    import xml.etree.ElementTree as ET
    root=ET.fromstring(xml.read_bytes())
    paragraphs=[p for s in root.findall('.//sec') if s.findtext('title')=='Experimental preparation' for p in s.findall('./p')]
    assert any('different muscles of the right leg' in ''.join(p.itertext()) for p in paragraphs)
    wave=ROOT/'paper/figures/candidates/waveform_verification_v001_2026.09.12-r1/waveforms.csv'
    with wave.open() as f:samples=[r for r in csv.DictReader(f) if r['panel']=='Gait120: first released envelope (sample)' and r['curve']=='source'][:3]
    excerpts={'fields':case['study38'],'samples':samples,'claim':{k:claim[k] for k in ['value','scope_level']},
              'decision':{k:decision[k] for k in ['resolved_status','amplitude_comparability']},
              'prior_test':{'positive_control_pass':True},'article_xml_sha256':claim['source_sha256'],
              'request':{'intended_use':'activation-timing'},'request_source':'Programmatically constructed resolver configuration; not an independently archived request file.'}
    (OUT/'verified_excerpts.json').write_text(json.dumps(excerpts,indent=2)+'\n')
    d=b.Drawing(OUT/f'{STEM}_layout.pdf');draw(d,excerpts);d.finish(OUT/f'{STEM}_layout.svg')
    page=PdfReader(OUT/f'{STEM}_layout.pdf').pages[0]
    for name,x,y in [('source38',6,44),('source120',6,96),('median38',151,43),('median120',151,95)]:
        page.merge_transformed_page(PdfReader(PREV/f'{name}.pdf').pages[0],Transformation().translate(x*b.MM,(b.H-y-28)*b.MM))
    w=PdfWriter();w.add_page(page);w.add_metadata({'/Title':'Source logos and actual content in EMG processing','/Author':'Yusuke Matsui'})
    with (OUT/f'{STEM}.pdf').open('wb') as f:w.write(f)
    subprocess.run(['pdftoppm','-singlefile','-r','350','-png',str(OUT/f'{STEM}.pdf'),str(OUT/STEM)],check=True)
    prev10=ROOT/'paper/figures/candidates/implementation_integrated_flow_v010_2026.09.12-r7'
    ev=json.loads((prev10/(prev10.name+'_evidence.json')).read_text())
    sources=[ROOT/a['path'] for a in ev['sources']]+[Path(__file__),result_path,LOGOS/'figshare-logo.png',LOGOS/'europe-pmc-basic-rgb.png',OUT/'verified_excerpts.json']
    (OUT/f'{STEM}_evidence.json').write_text(json.dumps({'version':'v011_2026.09.12-r8','status':'candidate_not_adopted',
        'scope':'Real file-content insets and official logo assets. Previous scientific results unchanged.',
        'sources':[{'path':str(p.relative_to(ROOT)),'sha256':hashlib.sha256(p.read_bytes()).hexdigest()} for p in sources]},indent=2)+'\n')
    print(OUT/f'{STEM}.png')

if __name__=='__main__':main()
