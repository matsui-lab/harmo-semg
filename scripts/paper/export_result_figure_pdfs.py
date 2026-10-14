#!/usr/bin/env python3
"""Export deterministic 190-mm PDFs from the 500-dpi result-figure PNGs."""

from __future__ import annotations

import argparse
import time
from pathlib import Path

from PIL import Image


FIGURES = {
    2: "figure2_conversion_reproducibility",
    3: "figure3_evidence_qualification",
    4: "figure4_full_record_query",
    5: "figure5_physiology_positive_control",
    6: "figure6_mechanics_release_boundary",
}

WIDTH_MM = 190
DPI = 500
EXPECTED_PIXELS = (3740, 2104)
FIXED_TIMESTAMP = time.gmtime(946684800)


def export_pdf(source: Path, output: Path, figure_number: int) -> None:
    with Image.open(source) as image:
        if image.size != EXPECTED_PIXELS:
            raise ValueError(
                f"{source}: expected {EXPECTED_PIXELS}, found {image.size}"
            )
        rgb = image.convert("RGB")
        temporary = output.with_name(output.name + ".tmp")
        rgb.save(
            temporary,
            "PDF",
            resolution=float(DPI),
            quality=100,
            subsampling=0,
            title=f"Figure {figure_number}",
            producer="harmo-semg",
            creationDate=FIXED_TIMESTAMP,
            modDate=FIXED_TIMESTAMP,
        )
        rgb.close()
    temporary.replace(output)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--root",
        type=Path,
        default=Path(__file__).resolve().parents[2],
    )
    arguments = parser.parse_args()
    root = arguments.root.resolve()
    figure_dir = root / "paper/figures"
    editable_dir = figure_dir / "editable"
    editable_dir.mkdir(parents=True, exist_ok=True)

    outputs = []
    for number, stem in FIGURES.items():
        source = figure_dir / f"{stem}.png"
        output = editable_dir / f"{stem}.pdf"
        export_pdf(source, output, number)
        outputs.append(
            {
                "figure": number,
                "source": str(source.relative_to(root)),
                "output": str(output.relative_to(root)),
                "width_mm": WIDTH_MM,
                "dpi": DPI,
                "pixels": list(EXPECTED_PIXELS),
            }
        )

    for item in outputs:
        print(
            "Figure {figure}: {output} ({width_mm} mm, {dpi} dpi)".format(
                **item
            )
        )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
