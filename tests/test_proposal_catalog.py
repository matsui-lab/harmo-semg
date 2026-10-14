import json
import sqlite3
import numpy as np
from harmo.config.automap import digest, save
from harmo.proposal_catalog import ingest, compare


def test_store_idempotent_tamper_and_policy(tmp_path):
    root, proposals, store = [tmp_path / n for n in ("input", "proposals", "store")]
    root.mkdir(); proposals.mkdir()
    (root / "a.mat").write_bytes(b"source")
    entries = []
    for i in range(2):
        dest = proposals / "file-0000" / ("record-%04d" % i)
        dest.mkdir(parents=True)
        values = np.arange(3, dtype=float).reshape(3,1)+i
        time = np.arange(3, dtype=float)/1000
        np.savez(dest / "converted.npz", values=values,time=time)
        entries.append(dict(status="accepted", data_key="s.Data.%d.EMG" % i, rows=3, channels=["EMG"],
            sampling_rate_hz=1000, values_sha256=digest(values.tobytes()), time_sha256=digest(time.tobytes()),
            options={"static_metadata":{"time_unit":"s"}}))
    save(proposals / "report.json", dict(config_errors=[], files=[dict(source="a.mat",status="accepted",source_sha256=digest(b"source"),records=entries)]))
    assert ingest(proposals,root,store,"test-release")["inserted"] == 2
    assert ingest(proposals,root,store,"test-release")["reused"] == 2
    db = sqlite3.connect(store / "catalog.sqlite")
    ids = [r[0] for r in db.execute("SELECT id FROM records ORDER BY id")]
    assert db.execute("SELECT count(*) FROM assessments WHERE status='unassessed'").fetchone()[0] == 4
    result = compare(store,*ids,"EMG")
    assert set(result) == {"activation-timing","amplitude-pooling"}
    assert all(r["assertion"]["status"] != "comparable" for r in result.values())
    (root / "a.mat").write_bytes(b"changed")
    result = ingest(proposals,root,store,"test-release")
    assert result["inserted"] == 0 and result["held"]
    db.close()
