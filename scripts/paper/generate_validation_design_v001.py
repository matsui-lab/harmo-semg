#!/usr/bin/env python3
"""Draw a bounded validation-design candidate using registered data and rules."""
import hashlib
import json
import subprocess
from pathlib import Path
from reportlab.pdfbase import pdfmetrics
from reportlab.pdfbase.ttfonts import TTFont
import yaml
import generate_implementation_figure as b

ROOT = b.ROOT
STEM = 'validation_design_v001_2026.09.12-r1'
OUT = ROOT / 'paper/figures/candidates' / STEM
b.W, b.H, b.Y_SCALE = 280, 210, 1
BLUE, PEACH = b.BLUE, b.PEACH


def text(d, x, y, s, size=9, bold=False):
    d.text(x, y, s, size, bold)


def arrow(d, x, y, z):
    d.line(x, y, z, y, width=.3, arrow=True)


def tag(d, x, y, w, label, colour=None):
    d.box(x, y, w, 7, width=.22)
    if colour:
        d.line(x, y, x+w, y, colour=colour, width=.7)
    d.text(x+w/2, y+4.8, label, 8.5, False, 'middle')


def page(d, x, y, label):
    d.box(x, y, 15, 9)
    text(d, x+2, y+4, label, 8, True)
    d.line(x+2, y+6, x+12, y+6, width=.18)


def person(d, x, y, colour):
    d.circle(x, y, 1.3, colour)
    for a in [(x,y+2,x,y+6),(x-2,y+3,x+2,y+3),
              (x,y+6,x-2,y+9),(x,y+6,x+2,y+9)]:
        d.line(*a, colour=b.RULE, width=.35)


def lane(d, y, h, title, endpoint, arrows=True):
    d.box(5, y, 270, h, colour='#BFC5C9', width=.2)
    d.line(5, y, 5, y+h, colour=BLUE, width=.85)
    text(d, 8, y+5.5, title, 10, True)
    d.text(271, y+5.5, endpoint, 8, False, 'end')
    if arrows:
        arrow(d, 72, y+h/2+2, 80)
        arrow(d, 210, y+h/2+2, 218)


