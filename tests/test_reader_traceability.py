import hashlib
import os
import subprocess
import sys
from pathlib import Path

import pandas as pd
import pytest

from harmo.evidence.io import source_hashes
from harmo.ingest.readers import _read_files_parallel


ROOT = Path(__file__).resolve().parents[1]


def test_lightweight_reader_import_does_not_load_pyspark():
    environment = dict(os.environ)
    environment["PYTHONPATH"] = str(ROOT / "src")
    result = subprocess.run(
        [
            sys.executable,
            "-c",
            (
                "import builtins, sys; original = builtins.__import__; "
                "builtins.__import__ = lambda name, *args, **kwargs: "
                "(_ for _ in ()).throw(ModuleNotFoundError(name='pyspark')) "
                "if name == 'pyspark' or name.startswith('pyspark.') "
                "else original(name, *args, **kwargs); "
                "import harmo.ingest.readers.c3d, harmo.ingest.readers.mat, "
                "harmo.ingest.readers.sto, harmo.ingest.readers.trc; "
                "assert not any(name == 'pyspark' or name.startswith('pyspark.') "
                "for name in sys.modules)"
            ),
        ],
        cwd=ROOT,
        env=environment,
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode == 0, result.stderr


def test_standard_reader_sources_are_hashed_before_transformation(tmp_path):
    first = tmp_path / "first.csv"
    second = tmp_path / "second.c3d"
    first.write_bytes(b"emg")
    second.write_bytes(b"markers")
    result = source_hashes([str(first), str(second)])
    assert result[str(first)] == hashlib.sha256(b"emg").hexdigest()
    assert result[str(second)] == hashlib.sha256(b"markers").hexdigest()


def test_reader_fails_closed_if_any_input_is_unreadable():
    def reader(path):
        if path == "broken.csv":
            raise ValueError("corrupt input")
        return pd.DataFrame({"time": [0.0], "value": [1.0]})

    with pytest.raises(RuntimeError, match="broken.csv.*corrupt input"):
        _read_files_parallel(["valid.csv", "broken.csv"], reader, max_workers=2)


def test_partial_reader_mode_requires_explicit_opt_in():
    def reader(path):
        if path == "broken.csv":
            raise ValueError("corrupt input")
        return pd.DataFrame({"time": [0.0], "value": [1.0]})

    frames = _read_files_parallel(
        ["valid.csv", "broken.csv"],
        reader,
        max_workers=2,
        allow_partial=True,
    )

    assert len(frames) == 1
    assert frames[0]["source_file_path"].tolist() == ["valid.csv"]
