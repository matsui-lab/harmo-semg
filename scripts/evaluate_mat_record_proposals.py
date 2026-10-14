"""Check every generated EMG record directly against original study_38 arrays."""
import argparse
import json
from pathlib import Path

import numpy as np
from scipy.io import loadmat
from harmo.config.automap import digest, save
from harmo.ingest.readers.mat import read_mat_file


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--input-root", required=True, type=Path)
    parser.add_argument("--proposals", required=True, type=Path)
    parser.add_argument("--output", required=True, type=Path)
    args = parser.parse_args()
    report = json.loads((args.proposals / "report.json").read_text())
    results = []
    for file_index, row in enumerate(report["files"]):
        path = args.input_root / row["source"]
        before = digest(path.read_bytes())
        assert before == row["source_sha256"]
        source = loadmat(path)["s"][0, 0]
        trials = source["Data"]
        proposed = {r["data_key"]: (index, r) for index, r in enumerate(row["records"]) if r["data_key"].endswith(".EMG")}
        assert len(proposed) == trials.size
        cells = 0
        for index, trial in enumerate(trials.flat):
            key = "s.Data.%d.EMG" % index
            output_index, record = proposed[key]
            expected = trial["EMG"].T
            labels = [str(v).strip() for v in source["EmgVarName"].flat]
            assert labels == record["channels"]
            expected_time = np.arange(len(expected)) / float(source["EMGFreq"].item())
            if index in (0, trials.size - 1):
                frame = read_mat_file(str(path), record["options"]["reader_options"])
                np.testing.assert_array_equal(frame[labels].to_numpy(), expected)
                np.testing.assert_array_equal(frame["time"].to_numpy(), expected_time)
            with np.load(args.proposals / ("file-%04d" % file_index) / ("record-%04d" % output_index) / "converted.npz", allow_pickle=False) as converted:
                np.testing.assert_array_equal(converted["values"], expected)
                np.testing.assert_array_equal(converted["time"], expected_time)
            cells += expected.size
        assert digest(path.read_bytes()) == before
        results.append(dict(source=row["source"], source_sha256=before, records=trials.size,
                            checked_values=cells, exact_match=True, generated_reader_checks=2))
    summary = dict(files=results, record_count=sum(r["records"] for r in results),
        checked_values=sum(r["checked_values"] for r in results), all_exact=True,
        scope="Development check against all original EMG arrays in the registered layout; not an independent decoder, scientific qualification, or reproduction of all 78 mappings.")
    save(args.output, summary)
    print(json.dumps(summary, indent=2))


if __name__ == "__main__":
    main()
