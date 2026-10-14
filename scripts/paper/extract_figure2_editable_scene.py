"""Extract existing PDF/SVG geometry, without recalculating plotted data."""
import json
import hashlib
import xml.etree.ElementTree as ET
from pathlib import Path
import pdfplumber

ROOT=Path(__file__).resolve().parents[2]
STEM="implementation_reassessment_v012_2026.09.16-r1"
OUT=ROOT/"paper/figures/candidates"/STEM
BUILD=ROOT/"tmp/figure2_pptx_v012"
PREV=ROOT/"paper/figures/candidates/implementation_real_waveforms_v007_2026.09.12-r4"
MM=72/25.4
PX=96/25.4
def colour(v):
    if v is None: return "none"
    if not isinstance(v,(list,tuple)): v=[v]*3
    return "#"+"".join(f"{round(x*255):02X}" for x in v[:3])
scene=[]
for el in ET.parse(OUT/f"{STEM}_layout.svg").getroot():
    tag=el.tag.rsplit("}",1)[-1];a=el.attrib
    if tag=="rect": continue # only the full-page background
    if tag=="text":
        x,y=float(a["x"]),float(a["y"])
        # Recreate the field-value inset as a single native PowerPoint table.
        if 75<=x<=134 and 54<=y<=70: continue
        scene.append(dict(kind="text",x=x*PX,y=y*PX,
            text=el.text or "",size=float(a["font-size"])*PX,
            bold=a.get("font-weight")=="bold",font="Courier New" if a.get("font-family")=="monospace" else "Arial",
            anchor=a.get("text-anchor","start"),colour=a.get("fill","#111111")))
    elif tag=="line":
        x1,y1,x2,y2=[float(a[k]) for k in ["x1","y1","x2","y2"]]
        if 75<=x1<=134 and 75<=x2<=134 and 54<=y1<=70 and 54<=y2<=70: continue
        scene.append(dict(kind="path",points=[[x1*PX,y1*PX],[x2*PX,y2*PX]],
            width=float(a["stroke-width"])*PX,colour=a["stroke"],dashed="stroke-dasharray" in a))
    elif tag=="image":
        scene.append(dict(kind="image",data=a["href"],x=float(a["x"])*PX,y=float(a["y"])*PX,
            w=float(a["width"])*PX,h=float(a["height"])*PX))
    else: raise ValueError(tag)
scene.append(dict(kind="table",x=75*PX,y=54*PX,widths=[24*PX,35*PX],rh=4*PX,
    values=[["Field","Value"],["EmgVarName","Tibialis Anterior"],["EMGSide","RX"],["EMGFreq","1000 Hz"]]))
curve_count=0
for name,x,y in [("source38",6,61),("source120",6,114),("median38",151,60),("median120",151,113)]:
    with pdfplumber.open(PREV/f"{name}.pdf") as doc:
        p=doc.pages[0]
        # The figure's PDF compositor anchors each 79pt chart in a nominal 28mm box.
        ox,oy=x*PX,(y+28-p.height/MM)*PX
        for obj in p.curves+p.lines:
            if not obj["stroke"]: continue
            assert all(z[0] in ("m","l") for z in obj.get("path",[])), name
            points=[[ox+q[0]*4/3,oy+q[1]*4/3] for q in obj["pts"]]
            scene.append(dict(kind="path",name=name+" curve" if len(points)>2 else name+" axis",
                points=points,width=obj["linewidth"]*4/3,colour=colour(obj["stroking_color"]),dashed=False))
            if len(points)>2: curve_count+=1
        # Preserve original labels as text, grouped by baseline/font.
        groups={}
        for c in p.chars:
            key=(round(c["top"],2),c["fontname"],round(c["size"],3))
            groups.setdefault(key,[]).append(c)
        for (top,font,size),chars in groups.items():
            chars.sort(key=lambda c:c["x0"])
            runs=[[]]
            for c in chars:
                if runs[-1] and c["x0"]-runs[-1][-1]["x1"]>size*.7:
                    runs.append([])
                runs[-1].append(c)
            for run in runs:
                label="".join(c["text"] for c in run)
                scene.append(dict(kind="text",x=ox+run[0]["x0"]*4/3,
                    y=oy+(top+size*.8)*4/3,text=label,size=size*4/3,bold="Bold" in font,
                    font="Arial",anchor="start",colour=colour(run[0]["non_stroking_color"])))
BUILD.mkdir(parents=True,exist_ok=True)
(BUILD/"scene.json").write_text(json.dumps(dict(width=280*PX,height=210*PX,objects=scene,curves=curve_count,
    source_sha256=hashlib.sha256((OUT/f"{STEM}.pdf").read_bytes()).hexdigest()),indent=2))
print(json.dumps({"objects":len(scene),"curves":curve_count}))
