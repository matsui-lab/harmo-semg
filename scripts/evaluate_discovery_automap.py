"""Assess automatic proposals against frozen source identities, not their settings."""
import argparse
import json
from pathlib import Path

from harmo.config.automap import discover, run, save


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--input-root", action="append", required=True)
    parser.add_argument("--output", required=True)
    args = parser.parse_args()
    out = Path(args.output).resolve()
    out.mkdir(parents=True, exist_ok=False)
    sources, groups = {}, []
    # Produce proposals before opening the frozen reference.
    for index, root in enumerate(args.input_root):
        manifest = discover(root)
        save(out / ("discovery-%02d.json" % index), manifest)
        report = run(root, out / ("trial-%02d" % index), manifest["files"])
        groups.append(dict(input_root=str(Path(root).resolve()), counts=report["counts"]))
        for row in report["files"]:
            if row.get("source_sha256"):
                sources.setdefault(row["source_sha256"], []).append(dict(
                    input_root=str(Path(root).resolve()), source=row["source"],
                    status=row["status"], reason=row.get("reason")))
    reference_path = Path(__file__).resolve().parents[1] / "paper/results/validation/mapping_outcomes.json"
    reference = json.loads(reference_path.read_text())
    coverage = []
    for row in reference:
        matches = sources.get(row.get("source_sha256"), [])
        status = ("candidate_requires_numerical_comparison" if any(r["status"] == "accepted" for r in matches)
                  else "available_but_not_automapped" if matches else "source_not_evaluated")
        coverage.append(dict(mapping_id=row["mapping_id"], status=status, sources=matches))
    save(out / "coverage.json", coverage)
    summary = dict(frozen_mapping_denominator=len(reference), groups=groups,
        coverage_counts={status: sum(r["status"] == status for r in coverage) for status in (
            "candidate_requires_numerical_comparison", "available_but_not_automapped", "source_not_evaluated")},
        scope="Source-hash coverage only. No frozen reader settings supplied to proposal generation. Not numerical reproduction or independent validation.")
    save(out / "summary.json", summary)
    print(json.dumps(summary, indent=2))


if __name__ == "__main__":
    main()
