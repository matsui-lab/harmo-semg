import json

import numpy as np
import pytest
from scipy.io import savemat

from harmo.config.automap import inspect_file, run


def test_discovery_bounded_and_no_links(tmp_path):
    from harmo.config.automap import discover
    root = tmp_path / "inputs"
    root.mkdir()
    (root / "trial.txt").write_text("time_s,x\n0,1\n1,2\n")
    (root / "description.pdf").write_bytes(b"pdf")
    (root / "archive.zip").write_bytes(b"zip")
    (root / "link.mat").symlink_to(tmp_path / "external.mat")
    result = discover(root)
    assert result["files"] == ["trial.txt"]
    assert {r["role"] for r in result["inventory"]} == {
        "signal_candidate", "documentation", "archive_requires_extraction", "excluded_symlink"}
    with pytest.raises(ValueError, match="discovery_entry_limit"):
        discover(root, max_entries=1)
    assert inspect_file(root / "trial.txt")[0]["status"] == "accepted"


def test_singleton_nested_mat(tmp_path):
    path = tmp_path / "nested.mat"
    signal = np.arange(6).reshape(3, 2)
    savemat(path, {"record": {"signals": {"values": signal,
        "labels": np.array(["CH1", "CH2"]), "fs_hz": 1000.0}}})
    result, time, values = inspect_file(path)
    assert result["options"]["reader_options"]["data_key"] == "record.signals.values"
    assert result["options"]["reader_options"]["fs_key"] == "record.signals.fs_hz"
    np.testing.assert_array_equal(values, signal)
    np.testing.assert_array_equal(time, np.arange(3) / 1000)


def test_discover_cli_generates_valid_nested_and_txt_configs(tmp_path):
    import subprocess
    import sys
    root = tmp_path / "source"
    root.mkdir()
    (root / "trial.txt").write_text("time_s,x\n0,1\n1,2\n")
    savemat(root / "nested.mat", {"record": {"values": np.arange(6).reshape(3, 2),
        "labels": np.array(["CH1", "CH2"]), "fs_hz": 1000.0}})
    out = tmp_path / "out"
    result = subprocess.run([sys.executable, "-m", "harmo.cli", "propose-mapping",
        "--input-root", str(root), "--discover", "--output", str(out)], capture_output=True, text=True)
    assert result.returncode == 0, result.stdout + result.stderr
    assert len(json.loads((out / "config.json").read_text())["file_mappings"]) == 2
    assert len(json.loads((out / "discovery.json").read_text())["files"]) == 2
    assert not json.loads((out / "report.json").read_text())["config_errors"]


def test_nested_mat_scope_and_ambiguity(tmp_path):
    path = tmp_path / "nested.mat"
    block = {"values": np.arange(6).reshape(3, 2),
             "labels": np.array(["CH1", "CH2"]), "fs_hz": 1000.0}
    savemat(path, {"a": block, "b": block})
    with pytest.raises(ValueError, match="ambiguous_or_missing_time"):
        inspect_file(path)
    savemat(path, {"record": {"values": block["values"]}, "labels": block["labels"], "fs_hz": 1000.0})
    with pytest.raises(ValueError, match="cross_scope"):
        inspect_file(path)


def test_nested_struct_array_is_not_first_trial(tmp_path):
    path = tmp_path / "trials.mat"
    records = np.zeros((1, 2), dtype=[("values", object)])
    for i in range(2):
        records[0, i]["values"] = np.ones((3, 2))
    savemat(path, {"records": records})
    with pytest.raises(ValueError, match="struct_array_requires_trial_selection"):
        inspect_file(path)


def table(tmp_path, text):
    path = tmp_path / "input.csv"
    path.write_text(text)
    return path


def test_table_preserves_all_missing_rows(tmp_path):
    result, time, values = inspect_file(table(tmp_path, "time_s,CH1,CH2\n0,1,2\n1,,\n2,3,4\n"))
    assert result["status"] == "accepted"
    assert result["checks"]["all_missing_rows"] == 1
    assert result["semantic_status"]["CH1"]["muscle"] == "unknown"
    assert np.isnan(values[1]).all()
    np.testing.assert_array_equal(time, [0, 1, 2])


