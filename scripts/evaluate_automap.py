"""Reproduce the frozen v001 structural-automation demonstration, without ingestion."""
from __future__ import annotations

import argparse
import csv
import json
import importlib.metadata
import platform
import sys
from pathlib import Path

import numpy as np
from scipy.io import savemat

from harmo.config.automap import digest, run, save


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", required=True, type=Path)
    args = parser.parse_args()
    repo = Path(__file__).resolve().parents[1]
    out = args.output.resolve()
    if out.exists():
        raise ValueError("output must be new")
    out.mkdir(parents=True)
    inputs = out / "standard_inputs"
    missing = out / "unit_missing_inputs"
    inputs.mkdir()
    missing.mkdir()
    hashes, expected, metadata = {}, {}, {}

    def track(path):
        key = str(path.relative_to(repo))
        hashes[key] = digest(path.read_bytes())
        return key + "#sha256=" + hashes[key]

    records = sorted((repo / "paper/results/atlas_materialization/records").glob("*/reference/recording.bundle.json"))
    assert {p.parent.parent.name for p in records} == {
        "study_28-004", "study_28-008", "study_33-002", "study_33-005", "study_33-008", "study_33-011"}
    for bundle in records:
        track(bundle)
        samples = bundle.parent / "normalized.samples.csv"
        timestamps = bundle.parent / "normalized.timestamps.csv"
        track(samples)
        reference = track(timestamps)
        with samples.open() as stream:
            rows = list(csv.reader(stream))
        with timestamps.open() as stream:
            time_rows = list(csv.reader(stream))
        assert time_rows[0] == ["time_seconds"]
        assert len(time_rows) == len(rows)
        labels = rows[0]
        values = np.array([[float(x) if x.strip() else np.nan for x in r] for r in rows[1:]])
        time = np.array([float(r[0]) for r in time_rows[1:]])
        name = bundle.parent.parent.name
        for directory, header in ((inputs, "time_seconds"), (missing, "time")):
            with (directory / (name + ".csv")).open("x", newline="") as stream:
                writer = csv.writer(stream)
                writer.writerow([header] + labels)
                writer.writerows([t + r for t, r in zip(time_rows[1:], rows[1:])])
        savemat(inputs / (name + ".mat"), {"signal": values, "channel_names": np.array(labels), "time_seconds": time})
        for suffix in (".csv", ".mat"):
            expected[name + suffix] = (time, values, labels)
        metadata[name + ".csv"] = {"time_unit": "s", "source_reference": reference}

    raw_mat = repo / "downloads/additional_framework_v001_2026.09.12-r1"
    raw_csv = repo / "studies/study_1/notebook"
    raw_names = ["Subject%d.mat" % n for n in range(1, 11)]
    for path in [raw_mat / n for n in raw_names] + [raw_csv / "Ref01_sss_le_shoes05.csv"]:
        if path.exists():
            track(path)
    save(out / "source_inventory.json", hashes)
    groups = [
        ("standard_real_waveforms", inputs, sorted(expected), {}, 12),
        ("unit_missing", missing, sorted(metadata), {}, 0),
        ("unit_supplied", missing, sorted(metadata), metadata, 6),
        ("original_mat", raw_mat, raw_names, {}, 0),
        ("original_csv", raw_csv, ["Ref01_sss_le_shoes05.csv"], {}, 0),
    ]
    summary = {"protocol": "automap_evaluation_protocol_v001_2026.09.13-r1.md",
               "python": sys.version, "platform": platform.platform(), "groups": {},
               "scope": "Development demonstration; not independent external validation or warehouse equivalence."}
    summary["dependencies"] = {name: importlib.metadata.version(name) for name in ("numpy", "pandas", "scipy", "jsonschema", "PyYAML")}
    summary["implementation_sha256"] = {path: digest((repo / path).read_bytes()) for path in (
        "src/harmo/config/automap.py", "src/harmo/cli.py", "src/harmo/ingest/readers/csv.py",
        "src/harmo/ingest/readers/mat.py", "src/harmo/ingest/readers/__init__.py", "scripts/evaluate_automap.py")}
    for group, root, files, meta, wanted in groups:
        manifest = {"files": files, "metadata": meta,
                    "sha256": {f: digest((root / f).read_bytes()) if (root / f).exists() else None for f in files}}
        save(out / (group + "_manifest.json"), manifest)
        reports = [run(root, out / (group + "_repeat_%d" % i), files, meta) for i in (1, 2)]
        assert reports[0] == reports[1], group + ": nondeterministic report"
        configs = [json.loads((out / (group + "_repeat_%d" % i) / "config.json").read_text()) for i in (1, 2)]
        assert configs[0] == configs[1]
        counts = reports[0]["counts"]
        equality = 0
        rows_checked = 0
        signal_cells = 0
        for index, row in enumerate(reports[0]["files"]):
            if row["status"] != "accepted":
                continue
            time, values, labels = expected[row["source"]]
            assert row["channels"] == labels
            for repeat in (1, 2):
                with np.load(out / (group + "_repeat_%d" % repeat) / ("file-%04d" % index) / "converted.npz") as converted:
                    np.testing.assert_array_equal(converted["time"], time)
                    np.testing.assert_array_equal(converted["values"], values)
            equality += 1
            rows_checked += len(time)
            signal_cells += values.size
        summary["groups"][group] = {"counts": counts, "repeat_equal": True,
                                    "expected_accepted": wanted, "expected_result_met": counts["accepted"] == wanted,
                                    "exact_reference_matches": equality, "rows_checked": rows_checked,
                                    "signal_cells_checked": signal_cells,
                                    "reasons": [{"source": r["source"], "reason": r.get("reason")} for r in reports[0]["files"] if r["status"] != "accepted"]}
        print(group, counts, flush=True)
    assert all(digest((repo / p).read_bytes()) == h for p, h in hashes.items())
    summary["original_sources_unchanged"] = True
    summary["source_file_count"] = len(hashes)
    save(out / "summary.json", summary)


if __name__ == "__main__":
    main()
