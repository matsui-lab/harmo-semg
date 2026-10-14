"""Publish reader-facing figure labels without changing scientific source data."""
from pathlib import Path
import hashlib
import json
import shutil
import subprocess
import os

ROOT = Path(__file__).resolve().parents[2]
VERSION = "v029_2026.09.26-r3"
STEMS = {
    2: "figure2_implementation_gui_v018_2026.09.26-r1",
    3: "figure3_integrated_results_v009_2026.09.26-r1",
}

def info(path):
    return {"path": str(path.relative_to(ROOT)), "bytes": path.stat().st_size,
            "sha256": hashlib.sha256(path.read_bytes()).hexdigest()}

def main():
    for number, stem in STEMS.items():
        folder = ROOT / "paper/figures/candidates" / stem
        caption = ROOT / f"paper/figures/figure{number}_caption.md"
        shutil.copy2(caption, folder / f"{stem}_caption.md")
        if number == 2:
            env = dict(os.environ, FONTCONFIG_FILE="/opt/homebrew/etc/fonts/fonts.conf")
            subprocess.run(["pdftocairo", "-svg", str(folder / f"{stem}.pdf"),
                            str(folder / f"{stem}.svg")], check=True, env=env,
                           stderr=subprocess.DEVNULL)
            for suffix in ("pdf", "png", "svg"):
                shutil.copy2(folder / f"{stem}.{suffix}",
                             ROOT / f"paper/figures/figure2_implementation.{suffix}")
        selection_path = ROOT / f"paper/figures/FIGURE{number}_SELECTION.json"
        selection = json.loads(selection_path.read_text())
        selection.update(version="v" + stem.split("_v")[-1],
                         manuscript_version=VERSION, caption_updated_in_manuscript=VERSION,
                         source=info(folder / f"{stem}_editable.pptx"),
                         caption=info(caption),
                         exports=[info(folder / f"{stem}.{ext}") for ext in ("pdf", "png", "svg")],
                         current_evidence=str((folder / f"{stem}_evidence.json").relative_to(ROOT)),
                         label_policy="Public name or first author and publication year; internal IDs only in Table S1 and software.",
                         scientific_values_changed=False)
        if number == 2:
            selection["generation"] = "Approved vector layout with public dataset labels; editable PowerPoint and original GUI screenshots."
        selection_path.write_text(json.dumps(selection, indent=2) + "\n")
    (ROOT / "paper/figures/candidates/IMPLEMENTATION_FIGURE_VERSION").write_text(
        "v018_2026.09.26-r1\n")

if __name__ == "__main__":
    main()
