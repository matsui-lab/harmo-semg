#!/usr/bin/env python3
"""Draw a source-linked implementation candidate, without renumbering the paper."""
import argparse
import csv
import hashlib
import io
import json
import math
from pathlib import Path
import subprocess
import sys
from xml.sax.saxutils import escape

from reportlab.pdfgen import canvas
from reportlab.pdfbase import pdfmetrics
from reportlab.pdfbase.ttfonts import TTFont
from reportlab.lib.colors import HexColor

ROOT = Path(__file__).resolve().parents[2]
VERSION = (ROOT / "paper/figures/candidates/IMPLEMENTATION_FIGURE_VERSION").read_text().strip()
STEM = f"implementation_four_panel_{VERSION}"
MM = 72 / 25.4
W, H = 190, 142.5
# Compress layout coordinates only: glyph sizes and circular marks stay native.
LAYOUT_H = 203
Y_SCALE = H / LAYOUT_H
BLUE, PEACH, GREY, INK, RULE = "#A8CADB", "#EDCAA5", "#D5D9DF", "#111111", "#8E989F"
SOURCES = [
    "paper/results/atlas_materialization/records/study_28-004/reference/normalized.samples.csv",
    "paper/results/atlas_materialization/records/study_28-004/reference/normalized.timestamps.csv",
    "paper/results/atlas_materialization/records/study_28-004/reference/recording.bundle.json",
    "paper/results/atlas_materialization/atlas.materialization.index.json",
    "paper/results/validation/literature_semantic_enrichment.json",
    "src/harmo/ingest/transform.py", "src/harmo/config/validator.py",
    "src/harmo/evidence/semantic_claims.py", "src/harmo/evidence/policy.py",
    "src/harmo/evidence/resolution.py", "tests/test_comparability_resolution.py",
]


def read_json(p):
    return json.loads((ROOT / p).read_text())


def evidence(analysis_python):
    with (ROOT / SOURCES[0]).open(newline="") as stream:
        samples = list(csv.DictReader(stream))
    with (ROOT / SOURCES[1]).open(newline="") as stream:
        times = list(csv.DictReader(stream))
    bundle = read_json(SOURCES[2])
    labels = ["Voltage.LTA", "Voltage.RTA"]
    channels = {c["name"]: c for c in bundle["channels"]}
    assert channels[labels[0]]["channel_id"] == "ch-006"
    assert channels[labels[1]]["channel_id"] == "ch-013"
    assert all(channels[label]["unit"] == "V" for label in labels)
    assert [channels[label]["side"] for label in labels] == ["left", "right"]
    long_rows = [dict(time_seconds=times[i]["time_seconds"], channel_id=channels[label]["channel_id"],
                      source_label=label, value=samples[i][label]) for i in range(2) for label in labels]
    article = read_json(SOURCES[4])
    claim = next(c for c in article["claims"] if c["claim_id"] == "study31-article-emg-bandpass")
    decision = next(c for c in article["decisions"] if c["claim_id"] == claim["claim_id"])
    assert claim["value"] == [20, 450] and claim["scope_level"] == "dataset"
    assert claim["scope_selector"] == {"representation": "processed-emg"}
    assert claim["extraction_method"] == "frozen-regex:presence" and decision["status"] == "accepted"
    mapping = next(m for m in read_json(SOURCES[3])["mappings"] if m["mapping_id"] == "study_28-004")
    assert mapping["comparison"]["status"] == "conditionally_comparable"
    assert mapping["comparison"]["variable"] == "tibialis-anterior.activation-timing"
    assert mapping["comparison"]["left_channel_id"] == "ch-013"
    # Execute the actual implemented interpolator on explicitly synthetic cycles.
    code = '''import json,sys,numpy as np
sys.path.insert(0,"src")
from harmo.evidence.resolution import _process_values
curves=[]
for n in (11,17):
    phase=np.linspace(0,100,n)
    source=(np.exp(-0.5*((phase-35)/12)**2)+0.25*np.exp(-0.5*((phase-78)/8)**2))[:,None]
    derived,operations,scale=_process_values(source,None,{"amplitude_normalization":"none"},101)
    assert scale=="source-scale"
    assert [op.operation for op in operations]==["linear-phase-resample"]
    assert np.array_equal(derived[:,0],np.interp(np.arange(101),phase,source[:,0]))
    assert np.array_equal(derived[[0,-1],0],source[[0,-1],0])
    curves.append(dict(n=n,source=source[:,0].tolist(),derived=derived[:,0].tolist(),operations=[op.to_dict() for op in operations]))
print(json.dumps(curves))'''
    completed = subprocess.run([analysis_python, "-c", code], cwd=ROOT, capture_output=True, text=True, check=True)
    return {"samples": samples[:2], "times": times[:2], "long_rows": long_rows,
            "claim": claim, "claim_decision": decision, "comparison": mapping["comparison"],
            "synthetic_resolution": json.loads(completed.stdout)}


