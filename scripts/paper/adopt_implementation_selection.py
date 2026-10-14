#!/usr/bin/env python3
"""Select the approved implementation artwork as manuscript Figure 2."""
import hashlib
import json
import shutil
import subprocess
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
VERSION = 'v016_2026.09.16-r5'
STEM = f'figure2_implementation_gui_{VERSION}'


def identity(path):
    data = path.read_bytes()
    return dict(path=str(path.relative_to(ROOT)), bytes=len(data), sha256=hashlib.sha256(data).hexdigest())


def main():
    figures = ROOT / 'paper/figures'
    candidate = figures / 'candidates' / STEM
    pdf = candidate / f'{STEM}.pdf'
    if identity(pdf)['sha256'] != '95de29389d73e8a250af40b2e44fea277b03d2da787ae1763cf440b97039ddd5':
        raise ValueError('Approved implementation artwork changed; review before adoption')
    pptx = candidate / f'{STEM}_editable.pptx'
    if identity(pptx)['sha256'] != 'a3e06a01da956611a0c5563477f18ec426fed0feb7b02fe8c354dd7e4e2d8424':
        raise ValueError('Approved editable counterpart changed; review before adoption')
    previous = figures / 'FIGURE2_SELECTION.json'
    if previous.exists():
        old = json.loads(previous.read_text())
        if old['version'] != VERSION:
            archive = ROOT / 'paper/versions' / f"figure2_selection_{old['version']}.json"
            if archive.exists() and archive.read_bytes() != previous.read_bytes():
                raise ValueError('Previous selection archive has different content')
            if not archive.exists():
                shutil.copyfile(previous, archive)
    exports = [pdf]
    for ext in ('png', 'pdf'):
        target = figures / f'figure2_implementation.{ext}'
        shutil.copyfile(candidate / f'{STEM}.{ext}', target)
        exports.append(target)
    # The candidate layout SVG excludes ggplot2 overlays. Convert the complete
    # approved PDF, never adopt that incomplete layout as the stable SVG.
    svg = figures / 'figure2_implementation.svg'
    subprocess.run(['pdftocairo', '-svg', str(pdf), str(svg)], check=True)
    exports.append(svg)
    record = dict(figure=2, status='selected_for_manuscript', version=VERSION,
                  manuscript_version=(ROOT / 'paper/MANUSCRIPT_VERSION').read_text().strip(),
                  source=identity(pptx),
                  caption=identity(figures / 'figure2_caption.md'),
                  exports=[identity(p) for p in exports], width_mm=280, height_mm=357,
                  raster_long_edge_px=2600, generation='Approved vector PDF and editable PowerPoint; unchanged v016 artwork with original storage illustration',
                  scope='A: registered two-study example; B-E: separate unreviewed diagnostic GUI run. No new empirical endpoints.',
                  historical_evidence=str((candidate / f'{STEM}_evidence.json').relative_to(ROOT)))
    (figures / 'FIGURE2_SELECTION.json').write_text(json.dumps(record, indent=2)+'\n')
    captions = figures / 'README.md'
    content, n = re.subn(r'^## Figure 2\..*?(?=^## Figure 3\.)',
                        (figures/'figure2_caption.md').read_text().strip()+'\n\n',
                        captions.read_text(), flags=re.M|re.S)
    if n != 1:
        raise ValueError('Expected one Figure 2 caption in caption registry')
    captions.write_text(content)


if __name__ == '__main__':
    main()
