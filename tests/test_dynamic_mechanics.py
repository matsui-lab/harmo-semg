from __future__ import annotations

import hashlib
import json
import runpy
from pathlib import Path

import numpy as np
import pandas as pd
import pytest
import yaml

from harmo.dynamic_mechanics import (
    build_dynamic_mechanics_plan,
    evaluate_dynamic_mechanics,
    waveform_comparison,
)


ROOT = Path(__file__).resolve().parents[1]


def _write_trc(path: Path):
    times = [20.34, 20.355, 20.5, 21.015, 21.02]
    rows = [
        "{}\t{}\t0\t0\t0\t1\t0\t0".format(index, time)
        for index, time in enumerate(times)
    ]
    path.write_text(
        "PathFileType\t4\t(X/Y/Z)\t{}\n".format(path.name)
        + "DataRate\tCameraRate\tNumFrames\tNumMarkers\tUnits\tOrigDataRate\tOrigDataStartFrame\tOrigNumFrames\n"
        + "200\t200\t5\t2\tm\t200\t0\t4\n"
        + "Frame#\tTimes\t\tA\t\t\tB\t\t\n"
        + "\t\tX1\tY1\tZ1\tX2\tY2\tZ2\n\n"
        + "\n".join(rows)
        + "\n",
        encoding="utf-8",
    )


def _asset(path: Path, role: str):
    content = path.read_bytes()
    return {
        "role": role,
        "local_name": path.name,
        "download_url": "https://example.org/{}".format(path.name),
        "size_bytes": len(content),
        "sha256": hashlib.sha256(content).hexdigest(),
    }


def _fixture(tmp_path):
    marker = tmp_path / "heldout.trc"
    grf = tmp_path / "heldout.mot"
    events = tmp_path / "events.xlsx"
    static_model = tmp_path / "static.osim"
    dynamic_model = tmp_path / "dynamic.osim"
    ik_setup = tmp_path / "ik.xml"
    id_setup = tmp_path / "id.xml"
    _write_trc(marker)
    grf.write_text("synthetic GRF", encoding="utf-8")
    static_model.write_text("static model", encoding="utf-8")
    dynamic_model.write_text("dynamic model", encoding="utf-8")
    ik_setup.write_text(
        "<OpenSimDocument><InverseKinematicsTool><IKTaskSet><objects>"
        '<IKMarkerTask name="A"><apply>true</apply><weight>1</weight>'
        "</IKMarkerTask></objects></IKTaskSet></InverseKinematicsTool>"
        "</OpenSimDocument>",
        encoding="utf-8",
    )
    id_setup.write_text("ID setup", encoding="utf-8")
    event_rows = [
        ["IC R", 3.067, 20.355, 21.015],
        ["TO R", 19.936, 20.622, np.nan],
        ["IC L", 20.012, 20.681, np.nan],
        ["TO L", 20.314, 20.963, np.nan],
    ]
    pd.DataFrame(event_rows).to_excel(
        events, sheet_name="sheet1", header=False, index=False
    )
    manifest = tmp_path / "manifest.yaml"
    manifest.write_text(
        yaml.safe_dump(
            {
                "schema_version": "1.0.0",
                "source_record_doi": "10.example/test",
                "assets": [
                    _asset(marker, "markers"),
                    _asset(grf, "grf"),
                    _asset(events, "events"),
                    _asset(static_model, "static_model"),
                    _asset(ik_setup, "ik_setup"),
                    _asset(id_setup, "id_setup"),
                ],
            },
            sort_keys=False,
        ),
        encoding="utf-8",
    )
    dynamic_hash = hashlib.sha256(dynamic_model.read_bytes()).hexdigest()
    g6b2 = tmp_path / "g6b2.json"
    g6b2.write_text(
        json.dumps(
            {
                "pass": True,
                "generated_model_sha256": dynamic_hash,
                "hidden_reference_model_sha256": hashlib.sha256(
                    static_model.read_bytes()
                ).hexdigest(),
                "target_subject_id": "02",
            }
        ),
        encoding="utf-8",
    )
    protocol = tmp_path / "protocol.yaml"
    protocol.write_text(
        yaml.safe_dump(
            {
                "schema_version": "1.0.0",
                "claim_boundary": "synthetic test only",
                "preconditions": {
                    "dynamic_model_sha256": dynamic_hash,
                    "target_subject_id": "02",
                },
                "models": {"static_derived": {"role": "static_model"}},
                "heldout_trial": {
                    "marker_role": "markers",
                    "ground_reaction_role": "grf",
                    "event_role": "events",
                    "event_sheet": "sheet1",
                    "start_time_s": 20.355,
                    "end_time_s": 21.015,
                },
                "opensim": {
                    "required_version": "4.5.2",
                    "active_marker_task_count": 1,
                    "ik_setup_role": "ik_setup",
                    "id_setup_role": "id_setup",
                },
                "primary_coordinates": ["knee_angle_r"],
                "thresholds": {},
            },
            sort_keys=False,
        ),
        encoding="utf-8",
    )
    return protocol, manifest, dynamic_model, g6b2


