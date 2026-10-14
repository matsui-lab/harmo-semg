import hashlib
import json
import subprocess
import sys
from pathlib import Path

import pytest
import yaml

from harmo.g6_execution import (
    build_execution_plan,
    enrich_validation,
    rra_angle_change_metrics,
    rra_residual_metrics,
    source_map_from_audit,
    validation_complete,
)
from harmo.opensim_sources import validate_opensim_source_bundle
from harmo.opensim_quality import _external_checkpoint_audit
from harmo.validation_study import load_registry


ROOT = Path(__file__).resolve().parents[1]


def _artifact(root, relative, content, static=False):
    path = root / relative
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(content)
    value = {
        "path": relative,
        "size_bytes": len(content),
        "sha256": hashlib.sha256(content).hexdigest(),
    }
    if static:
        value.update(
            coordinate_unit="mm",
            coordinate_system_id="data_31_rule",
            sampling_rate_hz=200,
        )
    return value


def _audit(tmp_path):
    subjects = []
    for subject in range(1, 17):
        subjects.append(
            {
                "subject_id": subject,
                "source_kind": "static-calibration",
                "provenance": {
                    "static_trial_id": "Participant{}:Static".format(subject),
                    "custodian": "author",
                    "acquisition_basis": "written permission",
                },
                "artifacts": {
                    "static_trial": _artifact(
                        tmp_path,
                        "Participant{}/static.c3d".format(subject),
                        "static-{}".format(subject).encode("ascii"),
                        static=True,
                    )
                },
            }
        )
    manifest = tmp_path / "manifest.yaml"
    manifest.write_text(
        yaml.safe_dump(
            {"schema_version": "1.1.0", "study_id": "study31", "subjects": subjects},
            sort_keys=False,
        ),
        encoding="utf-8",
    )
    return validate_opensim_source_bundle(manifest, tmp_path, range(1, 17))


def _ledger(registry):
    experiment = registry["experiments"]["E"]
    rows = []
    for subject in experiment["subjects"]:
        for speed in experiment["speeds"]:
            for trial in experiment["trials"]:
                absent = int(subject) == 4 and speed in {"V35", "V4"}
                rows.append(
                    {
                        "study_id": "study31",
                        "subject_id": str(subject),
                        "speed": speed,
                        "trial": trial,
                        "session_id": "ses-{}-{}".format(speed, trial),
                        "source_status": "source-absent" if absent else "available",
                    }
                )
    return rows


def _validation():
    return {
        "study_id": "study31",
        "subject_id": "1",
        "session_id": "ses-V1-T1",
        "speed": "V1",
        "trial": "T1",
        "ik_rms_error": 0.01,
        "ik_max_error": 0.02,
        "residual_force_rms": 2.0,
        "ex_loads_rms": 100.0,
        "residual_moment_rms": 0.2,
        "ex_moment_rms": 50.0,
        "rra_angle_change_rms_deg": 2.0,
        "rra_pass_count": 2,
        "rra_kinematics_sha256": "d" * 64,
        "rra_actuation_sha256": "e" * 64,
        "rra_adjusted_model_sha256": "f" * 64,
        "residual_force_source": "final-rra-actuation-force",
        "residual_moment_source": "final-rra-actuation-force",
        "downstream_kinematics_source": "final-rra-kinematics",
        "downstream_model_source": "final-rra-adjusted-model",
        "g6_provenance": {
            "run_id": "run",
            "source_manifest_sha256": "a" * 64,
            "scaling_source_kind": "static-calibration",
            "static_trial_sha256": "b" * 64,
            "scaled_model_sha256": "c" * 64,
            "scaled_model_file": "study31_1_scaled_model.osim",
            "rra_kinematics_sha256": "d" * 64,
            "rra_kinematics_file": "study31_1_ses-V1-T1_RRA_Kinematics_q.sto",
            "rra_actuation_sha256": "e" * 64,
            "rra_actuation_file": "study31_1_ses-V1-T1_RRA_Actuation_force.sto",
            "rra_adjusted_model_sha256": "f" * 64,
            "rra_adjusted_model_file": "study31_1_ses-V1-T1_RRA_AdjustedModel.osim",
        },
    }


def test_plan_retains_336_and_schedules_only_330_available_trials(tmp_path):
    registry = load_registry(ROOT / "paper/validation_registry.yaml")
    plan = build_execution_plan(
        registry, _ledger(registry), _audit(tmp_path), tmp_path / "results", "run"
    )

    assert plan["pass"] is True
    assert plan["counts"]["frozen"] == 336
    assert plan["counts"]["available"] == 330
    assert plan["counts"]["source_absent"] == 6
    assert plan["counts"]["pending"] == 330
    assert sum(row["execution_status"] == "source-absent" for row in plan["trials"]) == 6


