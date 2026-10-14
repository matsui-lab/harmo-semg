import json
from pathlib import Path

from PIL import Image, ImageChops

ROOT=Path(__file__).resolve().parents[1]
OLD='figure2_implementation_gui_v016_2026.09.16-r5'
NEW='figure2_implementation_gui_v017_2026.09.16-r6'
PX=96/25.4


def test_only_storage_image_replaced():
    old=json.loads((ROOT/'tmp'/OLD/'scene.json').read_text())
    new=json.loads((ROOT/'tmp'/NEW/'scene.json').read_text())
    target=lambda o:o['kind']=='image' and abs(o['x']/PX-235)<1e-6 and abs(o['y']/PX-148)<1e-6
    icon=lambda o:o.get('name','').startswith('Linked-output icon:')
    assert sum(target(o) for o in old['objects'])==1
    assert [o for o in old['objects'] if not target(o)]==[o for o in new['objects'] if not icon(o)]
    assert all(o['kind']=='path' and o['colour']=='#111111' for o in new['objects'] if icon(o))


def test_pdf_pixels_outside_icon_identical():
    a=Image.open(ROOT/'paper/figures/candidates'/OLD/(OLD+'.png')).convert('RGB')
    b=Image.open(ROOT/'paper/figures/candidates'/NEW/(NEW+'.png')).convert('RGB')
    assert a.size==b.size
    x0,y0,x1,y1=ImageChops.difference(a,b).getbbox()
    assert x0>=234/280*a.width and x1<=275/280*a.width
    assert y0>=147/357*a.height and y1<=183/357*a.height
