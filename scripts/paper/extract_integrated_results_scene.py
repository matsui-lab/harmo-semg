"""Keep ggplot2 geometry as editable PowerPoint shapes and text."""
import json
import sys
from pathlib import Path
import pdfplumber
ROOT=Path(__file__).resolve().parents[2]
STEM=sys.argv[1] if len(sys.argv)>1 else 'figure3_integrated_results_v001_2026.09.16-r1'
OUT=ROOT/'paper/figures/candidates'/STEM
BUILD=ROOT/'tmp'/STEM
def colour(v):
    if v is None:return 'none'
    if not isinstance(v,(tuple,list)):v=[v]*3
    return '#'+''.join(f'{max(0,min(255,round(c*255))):02X}' for c in v[:3])
def main():
    objects=[]
    with pdfplumber.open(OUT/(STEM+'.pdf')) as doc:
        p=doc.pages[0]
        for z in p.rects+p.lines+p.curves:
            if z['width']>p.width+1 or z['height']>p.height+1:continue
            if z.get('fill') and colour(z.get('non_stroking_color'))=='#FFFFFF':continue
            commands=[];current=None
            for c in z.get('path',[]):
                if c[0] in ('m','l'):
                    current=c[1];commands.append([c[0],*current])
                elif c[0]=='c':
                    p0=current;p1,p2,p3=c[1:]
                    for n in range(1,17):
                        t=n/16;v=1-t
                        commands.append(['l',*[v**3*p0[k]+3*v*v*t*p1[k]+3*v*t*t*p2[k]+t**3*p3[k] for k in (0,1)]])
                    current=p3
                elif c[0]=='h':commands.append(['h'])
                else:raise ValueError(c)
            if not commands:
                commands=[['m' if i==0 else 'l',*xy] for i,xy in enumerate(z['pts'])]
            objects.append(dict(kind='custom',commands=[[c[0],*[v*4/3 for v in c[1:]]]for c in commands],
                fill=colour(z['non_stroking_color'])if z.get('fill')else 'none',
                colour=colour(z['stroking_color'])if z.get('stroke')else 'none',width=z.get('linewidth',0)*4/3))
        groups={}
        for c in p.chars:
            key=(round(c['top'],2),c['fontname'],round(c['size'],3))
            groups.setdefault(key,[]).append(c)
        for (top,font,size),chars in groups.items():
            chars.sort(key=lambda c:c['x0']);runs=[[]]
            for c in chars:
                if runs[-1] and c['x0']-runs[-1][-1]['x1']>size*.65:runs.append([])
                runs[-1].append(c)
            for run in runs:
                objects.append(dict(kind='text',text=''.join(c['text']for c in run),x=run[0]['x0']*4/3,y=(top+size*.8)*4/3,
                    size=size*4/3,bold='Bold'in font,font='Arial',anchor='start',colour=colour(run[0]['non_stroking_color']),text_width=(run[-1]['x1']-run[0]['x0'])*4/3+2))
        scene=dict(width=p.width*4/3,height=p.height*4/3,objects=objects)
    BUILD.mkdir(parents=True,exist_ok=True);(BUILD/'scene.json').write_text(json.dumps(scene,indent=2))
    print(len(objects))
if __name__=='__main__':main()
