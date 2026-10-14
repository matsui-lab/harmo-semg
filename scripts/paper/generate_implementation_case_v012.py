#!/usr/bin/env python3
"""Figure 2 candidate: preserve measured curves; expose the saved reassessment."""
import hashlib
import json
import subprocess
from pathlib import Path
from reportlab.pdfbase import pdfmetrics
from reportlab.pdfbase.ttfonts import TTFont
from pypdf import PdfReader, PdfWriter, Transformation
import generate_implementation_case_v011 as base

b, ROOT, PREV = base.b, base.ROOT, base.PREV
t, route, box, logo, code = base.t, base.route, base.box, base.logo, base.code
VERSION = "v012_2026.09.16-r1"
STEM = f"implementation_reassessment_{VERSION}"
OUT = ROOT / "paper/figures/candidates" / STEM
OLD = ROOT / "paper/figures/candidates/implementation_content_insets_v011_2026.09.12-r8"
EMPH = "#2877A4"
CAPTION = """## Figure 2. Registered EMG reuse: processing and evidence-based reassessment

The worked example compares right tibialis anterior activation timing for Study
38 participant 1 and Gait120 S011. It joins saved components of the registered
two-study pathway, not an unconfigured new-study or GUI demonstration.
Dashed boxes identify analyst-defined settings; solid arrows show data flow
or the order of reassessment. The dashed arrow attaches documentary context
to its applicable recording group, not directly to release permission.

Figshare provides Study 38 as MATLAB and Gait120 as a MATLAB member of a ZIP
release. Original field names and selected file contents are shown. RX is the
Study 38 native right-side code. The registered protocol selects the first five
of 15 source-ordered Walking cycles whose reference-foot side matches the EMG
side; the Gait120 example contains ten accepted LevelWalking cycles of 101
points each. Study 38 processing uses mean centering, fourth-order zero-phase
20-350 Hz Butterworth filtering, rectification, 125-ms Hampel filtering at
three robust standard deviations, and 125-ms RMS smoothing. The Gait120 path
retains the released envelope. Both paths use cycle-maximum scaling, linear
interpolation to 101 gait-phase points, rescaling after interpolation, and
pointwise participant medians. Grey traces show individual cycles and coloured
traces show the median. Source amplitudes are not comparable; derived scales
are dimensionless and do not establish cross-study amplitude normalization.

The Europe PMC JATS XML excerpt is abridged. A fixed presence rule in the
Experimental preparation section assigns right to the declared Gait120
surface-EMG channel group. The project-local YAML side definition corroborates
the value at overlapping scope; it is an analyst-maintained definition, not
independent repository evidence. The scope includes the displayed S011 EMG
channel. The contextual arrow summarizes that applicability, not a new rerun
or an automatic trigger of the timing resolver.

Both numerical inputs already share the phase grid at the initial comparison.
Its remaining condition is a validated processing compatibility group.
The resolver links the registered protocol and previously passing physiological
control, records the supported group, and repeats comparison and release checks.
The saved pair changes from conditionally comparable to comparable, with timing
release allowed for both records; amplitude comparability remains unestablished.
This transition adds processing support, not another waveform alignment step.
The saved-output box summarizes curve files, recording descriptors, input
identities and transformation information, and the pair decision; it does not
depict the newer GUI collection. No human review is asserted for this example.
The curves and outcomes are retained computational evidence, not new independent
validation or proof of biological equivalence. The pair was not selected for
waveform similarity. Cohort-level physiological results are shown in Figure 6.
Official service logos identify sources, not endorsement; logo attribution is
retained with the figure assets.
"""


