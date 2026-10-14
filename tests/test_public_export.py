import json
import sqlite3
from dataclasses import replace

import numpy as np
import pytest

from harmo.public_batch import catalog, sha
from harmo.public_export import export_catalog
from harmo.evidence.models import TrustStatus, SignalProcessingState, ProcessingOperation
from test_evidence_contracts import bundle, passing_trust
from test_evidence_policy import _semantic_bundle


def setup_catalog(tmp_path, trusted=True):
    db = sqlite3.connect(tmp_path / "catalog.sqlite")
    catalog(db)
    for index in (1,2):
        directory = tmp_path / str(index)
        directory.mkdir()
        b = bundle()
        b.recording_id = str(index)
        signal = directory / "normalized.npz"
        np.savez(signal, time=np.arange(100)/1000, values=np.ones((100,2))*index)
        b.samples = replace(b.samples, uri=str(signal), format="npz", checksum=sha(signal))
        # Synthetic prevalidated amplitude contract: tests the export gate,
        # not an empirical validation of normalization.
        b.schema_version = "1.1.0"
        b.processing_state = SignalProcessingState("normalized-envelope", [ProcessingOperation("test-only", {})],
            "test-common-scale", "cross-recording-validated", "test-group",
            ["urn:test:sha256:" + "0"*64], "0"*64)
        b = _semantic_bundle(b, task_source="synthetic test walking")
        t = passing_trust(b.recording_id)
        if not trusted:
            for evidence in t.evidence:
                evidence.status = TrustStatus.UNASSESSED
        (directory/"recording.bundle.json").write_text(b.to_json())
        (directory/"trust.report.json").write_text(t.to_json())
        db.execute("INSERT INTO records VALUES(?,?,?,?,?,?)",(str(index),"test/1.0.0",str(index),"converted",str(directory),"{}"))
    db.commit()
    db.close()
    return dict(reference=dict(dataset="test/1.0.0",name="1",channel="TA"), purposes=["activation-timing","amplitude-pooling"])


def test_approved_and_incompatible_channels(tmp_path):
    request = setup_catalog(tmp_path)
    result = export_catalog(tmp_path/"catalog.sqlite",request,tmp_path/"out")
    assert result["exported"] > 0
    assert all(e["channel"] == "ch-1" for e in result["entries"] if e["status"] == "exported")
    assert any(e["status"] == "held" for e in result["entries"])


def test_tampered_samples_never_export(tmp_path):
    request = setup_catalog(tmp_path)
    (tmp_path/"2"/"normalized.npz").write_bytes(b"changed")
    result = export_catalog(tmp_path/"catalog.sqlite", request,tmp_path/"out")
    assert result["exported"] == 0
    assert not list((tmp_path/"out").glob("*.npz"))


def test_reference_ambiguity_and_no_overwrite(tmp_path):
    request = setup_catalog(tmp_path)
    request["reference"]["name"] = "absent"
    with pytest.raises(ValueError):
        export_catalog(tmp_path/"catalog.sqlite",request,tmp_path/"out")
    request["reference"]["name"] = "1"
    (tmp_path/"out").mkdir()
    with pytest.raises(FileExistsError):
        export_catalog(tmp_path/"catalog.sqlite",request,tmp_path/"out")


def test_unassessed_never_export(tmp_path):
    request = setup_catalog(tmp_path, trusted=False)
    assert export_catalog(tmp_path/"catalog.sqlite",request,tmp_path/"out")["exported"] == 0


def test_source_conflict_blocks_even_passing_contract(tmp_path):
    request = setup_catalog(tmp_path)
    (tmp_path/"2"/"source.screen.json").write_text(json.dumps({"issues":[{"code":"possible_source_unit_conflict"}]}))
    result = export_catalog(tmp_path/"catalog.sqlite",request,tmp_path/"out")
    assert result["exported"] == 0


def test_timestamp_contract_checked(tmp_path):
    request = setup_catalog(tmp_path)
    signal = tmp_path/"2"/"normalized.npz"
    np.savez(signal,time=np.arange(100)/2000,values=np.ones((100,2)))
    contract = tmp_path/"2"/"recording.bundle.json"
    data = json.loads(contract.read_text())
    data["samples"]["checksum"] = sha(signal)
    contract.write_text(json.dumps(data))
    assert export_catalog(tmp_path/"catalog.sqlite",request,tmp_path/"out")["exported"] == 0


def test_single_command_orchestration(tmp_path, monkeypatch):
    from harmo import cli
    from harmo import public_batch
    request = setup_catalog(tmp_path)
    request_file = tmp_path/"request.json"
    request_file.write_text(json.dumps(request))
    urls_file = tmp_path/"urls.json"
    urls_file.write_text('["https://physionet.org/content/test/1.0.0/"]')
    monkeypatch.setattr(public_batch, "run_batch", lambda *args: {"run_id":"fixture-run", "datasets":[{"status":"completed"}]})
    with pytest.raises(SystemExit) as exit_info:
        cli.main(["public-batch", "--urls", str(urls_file), "--output",str(tmp_path),
                  "--selection-request",str(request_file)])
    assert exit_info.value.code == 0
    report = json.loads((tmp_path/"runs/fixture-run/selection/selection.report.json").read_text())
    assert report["exported"] == 1


@pytest.mark.parametrize("invalid_request",[{}, [], {"reference":[],"purposes":[]},
    {"reference":{"dataset":"x","name":"y","channel":"z"},"purposes":[{}]}])
def test_invalid_requests(tmp_path, invalid_request):
    with pytest.raises(ValueError):
        export_catalog(tmp_path/"missing.sqlite",invalid_request,tmp_path/"out")
