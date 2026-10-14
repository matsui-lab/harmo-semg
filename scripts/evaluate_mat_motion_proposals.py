"""Direct array checks for every accepted multimodal MAT proposal."""
import argparse
import json
from pathlib import Path
import numpy as np
from scipy.io import loadmat
from harmo.config.automap import digest, save
from harmo.ingest.readers.mat import read_mat_file


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--input-root", type=Path, required=True)
    parser.add_argument("--proposals", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    report = json.loads((args.proposals / "report.json").read_text())
    totals = {name: dict(records=0, values=0) for name in ("EMG", "Marker", "Grf")}
    held = []
    for file_index, file in enumerate(report["files"]):
        path = args.input_root / file["source"]
        assert digest(path.read_bytes()) == file["source_sha256"]
        source = loadmat(path)["s"][0, 0]
        seen = set()
        reader_checked = set()
        for index, record in enumerate(file["records"]):
            tokens = record["data_key"].split(".")
            assert tokens[:2] == ["s", "Data"] and len(tokens) == 4
            trial, signal = int(tokens[2]), tokens[3]
            assert (trial, signal) not in seen
            seen.add((trial, signal))
            labels_key, rate_key = {"EMG": ("EmgVarName", "EMGFreq"), "Marker": ("MarkerVarName", "KinFreq"), "Grf": ("GrfVarName", "GrfFreq")}[signal]
            expected = source["Data"].flat[trial][signal].T
            labels = [str(v).strip() for v in source[labels_key].flat]
            time = np.arange(len(expected)) / float(source[rate_key].item())
            assert record["channels"] == labels
            with np.load(args.proposals / ("file-%04d" % file_index) / ("record-%04d" % index) / "converted.npz", allow_pickle=False) as saved:
                np.testing.assert_array_equal(saved["values"], expected)
                np.testing.assert_array_equal(saved["time"], time)
            if signal not in reader_checked:
                frame = read_mat_file(str(path), record["options"]["reader_options"])
                np.testing.assert_array_equal(frame[labels].to_numpy(), expected)
                np.testing.assert_array_equal(frame["time"], time)
                reader_checked.add(signal)
            totals[signal]["records"] += 1
            totals[signal]["values"] += expected.size
        # Every original dynamic stream must be accepted or explicitly held.
        excluded = {row["data_key"] for row in file["held"]}
        for trial in range(source["Data"].size):
            for signal in totals:
                assert (trial, signal) in seen or "s.Data.%d.%s" % (trial, signal) in excluded
        held.extend(dict(source=file["source"], **row) for row in file["held"])
        assert digest(path.read_bytes()) == file["source_sha256"]
    result = dict(totals=totals, held=held, all_accepted_values_equal=True,
                  scope="Registered layout, direct original-array comparison; no independent decoder or scientific qualification")
    save(args.output, result)
    print(json.dumps(totals, indent=2))


if __name__ == "__main__":
    main()
