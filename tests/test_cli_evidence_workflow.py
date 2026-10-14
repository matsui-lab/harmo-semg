import json
import subprocess
import sys

from harmo.evidence.io import write_json_atomic
from test_evidence_contracts import (
    bundle,
    comparison_for_bundle,
    manifest,
    passing_trust,
    release_for_bundle,
)


def _run(*args):
    return subprocess.run(
        [sys.executable, "-m", "harmo.cli", *args],
        check=False,
        capture_output=True,
        text=True,
    )


def test_evidence_cli_end_to_end(tmp_path):
    bundle_path = write_json_atomic(str(tmp_path / "bundle.json"), bundle())
    manifest_path = write_json_atomic(str(tmp_path / "manifest.json"), manifest())
    trust = passing_trust()
    comparison = comparison_for_bundle()
    decision = release_for_bundle(trust, comparison)
    trust_path = write_json_atomic(str(tmp_path / "trust.json"), trust)
    comparison_path = write_json_atomic(
        str(tmp_path / "comparison.json"), comparison
    )
    decision_path = write_json_atomic(
        str(tmp_path / "release.json"), decision
    )

    audited = _run("audit", "--manifest", manifest_path, "--artifact-id", "bundle")
    assert audited.returncode == 0, audited.stderr + audited.stdout
    assert json.loads(audited.stdout)["lineage"][0]["produced_by"] == ["ingest"]

    same = _run("reproduce", "--expected", bundle_path, "--actual", bundle_path)
    assert same.returncode == 0
    assert json.loads(same.stdout)["matches"] is True

    changed = bundle().to_dict()
    changed["study_id"] = "different-study"
    changed_path = write_json_atomic(str(tmp_path / "changed.json"), changed)
    different = _run("reproduce", "--expected", bundle_path, "--actual", changed_path)
    assert different.returncode == 1
    assert json.loads(different.stdout)["matches"] is False

    exchange = tmp_path / "exchange"
    exported = _run(
        "export-atlas",
        "--bundle", bundle_path,
        "--manifest", manifest_path,
        "--trust", trust_path,
        "--comparability", comparison_path,
        "--release-decision", decision_path,
        "--output", str(exchange),
    )
    assert exported.returncode == 0, exported.stderr + exported.stdout
    assert (exchange / "atlas.exchange.json").is_file()


def test_opensim_cli_end_to_end(tmp_path):
    validation = tmp_path / "validation.json"
    validation.write_text(json.dumps({
        "run_id": "run-1",
        "ik_rms_error": 0.01,
        "ik_max_error": 0.03,
        "residual_force_peak": 20.0,
        "ex_loads_peak": 1000.0,
    }), encoding="utf-8")
    model = tmp_path / "model.osim"
    model.write_text("<OpenSimDocument/>", encoding="utf-8")
    artifact = tmp_path / "opensim.artifact.json"
    trust = tmp_path / "opensim.trust.json"
    result = _run(
        "trust-opensim",
        "--validation", str(validation),
        "--artifact-id", "osim-1",
        "--recording-id", "recording-1",
        "--model", str(model),
        "--artifact", str(artifact),
        "--trust", str(trust),
    )
    assert result.returncode == 0, result.stderr + result.stdout
    assert json.loads(artifact.read_text(encoding="utf-8"))["manifest_run_id"] == "run-1"
    statuses = {
        item["rule_id"]: item["status"]
        for item in json.loads(trust.read_text(encoding="utf-8"))["evidence"]
    }
    assert statuses["opensim.ik_rms"] == "pass"
    assert statuses["opensim.residual_moment_peak"] == "unassessed"