def test_dynamic_mechanics_plan_binds_heldout_stride_and_g6b2_model(tmp_path):
    protocol, manifest, dynamic_model, g6b2 = _fixture(tmp_path)

    plan = build_dynamic_mechanics_plan(
        protocol, manifest, tmp_path, dynamic_model, g6b2
    )

    assert plan["target_subject_id"] == "02"
    assert plan["start_time_s"] == pytest.approx(20.355)
    assert plan["end_time_s"] == pytest.approx(21.015)
    assert plan["event_sequence"]["right_toe_off_s"] == pytest.approx(20.622)
    assert plan["models"]["dynamic_derived"]["static_used_in_synthesis"] is False
    assert plan["input_sha256"]["dynamic_derived_model"] == hashlib.sha256(
        dynamic_model.read_bytes()
    ).hexdigest()


def test_dynamic_mechanics_plan_rejects_changed_dynamic_model(tmp_path):
    protocol, manifest, dynamic_model, g6b2 = _fixture(tmp_path)
    dynamic_model.write_text("changed after G6b-2", encoding="utf-8")

    with pytest.raises(ValueError, match="checksum mismatch"):
        build_dynamic_mechanics_plan(
            protocol, manifest, tmp_path, dynamic_model, g6b2
        )


def test_waveform_comparison_reports_rmse_nrmse_and_correlation():
    result = waveform_comparison([1.0, 2.0, 3.0], [1.0, 2.0, 4.0])

    assert result["rmse"] == pytest.approx(1 / np.sqrt(3))
    assert result["nrmse"] == pytest.approx(
        result["rmse"] / np.sqrt(np.mean(np.square([1.0, 2.0, 3.0])))
    )
    assert 0.9 < result["correlation"] < 1.0


def test_dynamic_mechanics_retains_worker_failure_as_negative_result(tmp_path):
    protocol, manifest, dynamic_model, g6b2 = _fixture(tmp_path)
    plan = build_dynamic_mechanics_plan(
        protocol, manifest, tmp_path, dynamic_model, g6b2
    )
    worker = {
        "opensim_version": "4.5.2-test",
        "input_sha256": plan["input_sha256"],
        "models": {
            "dynamic_derived": {
                "success": False,
                "failed_stage": "rra-pass-1",
                "error_type": "RuntimeError",
                "error_message": "synthetic failure",
            },
            "static_derived": {"success": True},
        },
    }

    result = evaluate_dynamic_mechanics(protocol, plan, worker, tmp_path)

    assert result["pass"] is False
    assert result["checks"]["runtime_identity"] is True
    assert result["checks"]["worker_input_hashes"] is True
    assert result["checks"]["both_model_pipelines_complete"] is False


def test_opensim_worker_normalizes_only_the_nonstandard_time_header(tmp_path):
    namespace = runpy.run_path(
        str(ROOT / "scripts/paper/run_dynamic_mechanics_worker.py")
    )
    normalize = namespace["normalize_trc_time_header"]
    source = tmp_path / "source.trc"
    output = tmp_path / "normalized.trc"
    source.write_bytes(b"metadata\r\nFrame#\tTimes\t\tA\r\n1\t0.0\t1\r\n")

    result = normalize(source, output)

    assert output.read_bytes() == b"metadata\r\nFrame#\tTime\t\tA\r\n1\t0.0\t1\r\n"
    assert result["replacement_count"] == 1
    assert result["source_size_bytes"] == result["normalized_size_bytes"] + 1
    assert result["source_sha256"] == hashlib.sha256(source.read_bytes()).hexdigest()