@pytest.mark.parametrize("text,reason", [
    ("time,x\n0,1\n1,2\n", "explicit_time_unit"),
    ("time_s,time_ms,x\n0,0,1\n1,1000,2\n", "ambiguous_or_missing_time"),
    ("time_s,x,x\n0,1,2\n1,2,3\n", "duplicate_label"),
    ("time_s,id,x\n0,1,2\n1,1,3\n", "identifier_column"),
    ("time_s,channel,value\n0,1,2\n1,1,3\n", "identifier_column"),
    ("time_s,x\n0,1\n0,2\n", "invalid_time_axis"),
    ("time_s,x\n0,inf\n1,2\n", "infinite_signal"),
    ("time_s,x\n0,1,2\n1,2\n", "malformed_delimiter"),
    ("0,1\n1,2\n2,3\n", "header_required"),
    ("time_s,x\n0,hi\n1,2\n", "convert string"),
])
def test_review_cases(tmp_path, text, reason):
    with pytest.raises(ValueError, match=reason):
        inspect_file(table(tmp_path, text))


def test_time_metadata_and_conflict(tmp_path):
    p = table(tmp_path, "time,x\n0,1\n1,2\n")
    assert inspect_file(p, {"time_unit": "s", "source_reference": "example protocol"})[0]["status"] == "accepted"
    p.write_text("time_s,x\n0,1\n1,2\n")
    with pytest.raises(ValueError, match="conflict"):
        inspect_file(p, {"time_unit": "ms", "source_reference": "example"})


def test_explicit_label_not_abbreviation(tmp_path):
    r, _, _ = inspect_file(table(tmp_path, "time_s,Right Tibialis Anterior [mV],TA\n0,1,2\n1,3,4\n"))
    assert r["semantic_status"]["Right Tibialis Anterior [mV]"]["side"] == "right"
    assert r["semantic_status"]["TA"]["muscle"] == "unknown"


def test_integer_precision_loss_is_not_silent(tmp_path):
    with pytest.raises(ValueError, match="precision"):
        inspect_file(table(tmp_path, "time_s,x\n0,9007199254740993\n1,1\n"))


def test_symlink(tmp_path):
    source = tmp_path / "source"
    source.mkdir()
    external = table(tmp_path, "time_s,x\n0,1\n1,2\n")
    (source / "escape.csv").symlink_to(external)
    assert run(source, tmp_path / "out", ["escape.csv"])["counts"]["needs_review"] == 1


def test_roundtrip_decimal_reader(tmp_path):
    result, _, v = inspect_file(table(tmp_path, "time_s,x\n0,0.0082988271627990225\n1,-0.00011927488569509987\n"))
    assert result["options"]["reader_options"]["float_precision"] == "round_trip"
    assert v[0, 0] == float("0.0082988271627990225")


def test_size_limit_before_worker(tmp_path, monkeypatch):
    import harmo.config.automap as module
    source = tmp_path / "source"
    source.mkdir()
    (source / "big.csv").write_bytes(b"12345")
    monkeypatch.setattr(module, "MAX_BYTES", 4)
    report = run(source, tmp_path / "out", ["big.csv"])
    assert report["files"][0]["reason"] == "file_size_limit"


def test_worker_timeout(tmp_path, monkeypatch):
    import subprocess
    import harmo.config.automap as module
    source = tmp_path / "source"
    source.mkdir()
    (source / "a.csv").write_text("time_s,x\n0,1\n1,2\n")
    def timeout(*args, **kwargs):
        raise subprocess.TimeoutExpired("worker", 30)
    monkeypatch.setattr(module.subprocess, "run", timeout)
    assert run(source, tmp_path / "out", ["a.csv"])["counts"]["needs_review"] == 1


def test_corrupt_file_and_cli_exit(tmp_path):
    import os
    import subprocess
    import sys
    source = tmp_path / "source"
    source.mkdir()
    (source / "broken.mat").write_bytes(b"not a MATLAB file")
    manifest = tmp_path / "manifest.json"
    manifest.write_text(json.dumps({"files": ["broken.mat"]}))
    child = subprocess.run([sys.executable, "-m", "harmo.cli", "propose-mapping",
                            "--input-root", str(source), "--manifest", str(manifest),
                            "--output", str(tmp_path / "out")], capture_output=True, text=True, env=os.environ)
    assert child.returncode == 2, child.stderr + child.stdout
    assert json.loads((tmp_path / "out/report.json").read_text())["counts"]["needs_review"] == 1