def draw(d, protocol):
    text(d, 5, 8, 'Testing what harmonization enables - and where reuse must stop', 12, True)
    text(d, 5, 15, 'REGISTERED PUBLIC CORPUS', 9, True)
    text(d, 92, 15, '8 studies: 7 with EMG + 1 biomechanics-only format control', 9)
    names = [
        ('Controlled-speed walking (31)', 'HD-EMG (32)'),
        ('Ramp walking (1)*', 'Multi-condition locomotion (33)'),
        ('Gait120 (120)', 'Walking / stairs (38)'),
        ('Stroke / lifespan (28)', 'Sit-to-walk (43)'),
    ]
    for i, pair in enumerate(names):
        x = 5+i*68
        d.box(x, 18, 66, 6.5, colour='#BFC5C9', width=.2)
        d.box(x, 25.5, 66, 6.5, colour='#BFC5C9', width=.2)
        text(d, x+2, 23, pair[0], 8.8)
        text(d, x+2, 30.5, pair[1], 8.8)
    text(d, 8, 39, 'DATA USED', 9, True)
    text(d, 83, 39, 'TEST / COMPARISON', 9, True)
    text(d, 221, 39, 'EVALUATED PROPERTY', 9, True)

    lane(d, 43, 32, 'Conversion and extension', 'A / D / F', False)
    for y in [56,68.5]:
        arrow(d,72,y,80);arrow(d,210,y,218)
    text(d, 8, 55, '8 studies | 12 assets', 9, True)
    text(d, 8, 61, '78 input-to-conversion settings', 8.8)
    text(d, 8, 68, 'Held-out: ULTRA-MoCap HDF5', 8.7, True)
    text(d, 8, 72.5, '13 participants x 16 trials', 8.7)
    page(d, 84, 52, 'Input')
    arrow(d, 100, 56, 108)
    tag(d, 110, 50, 27, 'Convert x 2')
    text(d, 110, 61.5, 'Compare outputs', 8)
    for j, label in enumerate(['macOS', 'Ubuntu']):
        text(d, 147, 53+j*6, label, 8)
        for k in range(3):
            d.circle(174+k*5, 52+j*6, .85, BLUE)
    text(d, 190, 56, '3 runs', 8)
    tag(d, 84, 65, 36, 'HDF5 reader', PEACH)
    arrow(d, 121, 68.5, 129)
    tag(d, 131, 65, 47, 'Shared converter')
    text(d, 181, 69.5, 'x 2', 8.5)
    text(d, 221, 55, 'Output agreement', 9, True)
    text(d, 221, 61, 'Within / across environments', 8)
    text(d, 221, 68, 'Core edits + missing-cell retention', 8)

    lane(d, 79, 46, 'Evidence and comparability', 'B / C + evidence tests', False)
    for y in [90.5,100.5,110.5,119.5]:
        arrow(d,72,y,80);arrow(d,210,y,218)
    text(d, 8, 92, 'Corpus files + source articles', 8.8, True)
    text(d, 8, 101, 'Study 31 | 16 participants', 8.8, True)
    text(d, 8, 110, 'Studies 28 / 33 | 6 mappings', 8.8, True)
    text(d, 8, 120, '8 specified faults + clean control', 8.5)
    tag(d, 84, 87, 28, 'Article text')
    tag(d, 145, 87, 30, 'File metadata')
    arrow(d, 114, 90.5, 141)
    arrow(d, 141, 90.5, 114)
    text(d, 180, 92, 'Match scope', 8)
    for x, label in [(84,'ASCII'),(113,'MAT'),(142,'C3D')]:
        tag(d, x, 97, 23, label)
    d.line(95.5, 96, 153.5, 96, width=.2)
    text(d, 172, 101.5, 'Same record', 8)
    tag(d, 84, 107, 43, '2 records / mapping')
    arrow(d, 128, 110.5, 137)
    tag(d, 139, 107, 64, 'Declared timing use + rules')
    text(d, 84, 121, 'Unit / side / muscle / rate / time / frame / channel / source', 8)
    text(d, 221, 92, 'Coverage + conflicts', 9, True)
    text(d, 221, 101, 'Numerical equivalence', 9, True)
    text(d, 221, 110, 'Purpose-specific status', 9, True)
    text(d, 221, 120, 'Sensitivity + specificity', 9, True)

    lane(d, 129, 22, 'Complete-record reuse', 'H')
    text(d, 8, 141, 'Study 38 | 10 participants', 9, True)
    text(d, 8, 147, 'All eligible same-side Walking records', 8)
    # A complete sample-by-channel array, not a synthetic data plot.
    d.box(84, 137, 15, 10)
    for i in range(1,8): d.line(84+i*15/8,137,84+i*15/8,147,width=.12)
    for i in range(1,5): d.line(84,137+i*2,99,137+i*2,width=.12)
    text(d, 102, 141, 'All samples', 8)
    text(d, 102, 146, '8 channels', 8)
    arrow(d, 125, 142, 134)
    tag(d, 136, 138, 36, 'Materialize x 2')
    text(d, 177, 141, '4 queries', 8.5)
    text(d, 177, 146, '25 timed runs', 8)
    text(d, 221, 141, 'Retention + repeatability', 9, True)
    text(d, 221, 147, 'Local query latency', 8.5)

    lane(d, 155, 26, 'Physiological preservation', 'G')
    person(d, 10, 167, BLUE);person(d, 39, 167, PEACH)
    text(d, 15, 168, 'Study 38', 8.5, True)
    text(d, 15, 174, 'n = 10', 8.5)
    text(d, 44, 168, 'Gait120', 8.5, True)
    text(d, 44, 174, 'n = 10', 8.5)
    text(d, 84, 164, 'Prespecified activity windows (% gait cycle)', 8.5)
    for y, short, muscle in [(169,'TA','tibialis anterior'),(175,'MG','medial gastrocnemius')]:
        text(d, 84, y+1, short, 8, True)
        d.line(94,y,139,y,colour=b.GREY,width=.8)
        for low,high in protocol['muscles'][muscle]['expected_peak_windows_percent']:
            d.line(94+low*.45,y,94+high*.45,y,colour=BLUE,width=1.25)
    for value in [0,20,60,100]:
        d.text(94+value*.45,179,str(value),7,False,'middle')
    arrow(d, 143, 171, 152)
    text(d, 155, 169, 'Participant summaries', 8.5)
    text(d, 155, 175, 'Compare study centroids', 8.5)
    text(d, 221, 167, 'Pattern: >=70% of participants', 8.5, True)
    text(d, 221, 173, 'Centroid gap: <=15% of cycle', 8.5, True)
    text(d, 221, 178, 'No cross-study amplitude pooling', 8)

    lane(d, 185, 19, 'Boundary of supported reuse', 'E')
    text(d, 8, 197, 'Study 31 | 16 x 7 speeds x 3 trials', 8.5, True)
    text(d, 8, 202, '336 registered trial identities', 8.5)
    tag(d, 84, 191, 35, 'Dynamic trial')
    text(d, 122, 196, '+', 11, True)
    tag(d, 129, 191, 45, 'Calibration evidence')
    arrow(d, 175, 194.5, 181)
    text(d, 183, 194, 'Present: test', 8)
    text(d, 183, 200, 'Absent: stop', 8)
    text(d, 221, 197, 'Calibration prerequisite', 9, True)
    text(d, 221, 202, 'Mechanics release decision', 8.5)
    text(d, 5, 209, 'Rows are separate tests, not a sequential cohort. Counts denote design, not results. * Biomechanics-only control.', 8)