def draw(d, case):
    t(d, 5, 8, "Registered EMG reuse: processing and evidence-based reassessment", 12, True)
    box(d, 5, 14, 270, 13, dashed=True)
    t(d, 8, 19.5, "Analyst-defined use: right tibialis anterior activation timing", 10, True)
    t(d, 8, 24.5, "Study 38 participant 1 + Gait120 S011", 8.5)
    t(d, 211, 24.5, "Configured in Python / JSON", 8)

    t(d, 5, 37, "Released signals", 9.5, True)
    box(d, 72, 31, 137, 11, dashed=True)
    t(d, 75, 36, "Analyst-defined protocol | YAML", 9, True)
    t(d, 75, 40, "Cycle selection, filters, scaling and aggregation", 8)
    t(d, 224, 37, "Saved reassessment", 9.5, True)

    for y, label, sub, col in [
        (46, "MAT", "Study 38 / Subject1.mat", b.BLUE),
        (99, "ZIP > MAT", "S011/EMG/ProcessedData.mat", b.PEACH),
    ]:
        box(d, 5, y, 57, 44, col)
        logo(d, base.LOGOS / "figshare-logo.png", 8, y + 2, 5)
        t(d, 15, y + 6, "Figshare | " + label, 9, True)
        t(d, 8, y + 11, sub, 8)
    box(d, 72, 46, 65, 44)
    t(d, 75, 51, "Read fields; select walking cycles", 8.5, True)
    d.table(75, 54, [24, 35], ["Field", "Value"], [
        ["EmgVarName", "Tibialis Anterior"],
        ["EMGSide", "RX"],
        ["EMGFreq", "1000 Hz"],
    ], row_h=4, size=7.5)
    for i in range(15):
        d.line(77+i*2.1, 72.5, 77+i*2.1, 76, colour=b.BLUE if i < 5 else b.GREY, width=.65)
    t(d, 111, 75, "First 5 of 15", 7.5)
    t(d, 75, 81, "Filter / envelope / scale", 8.5)
    t(d, 75, 87, "101-point phase / median", 8.5)

    box(d, 72, 99, 65, 44)
    t(d, 75, 105, "Decode named EMG column", 9, True)
    code(d, 75, 111, ["column: TibialisAnterior"], 7.5)
    t(d, 75, 118, "10 LevelWalking cycles", 8.5)
    t(d, 75, 124, "Source already has 101 points", 8.5)
    d.line(75, 127, 134, 127)
    t(d, 75, 133, "Retain released envelope", 8.5)
    t(d, 75, 139, "Cycle scaling / median", 8.5)
    for y, col, name in [(46, b.BLUE, "Study 38"), (99, b.PEACH, "Gait120")]:
        box(d, 149, y, 60, 44, col)
        t(d, 152, y+6, name+" | derived curve", 9, True)
        route(d, [(62.5,y+22),(71,y+22)])
        route(d, [(137.5,y+22),(148,y+22)])
    # Both curve inputs enter the initial comparison, before support is linked.
    route(d, [(209.5,68),(215,68),(223,68)])
    route(d, [(209.5,121),(215,121),(215,68)], arrow=False)
    box(d, 224, 46, 51, 28)
    t(d, 227, 52, "Before: conditional", 9, True)
    t(d, 227, 59, "Common phase: present", 8)
    t(d, 227, 66, "Processing support: missing", 8)
    t(d, 227, 71, "Curves already aligned", 7.5)

    route(d, [(249.5,74),(249.5,81)])
    box(d, 224, 82, 51, 37)
    t(d, 227, 88, "Add processing support", 8.5, True)
    t(d, 227, 95, "Protocol YAML + control JSON", 8)
    t(d, 227, 101, "Physiological control: passed", 8)
    t(d, 227, 107, "Record compatibility group", 8)
    d.line(227, 110, 272, 110)
    t(d, 227, 116, "Repeat comparison + release", 8, True)
    route(d, [(249.5,119),(249.5,126)])
    box(d, 224, 127, 51, 28)
    d.text(227, 133, "After: timing allowed", 9, True, colour=EMPH)
    t(d, 227, 140, "Comparison: comparable", 8)
    t(d, 227, 147, "Amplitude: not established", 8)
    t(d, 227, 152, "No new resampling step", 7.5)

    # Documentary values are applied at their declared scope, not used as a
    # newly invented direct input to the resolver.
    box(d, 5, 152, 57, 29)
    logo(d, base.LOGOS / "europe-pmc-basic-rgb.png", 8, 155, 6)
    t(d, 17, 158, "Europe PMC | JATS XML", 8.5, True)
    t(d, 17, 163, "PMC12177048", 8)
    code(d, 8, 170, ["<p>... different muscles", "of the right leg ...</p>"], 7.5, 4)
    box(d, 5, 186, 57, 15, dashed=True)
    t(d, 8, 191, "Project definition | YAML", 8.5, True)
    code(d, 8, 197, ["side: Right"], 8)
    box(d, 72, 152, 65, 49)
    t(d, 75, 158, "Apply a declared extraction rule", 8.5, True)
    t(d, 75, 164, "Section: Experimental preparation", 8)
    t(d, 75, 170, "Fixed phrase match assigns right", 8)
    t(d, 75, 177, "Compare YAML value + scope", 8.5)
    d.line(75, 182, 134, 182)
    t(d, 75, 188, "Agreement: corroborated", 8.5, True)
    t(d, 75, 196, "Retain source, section and hash", 8)
    route(d, [(62.5,167),(71,167)])
    route(d, [(62.5,194),(71,194)])
    box(d, 149, 159, 60, 42)
    t(d, 152, 165, "Scoped context | JSON", 9, True)
    code(d, 152, 172, ['"value": ["right"]', '"scope_level": "channel-group"'], 7.1, 4)
    t(d, 152, 183, "Target: Gait120 surface EMG", 8)
    t(d, 152, 190, "Includes S011 / tibialis anterior", 8)
    t(d, 152, 197, "Context, not release permission", 7.5)
    route(d, [(137.5,182),(148,182)])
    route(d, [(179,159),(179,144)], dashed=True)
    t(d, 183, 151, "applies to", 7.5)

    route(d, [(249.5,155),(249.5,163)])
    box(d, 224, 164, 51, 37)
    t(d, 227, 170, "Retained outputs", 9, True)
    t(d, 227, 177, "Curves: CSV", 8.5)
    t(d, 227, 183, "Records + decisions: JSON", 8)
    t(d, 227, 190, "Input identities + processing", 8)
    t(d, 5, 208, "Actual waveforms; grey = cycles, colour = median. Dashed boxes: analyst settings. Dashed arrow: applicable context.", 7.5)