def test_regex_literal_is_exact(tmp_path):
    import re
    source = tmp_path / "source"
    source.mkdir()
    name = "a(1).csv"
    (source / name).write_text("time_s,x\n0,1\n1,2\n")
    assert run(source, tmp_path / "out", [name])["counts"]["accepted"] == 1
    config = json.loads((tmp_path / "out/config.json").read_text())
    mapping = config["file_mappings"][0]
    regex = mapping["input_path_template"].replace("{file}", mapping["path_patterns"]["file"])
    assert re.search(regex, name)
    assert not re.search(regex, name+".csv")
    assert not re.search(regex, "a1.csv")


def mat(tmp_path, **updates):
    p = tmp_path / "input.mat"
    data = {"emg": np.arange(6).reshape(3, 2), "labels": np.array(["CH1", "CH2"]), "fs_hz": 1000.0}
    data.update(updates)
    savemat(p, data)
    return p


def test_mat_fs(tmp_path):
    r, t, v = inspect_file(mat(tmp_path))
    assert r["status"] == "accepted"
    np.testing.assert_array_equal(t, [0, .001, .002])
    np.testing.assert_array_equal(v, np.arange(6).reshape(3, 2))


def test_mat_fixed_width_labels(tmp_path):
    r, _, _ = inspect_file(mat(tmp_path, labels=np.array(["soleus", "tibialisanterior"])))
    assert r["channels"] == ["soleus", "tibialisanterior"]
    assert r["options"]["reader_options"]["strip_channel_names"] is True


def test_mat_reserved_columns(tmp_path):
    with pytest.raises(ValueError, match="identifier"):
        inspect_file(mat(tmp_path, labels=np.array(["channel", "value"])))


@pytest.mark.parametrize("updates,reason", [
    ({"emg": np.ones((2,2))}, "ambiguous_array_orientation"),
    ({"time_s": np.array([0, 1, 2])}, "time_rate_conflict"),
    ({"fs_hz": -2}, "invalid_sampling_rate"),
    ({"second": np.ones((3,2))}, "ambiguous_signal_array"),
    ({"labels": np.array(["CH1", "CH1"])}, "duplicate"),
    ({"structure": {"emg": [1, 2]}}, "ambiguous_signal_array"),
])
def test_mat_review(tmp_path, updates, reason):
    with pytest.raises(ValueError, match=reason):
        inspect_file(mat(tmp_path, **updates))


def test_manifest_trials_and_repeat(tmp_path):
    source = tmp_path / "source"
    source.mkdir()
    (source / "a.csv").write_text("time_s,CH1\n0,1\n1,2\n")
    (source / "b.tsv").write_text("time_ms\tCH1\n0\t1\n2\t2\n")
    (source / "unknown.zip").write_bytes(b"not extracted")
    runs = [run(source, tmp_path / name, ["a.csv", "b.tsv", "unknown.zip"]) for name in ("one", "two")]
    assert runs[0] == runs[1]
    assert runs[0]["counts"] == {"accepted": 2, "partial": 0, "needs_review": 0, "unsupported": 1}
    config = json.loads((tmp_path / "one/config.json").read_text())
    assert config["dataset_status"] == "draft"
    with pytest.raises(ValueError, match="already_exists"):
        run(source, tmp_path / "one", ["a.csv"])


def test_empty_and_path_escape(tmp_path):
    source = tmp_path / "source"
    source.mkdir()
    with pytest.raises(ValueError, match="manifest"):
        run(source, tmp_path / "out", [])
    result = run(source, tmp_path / "out", ["../other.csv"])
    assert result["counts"]["needs_review"] == 1


def test_source_mutation_detected(tmp_path, monkeypatch):
    from harmo.ingest.readers import csv as module
    original = module.read_csv_file
    def mutate(path, **kwargs):
        frame = original(path, **kwargs)
        from pathlib import Path
        Path(path).write_text("time_s,x\n0,3\n1,4\n")
        return frame
    monkeypatch.setattr(module, "read_csv_file", mutate)
    with pytest.raises(ValueError, match="source_changed"):
        inspect_file(table(tmp_path, "time_s,x\n0,1\n1,2\n"))
