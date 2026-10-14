"""Summarize fixed pilot receipts; never infer scientific correctness from holds."""
import argparse
import hashlib
import json
from pathlib import Path

import numpy as np
import wfdb


def read(path):
    return json.loads(path.read_text())


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("root", type=Path)
    args = parser.parse_args()
    root = args.root.resolve()
    rows, reference = [], []
    for slug in ("taichidb", "grabmyo", "hd-semg"):
        folder = root / slug
        execution = read(folder / "execution.json")
        summaries = list((folder / "workflow/runs").glob("*/automatic.summary.json"))
        assert len(summaries) == 1
        result = read(summaries[0])
        entry = result["intake"]["entries"][0]
        assessment = result["assessment"]
        descriptions = list(folder.rglob("description.html"))
        assert len(descriptions) == 1
        stage = descriptions[0].parent
        conversions = []
        for record in entry["details"]["records"]:
            if record["status"] != "converted":
                continue
            location = Path(record["location"])
            header = stage / "raw" / record["name"]
            original = wfdb.rdrecord(str(header.with_suffix("")), physical=False)
            expected = (original.d_signal.astype(float) - np.asarray(original.baseline)) / np.asarray(original.adc_gain)
            # This pilot's converted records have no missing-sample sentinel.
            conversion = read(location / "conversion.json")
            assert conversion["missing_samples"] == 0
            with np.load(location / "normalized.npz", allow_pickle=False) as normalized:
                np.testing.assert_array_equal(normalized["values"], expected)
                np.testing.assert_array_equal(normalized["time"], np.arange(original.sig_len) / original.fs)
            bundle = read(location / "recording.semantic.bundle.json")
            conversions.append({"record": record["name"], "shape": list(expected.shape),
                                "fs": original.fs, "independent_values_exact": True,
                                "independent_time_exact": True, "channels": bundle["channels"],
                                "receipt": str((location / "conversion.json").relative_to(root))})
        rows.append({"dataset": slug, "url": entry["url"],
                     "wall_seconds": execution["wall_seconds"], "timed_out": execution["timed_out"],
                     "exit_code": execution["exit_code"], "code_commit": execution["code_commit"],
                     "description_downloaded": True, "directory_pages": len(list(stage.glob("listing-*.html"))),
                     "inventory_complete": (stage / "inventory.json").exists(),
                     "selected_records": entry["details"].get("selected_records"),
                     "converted_records": len(conversions), "conversions": conversions,
                     "records_at_assessment": assessment["records"],
                     "comparison_jobs": len(assessment["jobs"]), "exported": assessment["exported"],
                     "linked_evidence_documents": len(result["evidence"]["documents"]),
                     "intake_errors": entry["errors"] + entry["details"]["errors"],
                     "manual_source_configurations": execution["manual_source_configurations"],
                     "code_changes": execution["code_changes"], "local_llm_used": False,
                     "scientific_accuracy_evaluated": False,
                     "receipt": str(summaries[0].relative_to(root))})
        sources = []
        for path in [descriptions[0], *sorted((stage / "raw").rglob("*.hea"))]:
            sources.append({"path": str(path.relative_to(root)),
                            "sha256": hashlib.sha256(path.read_bytes()).hexdigest(),
                            "kind": "repository_description" if path.suffix == ".html" else "WFDB_header"})
        targets = [p.relative_to(stage / "raw").as_posix() for p in sorted((stage / "raw").rglob("*.hea"))]
        reference.append({"dataset": slug, "url": entry["url"], "sources": sources,
                          "record_targets": targets,
                          "scope_note": "No record targets means acquisition has not reached record selection; do not invent record-level reference judgments.",
                          "reviewers": [{"reviewer_id": None, "status": "not_started",
                                         "facts": [{"field": field, "value": None, "status": "unreviewed",
                                                    "record_scope": None, "channel_scope": None,
                                                    "source_path": None, "source_sha256": None,
                                                    "quote": None, "locator": None}
                                                   for field in ("sensor_type", "muscle", "side", "unit", "task",
                                                                 "processing_history", "event_definition", "normalization_reference")],
                                         "purpose_judgments": [{"purpose": purpose, "record_pair": None,
                                                                "decision": None, "reason": None,
                                                                "required_processing": None, "evidence": []}
                                                               for purpose in ("activation-timing", "amplitude-pooling")]}
                                        for _ in range(2)]})
    summary = {"version": "general-use-pilot/001", "scope": "Exploratory no-adjustment pilot, not a scientific accuracy benchmark",
               "datasets": rows, "studies_with_conversion": sum(bool(r["converted_records"]) for r in rows),
               "studies_with_comparison_jobs": sum(bool(r["comparison_jobs"]) for r in rows),
               "studies_with_exports": sum(bool(r["exported"]) for r in rows)}
    for name, value in (("pilot.summary.json", summary), ("expert_reference.blank.json", reference)):
        target = root / name
        with target.open("x") as stream:
            json.dump(value, stream, indent=2, ensure_ascii=False)
    print(json.dumps({k: v for k, v in summary.items() if k != "datasets"}, ensure_ascii=False))


if __name__ == "__main__":
    main()
