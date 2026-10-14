"""Replace only the storage artwork with an editable monochrome relationship icon."""
import hashlib
import json
import subprocess
from pathlib import Path

from pypdf import PdfReader, PdfWriter
from reportlab.pdfgen import canvas

ROOT=Path(__file__).resolve().parents[2]
PREV='figure2_implementation_gui_v016_2026.09.16-r5'
STEM='figure2_implementation_gui_v017_2026.09.16-r6'
OLD=ROOT/'paper/figures/candidates'/PREV
OUT=ROOT/'paper/figures/candidates'/STEM
BUILD=ROOT/'tmp'/STEM
MM,PX=72/25.4,96/25.4
W,H=280,357


def main():
    OUT.mkdir(parents=True,exist_ok=True)
    BUILD.mkdir(parents=True,exist_ok=True)
    scene=json.loads((ROOT/'tmp'/PREV/'scene.json').read_text())
    targets=[o for o in scene['objects'] if o['kind']=='image' and abs(o['x']/PX-235)<1e-6 and abs(o['y']/PX-148)<1e-6]
    assert len(targets)==1
    scene['objects'].remove(targets[0])
    c=canvas.Canvas(str(BUILD/'icon.pdf'),pagesize=(W*MM,H*MM),invariant=1)
    icon=[]

    def line(points,name,width=.38):
        c.setStrokeColorRGB(.067,.067,.067)
        c.setLineWidth(width*MM)
        c.setLineJoin(1)
        c.setLineCap(1)
        p=c.beginPath()
        for i,(x,y) in enumerate(points):
            (p.moveTo if i==0 else p.lineTo)(x*MM,(H-y)*MM)
        c.drawPath(p,stroke=1,fill=0)
        icon.append(dict(kind='path',name='Linked-output icon: '+name,
            points=[[x*PX,y*PX] for x,y in points],width=width*PX,colour='#111111',dashed=False))

    for x,name in [(235,'curve'),(249,'record'),(263,'decision')]:
        line([(x,154),(x+7.5,154),(x+10,156.5),(x+10,173),(x,173),(x,154)],name+' document')
        line([(x+7.5,154),(x+7.5,156.5),(x+10,156.5)],name+' fold')
        line([(x+5,173),(x+5,179)],name+' identity link',.30)
    line([(240,179),(268,179)],'shared identity link',.30)
    # A conventional signal symbol, not a data trace or result.
    line([(236.5,164),(238,164),(239,160),(240.3,168),(241.5,162),(242.4,164),(243.5,164)],'curve symbol')
    for y in [160,164,168]:
        line([(251, y),(257,y)],'record field',.30)
    line([(253,160),(253,168)],'record columns',.30)
    # A check plus a held/undefined dash denotes decisions, not blanket approval.
    line([(265,161),(266.3,162.4),(270,159)],'decision check')
    line([(265.5,167),(270,167)],'decision dash')
    c.showPage();c.save()
    scene['objects'].extend(icon)
    (BUILD/'scene.json').write_text(json.dumps(scene,indent=2))

    writer=PdfWriter(clone_from=OLD/(PREV+'.pdf'))
    page=writer.pages[0]
    content=page.get_contents()
    removed=[]
    # The two source illustration crops share an image object. Remove only the
    # paint operation with the verified storage-crop transform, not the EMG leg.
    operations=[]
    for i,(args,op) in enumerate(content.operations):
        previous=content.operations[i-1] if i else ([],b'')
        is_target=op==b'Do' and previous[1]==b'cm' and all(
            abs(float(v)-expected)<.002 for v,expected in zip(previous[0],[412.6443,0,0,220.141,372.9408,429.9788]))
        if is_target:
            removed.append(dict(index=i,image=str(args[0])))
        else:
            operations.append((args,op))
    assert len(removed)==1,removed
    content.operations=operations
    page.replace_contents(content)
    page.merge_page(PdfReader(BUILD/'icon.pdf').pages[0])
    with (OUT/(STEM+'.pdf')).open('wb') as f:
        writer.write(f)

    caption=(OLD/(PREV+'_caption.md')).read_text()
    caption=caption.replace('The acquisition and storage artwork\n+is schematic; it does not document either study\'s actual electrode placement\n+or imply a particular database engine.'.replace('\n+','\n'),
        'The acquisition artwork is schematic and does not document either study\'s\nactual electrode placement. The monochrome linked-document icon represents\ncurves, records and decisions sharing an identity. Its waveform symbol is\nillustrative, not a measured trace or a claim of scientific approval.')
    assert 'monochrome linked-document icon' in caption
    (OUT/(STEM+'_caption.md')).write_text(caption)
    evidence=json.loads((OLD/(PREV+'_evidence.json')).read_text())
    evidence.update(version='v017_2026.09.16-r6',change_scope='Storage illustration only: monochrome native linked-document icon',
        removed_image_operations=removed,other_scene_objects_unchanged=True,
        new_icon_objects=len(icon),icon_bounds_mm=[235,148,39,34],
        icon_note='Schematic curve, record and decision symbols linked by shared identity; no new data or approval.')
    evidence['sources'] += [dict(path=str(p.relative_to(ROOT)),sha256=hashlib.sha256(p.read_bytes()).hexdigest())
        for p in [OLD/(PREV+'.pdf'),Path(__file__)]]
    (OUT/(STEM+'_evidence.json')).write_text(json.dumps(evidence,indent=2))
    subprocess.run(['pdftoppm','-singlefile','-scale-to','2600','-png',str(OUT/(STEM+'.pdf')),str(OUT/STEM)],check=True)
    subprocess.run(['pdftoppm','-singlefile','-r','180','-x','1481','-y','1035','-W','490','-H','345',
                    '-png',str(OUT/(STEM+'.pdf')),str(OUT/(STEM+'_detail'))],check=True)
    print(OUT)


if __name__=='__main__':
    main()