def main():
    OUT.mkdir(parents=True, exist_ok=True)
    for name, file in [("Arial","Arial.ttf"), ("Arial-Bold","Arial Bold.ttf")]:
        pdfmetrics.registerFont(TTFont(name, "/System/Library/Fonts/Supplemental/"+file))
    # Verify historical input artifacts, not current implementation code, against
    # the approved figure's evidence index.
    historical = json.loads((OLD / (OLD.name+"_evidence.json")).read_text())
    source_rows = [s for s in historical["sources"] if not s["path"].startswith(("src/","scripts/"))]
    for item in source_rows:
        assert hashlib.sha256((ROOT/item["path"]).read_bytes()).hexdigest() == item["sha256"], item["path"]
    case = json.loads((PREV/"extracted_case.json").read_text())
    pair = json.loads((ROOT/"paper/results/validation/comparability_resolution_study.json").read_text())["comparisons"][0]
    assert pair == case["saved_decision"]
    assert pair["source_status"] == "conditionally_comparable"
    assert pair["conditions_before"] == ["establish a validated processing compatibility group for activation timing"]
    assert pair["temporal_grid_and_landmarks_equal"]
    assert pair["conditions_after"] == []
    assert pair["resolved_status"] == "comparable"
    assert pair["left_release_status"] == pair["right_release_status"] == "allow"
    assert pair["amplitude_comparability"] == "not-established"
    claim = case["literature"]["claim"]
    assert claim["scope_selector"] == {"sensor_type":"surface-emg"}
    assert claim["scope_level"] == "channel-group" and claim["value"] == ["right"]
    phrase = case["literature"]["phrase"]
    digest = hashlib.sha256(json.dumps([phrase], ensure_ascii=False, sort_keys=True, separators=(",",":")).encode()).hexdigest()
    assert digest == claim["evidence_sha256"]
    d = b.Drawing(OUT/f"{STEM}_layout.pdf")
    draw(d, case)
    d.finish(OUT/f"{STEM}_layout.svg")
    page = PdfReader(OUT/f"{STEM}_layout.pdf").pages[0]
    placements = [("source38",6,61),("source120",6,114),("median38",151,60),("median120",151,113)]
    for name,x,y in placements:
        chart = PdfReader(PREV/f"{name}.pdf").pages[0]
        # R's base PDF device rounds the requested millimetres to whole points.
        assert abs(float(chart.mediabox.height)/b.MM-28) < .4
        page.merge_transformed_page(chart, Transformation().translate(x*b.MM,(b.H-y-28)*b.MM))
    writer = PdfWriter()
    writer.add_page(page)
    writer.add_metadata({"/Title":"Registered EMG reuse: processing and evidence-based reassessment","/Author":"Yusuke Matsui"})
    with (OUT/f"{STEM}.pdf").open("wb") as stream:
        writer.write(stream)
    subprocess.run(["pdftoppm","-singlefile","-r","300","-png",str(OUT/f"{STEM}.pdf"),str(OUT/STEM)], check=True)
    subprocess.run(["pdftocairo","-svg",str(OUT/f"{STEM}.pdf"),str(OUT/f"{STEM}.svg")], check=True)
    (OUT/f"{STEM}_caption.md").write_text(CAPTION)
    source_rows += [dict(path=str(Path(__file__).relative_to(ROOT)), sha256=hashlib.sha256(Path(__file__).read_bytes()).hexdigest())]
    (OUT/f"{STEM}_evidence.json").write_text(json.dumps({
        "version":VERSION,"status":"candidate_not_adopted","width_mm":b.W,"height_mm":b.H,
        "scope":"Historical registered two-study example; no new data, scientific approval, or GUI execution.",
        "waveforms":"Four original ggplot2 vector PDFs reused unchanged.",
        "verification":{"historical_inputs_match":True,"initial_grid_already_equal":True,"saved_reassessment_match":True,"matched_phrase_digest":digest},
        "sources":source_rows,
        "exports":[{"path":str(p.relative_to(ROOT)),"sha256":hashlib.sha256(p.read_bytes()).hexdigest()} for p in [
            OUT/f"{STEM}.pdf",OUT/f"{STEM}.svg",OUT/f"{STEM}.png",OUT/f"{STEM}_caption.md"]],
    },indent=2)+"\n")
    print(OUT/f"{STEM}.png")


if __name__ == "__main__":
    main()
