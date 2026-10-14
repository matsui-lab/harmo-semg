#!/usr/bin/env python3
"""Adopt the approved ggplot2 Figure 3 without regenerating its artwork."""

import hashlib
import json
import shutil
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
VERSION = "v001_2026.09.09-r1"
STEM = f"figure2_three_panel_{VERSION}"


def identity(path):
    data = path.read_bytes()
    return {"path": str(path.relative_to(ROOT)), "bytes": len(data),
            "sha256": hashlib.sha256(data).hexdigest()}


def main():
    figures = ROOT / "paper/figures"
    candidate = figures / "candidates" / STEM
    pdf = candidate / f"{STEM}.pdf"
    pptx = figures / "editable" / f"{STEM}_editable.pptx"
    expected = {
        pdf: "04f254bf8b03f90e1cef1f66be4b4fcb25acca5bc7d4c3e464bd1904ee0d8a41",
        pptx: "ba879dcea233d49607020c48ade12bc8d809098f1c4b9461b91fb7b29ea454df",
    }
    for path, digest in expected.items():
        if identity(path)["sha256"] != digest:
            raise ValueError(f"Approved artwork changed; review before adopting: {path}")
    exports = [pdf]
    for extension in ("png", "svg"):
        target = figures / f"figure3_conversion_reproducibility.{extension}"
        shutil.copyfile(candidate / f"{STEM}.{extension}", target)
        exports.append(target)
    selection = {
        "figure": 3,
        "status": "selected_for_manuscript",
        "version": VERSION,
        "selected_slide": 1,
        "manuscript_version": (ROOT / "paper/MANUSCRIPT_VERSION").read_text().strip(),
        "source": identity(pptx),
        "caption": identity(figures / "figure3_caption.md"),
        "exports": [identity(path) for path in exports],
        "width_mm": 190,
        "height_mm": 156,
        "raster_dpi": 600,
        "generation": "ggplot2 and R grid; matched native-text PowerPoint counterpart",
        "scope": "Bounded conversion coverage, computational reproducibility, and held-out reader extension",
        "historical_evidence": str((candidate / f"{STEM}_evidence.json").relative_to(ROOT)),
    }
    (figures / "FIGURE3_SELECTION.json").write_text(json.dumps(selection, indent=2) + "\n")
    print(json.dumps(selection, indent=2))


if __name__ == "__main__":
    main()