def main():
    OUT.mkdir(parents=True, exist_ok=True)
    for name, file in [('Arial','Arial.ttf'),('Arial-Bold','Arial Bold.ttf')]:
        pdfmetrics.registerFont(TTFont(name, '/System/Library/Fonts/Supplemental/'+file))
    protocol = yaml.safe_load((ROOT/'paper/physiology_positive_control_protocol.yaml').read_text())
    assert len(protocol['required_studies']['study_38']['subjects']) == 10
    assert len(protocol['required_studies']['study_120']['subjects']) == 10
    assert protocol['decision_rules']['minimum_subject_pattern_fraction_per_muscle'] == .70
    assert protocol['decision_rules']['maximum_cross_study_centroid_difference_percent'] == 15
    d = b.Drawing(OUT / f'{STEM}.pdf')
    d.pdf.setTitle('Public data and validation design for purpose-specific EMG reuse')
    draw(d, protocol)
    d.finish(OUT / f'{STEM}.svg')
    subprocess.run(['pdftoppm','-singlefile','-r','300','-png',str(OUT/f'{STEM}.pdf'),str(OUT/STEM)],check=True)
    sources = ['paper/paper.md','paper/tables/table1_public_studies.md',
               'paper/tables/table2_validation_endpoints.md',
               'paper/physiology_positive_control_protocol.yaml',
               'paper/full_record_materialization_protocol.yaml',
               'paper/g7_ultra_mocap_protocol.yaml',
               'scripts/paper/generate_implementation_figure.py',
               'scripts/paper/generate_validation_design_v001.py']
    record = {'version':'v001_2026.09.12-r1','status':'candidate_not_adopted',
              'width_mm':280,'height_mm':210,
              'scope':'Design schematic only; no newly measured outcomes or validation.',
              'sources':[{'path':s,'sha256':hashlib.sha256((ROOT/s).read_bytes()).hexdigest()} for s in sources],
              'text_items':d.texts}
    (OUT/f'{STEM}_evidence.json').write_text(json.dumps(record,indent=2)+'\n')
    print(OUT/f'{STEM}.png')


if __name__ == '__main__':
    main()