def test_opensim_plan_cli_materializes_the_frozen_ledger(tmp_path):
    registry = load_registry(ROOT / "paper/validation_registry.yaml")
    _audit(tmp_path)
    ledger_path = tmp_path / "ledger.json"
    ledger_path.write_text(json.dumps(_ledger(registry)), encoding="utf-8")
    output = tmp_path / "execution-plan.json"

    completed = subprocess.run(
        [
            sys.executable,
            str(ROOT / "scripts/paper/run_validation.py"),
            "--root",
            str(ROOT),
            "opensim-plan",
            "--source-manifest",
            str(tmp_path / "manifest.yaml"),
            "--source-bundle-root",
            str(tmp_path),
            "--source-ledger",
            str(ledger_path),
            "--execution-results",
            str(tmp_path / "results"),
            "--run-id",
            "cli-contract-test",
            "--no-resume",
            "--output",
            str(output),
        ],
        check=False,
        capture_output=True,
        text=True,
    )

    assert completed.returncode == 0, completed.stderr
    plan = json.loads(output.read_text(encoding="utf-8"))
    assert plan["pass"] is True
    assert plan["counts"]["frozen"] == 336
    assert plan["counts"]["pending"] == 330


def test_plan_resume_requires_complete_identity_metrics_and_provenance(tmp_path):
    registry = load_registry(ROOT / "paper/validation_registry.yaml")
    results = tmp_path / "results"
    results.mkdir()
    path = results / "study31_1_ses-V1-T1_validation.json"
    audit = _audit(tmp_path)
    value = _validation()
    value["g6_provenance"]["source_manifest_sha256"] = audit["manifest_sha256"]
    value["g6_provenance"]["static_trial_sha256"] = source_map_from_audit(audit)[
        "1"
    ]["artifacts_by_role"]["static_trial"]["observed_sha256"]
    retained_model = results / "study31_1_scaled_model.osim"
    retained_model.write_bytes(b"retained model")
    value["g6_provenance"]["scaled_model_sha256"] = hashlib.sha256(
        retained_model.read_bytes()
    ).hexdigest()
    artifacts = {
        "rra_kinematics": ("study31_1_ses-V1-T1_RRA_Kinematics_q.sto", b"kinematics"),
        "rra_actuation": ("study31_1_ses-V1-T1_RRA_Actuation_force.sto", b"actuation"),
        "rra_adjusted_model": ("study31_1_ses-V1-T1_RRA_AdjustedModel.osim", b"adjusted"),
    }
    for label, (name, content) in artifacts.items():
        artifact_path = results / name
        artifact_path.write_bytes(content)
        value["g6_provenance"][label + "_sha256"] = hashlib.sha256(
            content
        ).hexdigest()
    path.write_text(json.dumps(value), encoding="utf-8")

    plan = build_execution_plan(
        registry, _ledger(registry), audit, results, "run"
    )
    assert plan["counts"]["resume_complete"] == 1
    assert plan["counts"]["pending"] == 329

    actuation_path = results / artifacts["rra_actuation"][0]
    actuation_path.write_bytes(b"changed actuation")
    corrupted = build_execution_plan(
        registry, _ledger(registry), audit, results, "run"
    )
    assert corrupted["counts"]["resume_complete"] == 0
    assert "rra-actuation-artifact-checksum-mismatch" in corrupted["trials"][0][
        "reason_codes"
    ]
    actuation_path.write_bytes(artifacts["rra_actuation"][1])

    bad = dict(value)
    bad.pop("rra_angle_change_rms_deg")
    path.write_text(json.dumps(bad), encoding="utf-8")
    rerun = build_execution_plan(
        registry, _ledger(registry), audit, results, "run"
    )
    assert rerun["counts"]["resume_complete"] == 0
    assert "rra_angle_change_rms_deg-missing" in rerun["trials"][0]["reason_codes"]

    wrong_source = dict(value)
    wrong_source["g6_provenance"] = dict(value["g6_provenance"])
    wrong_source["g6_provenance"]["static_trial_sha256"] = "f" * 64
    path.write_text(json.dumps(wrong_source), encoding="utf-8")
    stale = build_execution_plan(
        registry, _ledger(registry), audit, results, "run"
    )
    assert stale["counts"]["resume_complete"] == 0
    assert "g6-provenance-static_trial_sha256-mismatch" in stale["trials"][0][
        "reason_codes"
    ]