class Drawing:
    def __init__(self, path):
        self.pdf = canvas.Canvas(str(path), pagesize=(W * MM, H * MM), invariant=1)
        self.pdf.setTitle("Implementation of evidence-linked EMG harmonization")
        self.pdf.setAuthor("Yusuke Matsui")
        self.svg = [f'<svg xmlns="http://www.w3.org/2000/svg" width="{W}mm" height="{H}mm" viewBox="0 0 {W} {H}">',
                    f'<rect width="{W}" height="{H}" fill="white"/>']
        self.texts = []

    def text(self, x, y, text, size=8, bold=False, anchor="start", colour=INK):
        y *= Y_SCALE
        face = "Arial-Bold" if bold else "Arial"
        width = pdfmetrics.stringWidth(text, face, size) / MM
        start = x - (width / 2 if anchor == "middle" else width if anchor == "end" else 0)
        assert 0 <= start and start + width <= W, (text, start, width)
        assert 0 < y < H
        self.pdf.setFillColor(HexColor(colour)); self.pdf.setFont(face, size)
        self.pdf.drawString(start * MM, (H - y) * MM, text)
        self.svg.append(f'<text x="{x}" y="{y}" font-family="Arial" font-size="{size / MM}" font-weight="{"bold" if bold else "normal"}" text-anchor="{anchor}" fill="{colour}">{escape(text)}</text>')
        self.texts.append(dict(text=text, x=x, y=y, size_pt=size))

    def line(self, x1, y1, x2, y2, colour=RULE, width=0.22, dashed=False, arrow=False):
        self.pdf.setStrokeColor(HexColor(colour)); self.pdf.setLineWidth(width * MM)
        self.pdf.setDash(2 * MM, 1 * MM) if dashed else self.pdf.setDash()
        self.pdf.line(x1 * MM, (H-y1*Y_SCALE) * MM, x2 * MM, (H-y2*Y_SCALE) * MM)
        self.svg.append(f'<line x1="{x1}" y1="{y1*Y_SCALE}" x2="{x2}" y2="{y2*Y_SCALE}" stroke="{colour}" stroke-width="{width}"' + (' stroke-dasharray="2 1"' if dashed else '') + '/>')
        if arrow:
            a = math.atan2(y2-y1,x2-x1)
            for sign in (-1,1):
                self.line(x2,y2,x2-1.5*math.cos(a+sign*.42),y2-1.5*math.sin(a+sign*.42),colour,width)

    def box(self,x,y,w,h,dashed=False,colour=RULE,width=0.22):
        for points in [(x,y,x+w,y),(x+w,y,x+w,y+h),(x+w,y+h,x,y+h),(x,y+h,x,y)]:
            self.line(*points,dashed=dashed,colour=colour,width=width)

    def rounded_box(self,x,y,w,h,r=1.6,colour=RULE,width=0.25,fill=None):
        y *= Y_SCALE
        h *= Y_SCALE
        self.pdf.setDash(); self.pdf.setStrokeColor(HexColor(colour))
        self.pdf.setLineWidth(width*MM)
        if fill: self.pdf.setFillColor(HexColor(fill))
        self.pdf.roundRect(x*MM,(H-y-h)*MM,w*MM,h*MM,r*MM,stroke=1,fill=int(fill is not None))
        self.svg.append(f'<rect x="{x}" y="{y}" width="{w}" height="{h}" rx="{r}" fill="{fill or "none"}" stroke="{colour}" stroke-width="{width}"/>')

    def circle(self,x,y,r,colour=BLUE):
        y *= Y_SCALE
        self.pdf.setFillColor(HexColor(colour)); self.pdf.setStrokeColor(HexColor(colour))
        self.pdf.circle(x*MM,(H-y)*MM,r*MM,fill=1,stroke=0)
        self.svg.append(f'<circle cx="{x}" cy="{y}" r="{r}" fill="{colour}"/>')

    def table(self,x,y,widths,headers,rows,row_h=4.6,size=7.5):
        self.line(x,y,x+sum(widths),y)
        centers=[x+sum(widths[:i])+w/2 for i,w in enumerate(widths)]
        for cx,label in zip(centers,headers): self.text(cx,y+3.4,label,size,True,"middle")
        self.line(x,y+row_h,x+sum(widths),y+row_h)
        for i,row in enumerate(rows):
            for cx,label in zip(centers,row): self.text(cx,y+row_h*(i+1)+3.4,str(label),size,False,"middle")
        self.line(x,y+row_h*(len(rows)+1),x+sum(widths),y+row_h*(len(rows)+1))
        for xx in (x,x+sum(widths)):
            self.line(xx,y,xx,y+row_h*(len(rows)+1))

    def panel(self,x,y,letter,title):
        self.rounded_box(x+.8,y+.8,92.4,99.4)
        self.rounded_box(x+2,y+2,6.5,6.5,r=1,colour=BLUE,fill=BLUE)
        self.text(x+3,y+7,letter,13,True)
        self.text(x+11,y+7,title,10,True)
        self.line(x+3,y+10.5,x+91,y+10.5,colour=BLUE,width=.4)

    def finish(self,svg_path):
        self.pdf.showPage(); self.pdf.save()
        svg_path.write_text("\n".join(self.svg+["</svg>"])+"\n")