def test_opensim_worker_rejects_ambiguous_time_header_normalization(tmp_path):
    namespace = runpy.run_path(
        str(ROOT / "scripts/paper/run_dynamic_mechanics_worker.py")
    )
    normalize = namespace["normalize_trc_time_header"]
    source = tmp_path / "source.trc"
    source.write_bytes(b"Frame#\tTimes\tFrame#\tTimes\t")

    with pytest.raises(ValueError, match="exactly one"):
        normalize(source, tmp_path / "normalized.trc")


def test_opensim_worker_runs_rra_from_serialized_setup(tmp_path):
    namespace = runpy.run_path(
        str(ROOT / "scripts/paper/run_dynamic_mechanics_worker.py")
    )
    run_rra_pass = namespace["_run_rra_pass"]

    class FakeRRATool:
        pending = None

        def __init__(self, setup=None):
            self.setup = setup
            self.properties = (
                dict(FakeRRATool.pending.properties) if setup is not None else {}
            )

        def __getattr__(self, name):
            if not name.startswith("set"):
                raise AttributeError(name)

            def setter(value):
                self.properties[name] = value

            return setter

        def printToXML(self, path):
            Path(path).write_text("serialized RRA setup", encoding="utf-8")
            FakeRRATool.pending = self

        def run(self):
            assert self.setup is not None, "the in-memory RRATool must not execute"
            adjusted = Path(self.properties["setOutputModelFileName"])
            results = Path(self.properties["setResultsDir"])
            adjusted.write_text("adjusted", encoding="utf-8")
            results.mkdir(parents=True, exist_ok=True)
            (results / "test_Kinematics_q.sto").write_text("q", encoding="utf-8")
            (results / "test_Actuation_force.sto").write_text(
                "actuation", encoding="utf-8"
            )
            return True

    class FakeOpenSim:
        RRATool = FakeRRATool
        ArrayStr = list

    config = {
        "lowpass_cutoff_hz": 15.0,
        "adjust_com_to_reduce_residuals": True,
        "adjusted_com_body": "torso",
    }
    model, kinematics, actuation = run_rra_pass(
        FakeOpenSim,
        tmp_path / "model.osim",
        tmp_path / "ik.mot",
        tmp_path / "loads.xml",
        tmp_path / "actuators.xml",
        tmp_path / "tasks.xml",
        config,
        20.355,
        21.015,
        tmp_path,
        1,
    )

    assert model.name == "rra_adjusted_pass1.osim"
    assert kinematics.name == "test_Kinematics_q.sto"
    assert actuation.name == "test_Actuation_force.sto"
    assert FakeRRATool.pending.setup is None


def test_retained_g6b3_result_preserves_the_frozen_negative_outcome():
    result_path = (
        ROOT / "paper/results/validation/g6b_dynamic_mechanics_pilot.json"
    )
    result = json.loads(result_path.read_text(encoding="utf-8"))

    assert result["pass"] is False
    assert result["environment"]["code_dirty"] == "false"
    assert result["worker"]["opensim_version"].startswith("4.5.2")
    assert result["checks"]["both_model_pipelines_complete"] is True
    assert result["checks"]["dynamic_derived_ik_rms"] is False
    assert result["checks"]["static_derived_ik_rms"] is True
    assert result["checks"]["minimum_joint_moment_correlation"] is True
    assert sum(result["checks"].values()) == 11
    assert len(result["checks"]) == 20


def test_registered_mechanics_result_preserves_partial_but_insufficient_gain():
    result_path = (
        ROOT / "paper/results/validation/g6b_registered_mechanics_exploratory.json"
    )
    result = json.loads(result_path.read_text(encoding="utf-8"))

    assert result["scope"] == "g6b-registered-heldout-mechanics-exploratory"
    assert result["pass"] is False
    assert result["environment"]["code_dirty"] == "false"
    assert result["checks"]["dynamic_derived_ik_rms"] is True
    assert result["checks"]["dynamic_ik_rms_degradation"] is True
    assert result["checks"]["dynamic_derived_ik_max"] is False
    assert result["checks"]["minimum_joint_moment_correlation"] is False
    assert sum(result["checks"].values()) == 12
    assert len(result["checks"]) == 20
