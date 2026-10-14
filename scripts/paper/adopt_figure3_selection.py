#!/usr/bin/env python3
"""Adopt the approved pastel ggplot2 Figure 4 without redrawing it."""
import hashlib
import json
import shutil
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
VERSION = "v002_2026.09.09-r2"
STEM = f"figure3_three_panel_{VERSION}"


def identity(path):
    data = path.read_bytes()
    return {"path": str(path.relative_to(ROOT)), "bytes": len(data),
            "sha256": hashlib.sha256(data).hexdigest()}


def main():
    figures = ROOT / "paper/figures"
    candidate = figures / "candidates" / STEM
    pdf = candidate / f"{STEM}.pdf"
    if identity(pdf)["sha256"] != "31598081074e73748bd1c73562add3962ef80022ebb15800792f3c4b1f1b7a99":
        raise ValueError("Approved Figure 4 changed; review before adoption")
    exports = [pdf]
    for extension in ("png", "svg"):
        target = figures / f"figure4_evidence_qualification.{extension}"
        shutil.copyfile(candidate / f"{STEM}.{extension}", target)
        exports.append(target)
    selection = {
        "figure": 4, "status": "selected_for_manuscript", "version": VERSION,
        "manuscript_version": (ROOT / "paper/MANUSCRIPT_VERSION").read_text().strip(),
        "source": identity(candidate / f"{STEM}.svg"),
        "caption": identity(figures / "figure4_caption.md"),
        "exports": [identity(path) for path in exports],
        "width_mm": 190, "height_mm": 166, "raster_dpi": 600,
        "generation": "ggplot2 and R grid; pastel plot marks with dark text",
        "scope": "Registered evidence coverage, format concordance, and conditional intended use",
    }
    (figures / "FIGURE4_SELECTION.json").write_text(json.dumps(selection, indent=2) + "\n")
    print(json.dumps(selection, indent=2))


if __name__ == "__main__":
    main()