def draw(d,e):
    d.panel(0,0,"A","Standardize without changing values")
    d.box(5,13,28,12); d.text(19,18,"Public file",8,True,"middle")
    d.text(19,22,"C3D / MAT / CSV",7.5,False,"middle")
    d.box(40,13,51,12,True); d.text(65.5,18,"Author-specified setup",8,True,"middle")
    d.text(65.5,22,"File pattern + channel definitions",7.5,False,"middle")
    d.line(19,25,19,29); d.line(65,25,65,29); d.line(19,29,65,29)
    d.line(43,29,43,32,arrow=True)
    d.box(5,32,86,11,colour=BLUE,width=.35)
    d.text(48,36,"Validate inputs, then select a reader",8.5,True,"middle")
    d.text(48,41,"Invalid input: stop and report the problem",7.5,False,"middle")
    d.rounded_box(3,44.5,88,41.5,r=1,colour=GREY)
    d.text(5,48,"Read array: two channels",8,True)
    d.text(51,48,"Common rows",8,True)
    source_rows=[[f'{float(e["times"][i]["time_seconds"]):.3f}',f'{float(e["samples"][i]["Voltage.LTA"]):.5f}',f'{float(e["samples"][i]["Voltage.RTA"]):.5f}'] for i in range(2)]
    d.table(5,51,[8,15,15],["t (s)","LTA (V)","RTA (V)"],source_rows)
    rows=[[f'{float(r["time_seconds"]):.3f}',r["channel_id"],f'{float(r["value"]):.5f}'] for r in e["long_rows"]]
    d.table(51,51,[8,15,15],["t (s)","Channel","Value (V)"],rows)
    d.line(44,58,49,58,arrow=True)
    d.text(5,69,"Reshape + link labels",7.5)
    d.text(5,73,"No amplitude scaling",7.5)
    d.text(5,79,"LTA / ch-006: left tibialis anterior",7.5)
    d.text(5,84,"RTA / ch-013: right tibialis anterior",7.5)
    d.line(48,86,48,88,arrow=True)
    d.box(3,88,88,8,colour=BLUE,width=.35)
    d.text(5,93,"Output: values + definitions + source digest",8,True)

    d.panel(96,0,"B","Attach meaning with explicit evidence")
    d.box(101,13,85,12,True)
    d.text(143.5,18,"Author-specified extraction rule",8,True,"middle")
    d.text(143.5,22,"Property + section + pattern + scope",7.5,False,"middle")
    d.box(101,30,39,13); d.text(120.5,35,"Article: Study 31",8,True,"middle")
    d.text(120.5,40,"Band-pass: 20-450 Hz",7.5,False,"middle")
    d.box(147,30,39,13); d.text(166.5,35,"Repository definition",7.5,True,"middle")
    d.text(166.5,40,"No overlapping value",7.5,False,"middle")
    d.line(143.5,25,143.5,47.5,arrow=True)
    d.line(120.5,43,120.5,46); d.line(166.5,43,166.5,46)
    d.line(120.5,46,166.5,46)
    d.box(101,47.5,85,6,colour=BLUE,width=.35)
    d.text(143.5,51,"Match rule; compare values at overlapping scope",7.5,True,"middle")
    d.line(143.5,53.5,143.5,58,arrow=True)
    d.box(101,58,85,28,colour=BLUE,width=.35)
    d.text(103,62,"Evidence-linked property",8.5,True)
    d.line(101,64.5,186,64.5)
    for y,key,value in [(68,"Value","Band-pass = [20, 450] Hz"),(73,"Scope","Dataset / processed EMG"),(78,"Source","Article section + digest"),(83,"Status","Accepted at declared scope")]:
        d.text(103,y,key,7.5,True); d.text(122,y,value,7.5)
    d.text(101,92,"No match: unresolved. Conflicting values: retain both.",7.5)
    d.text(101,97,"No promotion to an unobserved trial-level measurement.",7.5)

    d.panel(0,101,"C","Evaluate the declared use")
    d.box(5,115,86,13)
    d.text(48,120,"Two records + channels + intended use",8,True,"middle")
    d.text(48,125,"Example: right tibialis-anterior activation timing",7.5,False,"middle")
    d.line(48,128,48,132,arrow=True)
    d.box(3,132,88,13,colour=BLUE,width=.35)
    d.text(48,137,"Check evidence, anatomy/side, task, processing and time",7.5,False,"middle")
    d.text(5,143,"Collect failures F, unknowns U, and conditions C",7.5,True)
    d.table(5,147,[39,47],["First applicable rule","Comparison state"],
            [["F is not empty","Not comparable"],["else U is not empty","Unassessed"],["else C is not empty","Conditionally comparable"],["else","Comparable"]],5.1,7.5)
    d.line(5,162.3,5,167.4,BLUE,1.1)
    d.line(48,172.5,48,177,arrow=True)
    d.box(3,177,88,11,colour=BLUE,width=.35)
    d.text(48,181,"Apply release policy + required evidence",8,True,"middle")
    d.text(48,186,"Deny / hold / conditional release / release",7.5,False,"middle")
    d.line(48,188,48,191,arrow=True)
    d.box(3,191,88,7,colour=BLUE,width=.35)
    d.text(5,195,"Output: state + reasons + conditions + input digests",7.5,True)

    d.panel(96,101,"D","Resolve conditions, then reassess")
    d.box(101,112,85,7)
    d.text(103,117,"Input: bounded cycles + validated event references",7.5,True)
    d.box(101,121,85,10,True)
    d.text(143.5,125,"Declared processing; interpolation illustrated below",7.5,False,"middle")
    d.text(143.5,129,"Optional: centering, filtering, envelope, normalization",7.5,False,"middle")
    d.rounded_box(100,132,86,38,r=1,colour=GREY)
    d.circle(104,134,.6,BLUE); d.text(106,135,"11",7.5)
    d.circle(117,134,.6,RULE); d.text(119,135,"17 samples",7.5)
    d.text(184,135,"Illustrative cycles",7.5,False,"end")
    # Synthetic, already bounded arrays; actual resolver interpolation output.
    for k,c in enumerate(e["synthetic_resolution"]):
        colour = BLUE if k==0 else "#8E989F"
        for values,x0,xw,denom in [(c["source"],102,33,16),(c["derived"],151,33,100)]:
            coords=[(x0+i/denom*xw,150-v*14) for i,v in enumerate(values)]
            for a,b in zip(coords,coords[1:]): d.line(*a,*b,colour,0.4)
            if x0==102:
                for x,y in coords: d.circle(x,y,.4,colour)
    d.line(102,151,135,151); d.line(151,151,184,151)
    d.text(118,155,"11 / 17 samples",7.5,False,"middle")
    d.text(167.5,155,"101 phase points",7.5,False,"middle")
    d.line(138,143,148,143,arrow=True)
    d.text(143,140,"linear",7.5,False,"middle")
    d.text(103,163,"p(i) = 100 i / (N - 1); q(j) = j, j = 0,...,100",7.5)
    d.text(103,168,"y*(q) = linear interpolation of (p, y); no extrapolation",7.5)
    d.line(143.5,170,143.5,172,arrow=True)
    d.box(101,172,85,12,colour=BLUE,width=.35)
    d.text(143.5,177,"Derived record + operation parameters",8,True,"middle")
    d.text(143.5,181,"Parent digest + software version; source unchanged",7.5,False,"middle")
    d.line(143.5,184,143.5,188,arrow=True)
    d.box(101,188,85,7,colour=BLUE,width=.35)
    d.text(143.5,192,"Reassess comparison AND release policy",8,True,"middle")
    d.text(101,198,"No event detection; no amplitude-pooling claim.",7.5)


