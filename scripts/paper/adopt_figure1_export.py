#!/usr/bin/env python3
"""Publish slide 1 of the selected Figure 1 PDF without modifying its PPTX."""

from __future__ import annotations

import argparse
import hashlib
import json
import subprocess
from pathlib import Path

from pypdf import PdfReader, PdfWriter


VERSION = "v018_2026.09.16-r1"


def identity(root: Path, path: Path) -> dict:
    return {
        "path": str(path.relative_to(root)),
        "bytes": path.stat().st_size,
        "sha256": hashlib.sha256(path.read_bytes()).hexdigest(),
    }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--pdf", required=True, type=Path,
                        help="PDF exported from the selected PPTX with its original fonts")
    parser.add_argument("--root", type=Path, default=Path(__file__).resolve().parents[2])
    args = parser.parse_args()
    root = args.root.resolve()
    figures = root / "paper/figures"
    source = figures / "editable" / f"figure1_four_panel_{VERSION}_editable.pptx"
    output = figures / "editable" / f"figure1_four_panel_{VERSION}.pdf"
    source_identity = identity(root, source)
    reader = PdfReader(args.pdf)
    if len(reader.pages) != 5:
        raise ValueError("The selected Figure 1 deck must contain five slides")
    page = reader.pages[0]
    for expected in ["Public availability", "Common processing", "Automatic attachment",
                     "Check cited evidence", "Release policy", "Derived signal", "Do not pool"]:
        if expected not in page.extract_text():
            raise ValueError(f"Missing selected-panel text: {expected}")
    width = 190 / 25.4 * 72
    page.scale_to(width, width * float(page.mediabox.height) / float(page.mediabox.width))
    writer = PdfWriter()
    writer.add_page(page)
    writer.add_metadata({"/Title": "Figure 1. Evidence-linked harmonization of public electromyography",
                         "/Author": "Yusuke Matsui", "/Producer": "harmo-semg"})
    with output.open("wb") as stream:
        writer.write(stream)
    png = figures / "figure1_evidence_framework.png"
    svg = figures / "figure1_evidence_framework.svg"
    subprocess.run(["pdftoppm", "-f", "1", "-singlefile", "-r", "500", "-png",
                    str(output), str(png.with_suffix(""))], check=True)
    subprocess.run(["pdftocairo", "-f", "1", "-l", "1", "-svg", str(output), str(svg)], check=True)
    if identity(root, source) != source_identity:
        raise RuntimeError("Selected PPTX changed during export")
    selection = {
        "figure": 1,
        "status": "selected_for_manuscript",
        "version": VERSION,
        "selected_slide": 1,
        "manuscript_version": (root / "paper/MANUSCRIPT_VERSION").read_text().strip(),
        "source": source_identity,
        "caption": identity(root, figures / "figure1_caption.md"),
        "exports": [identity(root, item) for item in [output, png, svg]],
        "width_mm": 190,
        "raster_dpi": 500,
        "pdf_content": "Vector text, tables and diagrams with original raster illustration and logos",
        "scope": "Conceptual and procedural examples; not empirical result plots",
    }
    (figures / "FIGURE1_SELECTION.json").write_text(json.dumps(selection, indent=2) + "\n")
    print(json.dumps(selection, indent=2))


if __name__ == "__main__":
    main()