def test_enrichment_binds_identity_static_and_scaled_model_checksums(tmp_path):
    audit = _audit(tmp_path)
    source = source_map_from_audit(audit)["1"]
    model = tmp_path / "scaled.osim"
    model.write_text("model", encoding="utf-8")
    validation_path = tmp_path / "validation.json"
    raw = _validation()
    raw.pop("g6_provenance")
    checkpoint_artifacts = {
        "rra_kinematics": b"final kinematics",
        "rra_actuation": b"final actuation",
        "rra_adjusted_model": b"final adjusted model",
    }
    for label, content in checkpoint_artifacts.items():
        raw[label + "_sha256"] = hashlib.sha256(content).hexdigest()
    validation_path.write_text(json.dumps(raw), encoding="utf-8")

    value = enrich_validation(
        validation_path,
        raw,
        "run",
        audit,
        source,
        model,
    )
    complete, reasons = validation_complete(value, ("1", "V1", "T1"))
    assert complete is True
    assert reasons == []
    assert value["g6_provenance"]["scaled_model_sha256"] == hashlib.sha256(
        b"model"
    ).hexdigest()
    for label, content in checkpoint_artifacts.items():
        filename = value["g6_provenance"][label + "_file"]
        (tmp_path / filename).write_bytes(content)
    checkpoint_audit = _external_checkpoint_audit(
        [validation_path], audit, [("1", "V1", "T1")]
    )
    assert checkpoint_audit["pass"] is True
    assert checkpoint_audit["complete"] == 1


def _write_motion(path, in_degrees, rows):
    path.write_text(
        "name\nversion=1\ninDegrees={}\nendheader\n".format(
            "yes" if in_degrees else "no"
        )
        + "time\thip_flexion_r\tpelvis_tx\n"
        + "\n".join("\t".join(str(value) for value in row) for row in rows)
        + "\n",
        encoding="utf-8",
    )


def test_rra_angle_change_interpolates_and_excludes_translation(tmp_path):
    ik = tmp_path / "ik.mot"
    rra = tmp_path / "rra.sto"
    _write_motion(ik, True, [(0.0, 0.0, 0.0), (0.5, 10.0, 0.1), (1.0, 20.0, 0.2)])
    _write_motion(rra, True, [(0.0, 2.0, 9.0), (1.0, 22.0, 9.0)])

    metrics = rra_angle_change_metrics(ik, rra)
    assert metrics["rra_angle_change_rms_deg"] == pytest.approx(2.0)
    assert metrics["rra_angle_coordinate_count"] == 1
    assert metrics["rra_angle_value_count"] == 3


def test_rra_angle_change_converts_radians_to_degrees(tmp_path):
    ik = tmp_path / "ik.mot"
    rra = tmp_path / "rra.sto"
    _write_motion(ik, False, [(0.0, 0.0, 0.0), (1.0, 0.0, 0.0)])
    _write_motion(rra, False, [(0.0, 0.1, 0.0), (1.0, 0.1, 0.0)])

    metrics = rra_angle_change_metrics(ik, rra)
    assert metrics["rra_angle_change_rms_deg"] == pytest.approx(5.7295779513)


def test_rra_residual_metrics_use_final_actuation_vectors(tmp_path):
    actuation = tmp_path / "g6_RRA_Actuation_force.sto"
    actuation.write_text(
        "name\nversion=1\nendheader\n"
        "time\tFX\tFY\tFZ\tMX\tMY\tMZ\n"
        "0.0\t3\t4\t0\t0\t0\t2\n"
        "0.1\t0\t0\t0\t0\t0\t0\n",
        encoding="utf-8",
    )

    metrics = rra_residual_metrics(actuation)

    assert metrics["residual_force_rms"] == pytest.approx(5 / 2**0.5)
    assert metrics["residual_moment_rms"] == pytest.approx(2 / 2**0.5)
    assert metrics["residual_force_source"] == "final-rra-actuation-force"
    assert metrics["rra_residual_sample_count"] == 2


def test_complete_g6_validation_rejects_non_rra_residual_source():
    value = _validation()
    value["residual_force_source"] = "moco-reserve"

    complete, reasons = validation_complete(value, ("1", "V1", "T1"))

    assert complete is False
    assert "residual-force-source-invalid" in reasons