def main():
    parser=argparse.ArgumentParser()
    parser.add_argument("--analysis-python",default=sys.executable)
    parser.add_argument("--output",type=Path)
    args=parser.parse_args()
    out=args.output or ROOT / "paper/figures/candidates" / STEM
    out.mkdir(parents=True,exist_ok=True)
    pdfmetrics.registerFont(TTFont("Arial","/System/Library/Fonts/Supplemental/Arial.ttf"))
    pdfmetrics.registerFont(TTFont("Arial-Bold","/System/Library/Fonts/Supplemental/Arial Bold.ttf"))
    e=evidence(args.analysis_python)
    d=Drawing(out/f"{STEM}.pdf"); draw(d,e); d.finish(out/f"{STEM}.svg")
    subprocess.run(["pdftoppm","-singlefile","-r","600","-png",str(out/f"{STEM}.pdf"),str(out/STEM)],check=True)
    (out/f"{STEM}_examples.json").write_text(json.dumps(e,indent=2)+"\n")
    paths=SOURCES+["scripts/paper/generate_implementation_figure.py","paper/figures/candidates/IMPLEMENTATION_FIGURE_VERSION",
                  f"paper/figures/candidates/{STEM}/{STEM}_caption.md"]
    record=dict(status="candidate_not_adopted",version=VERSION,width_mm=W,height_mm=H,
                minimum_text_pt=min(t["size_pt"] for t in d.texts),text_count=len(d.texts),
                examples={"A":"stored Study 28 sample subset, reshaped for display; values rounded only for labeling",
                          "B":"stored Study 31 band-pass evidence claim", "C":"implemented precedence and stored conditional timing case",
                          "D":"synthetic bounded arrays passed through the implemented interpolation function"},
                sources=[dict(path=p,sha256=hashlib.sha256((ROOT/p).read_bytes()).hexdigest()) for p in paths])
    (out/f"{STEM}_evidence.json").write_text(json.dumps(record,indent=2)+"\n")
    print(json.dumps(dict(output=str(out),text_count=len(d.texts))))


if __name__=="__main__": main()
