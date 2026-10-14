import hashlib
import json
from pathlib import Path
from zipfile import ZipFile

from harmo.opensim_quality import run_study31_opensim_quality
from harmo.validation_study import load_registry


ROOT = Path(__file__).resolve().parents[1]


def _make_archives(data_root, registry, absent=(), include_static=False):
    experiment = registry["experiments"]["E"]
    absent = set(absent)
    c3d_path = data_root / "c3d.zip"
    ascii_path = data_root / "ascii.zip"
    mat_path = data_root / "mat.zip"
    with ZipFile(c3d_path, "w") as c3d, ZipFile(
        ascii_path, "w"
    ) as ascii_archive, ZipFile(mat_path, "w") as mat_archive:
        for subject in experiment["subjects"]:
            if include_static:
                c3d.writestr(
                    "c3d files/Participant{}/Static/calibration.c3d".format(
                        subject
                    ),
                    b"static",
                )
            mat_archive.writestr(
                "MAT files/Participant{}/Raw_Data.mat".format(subject), b"mat"
            )
        for subject in experiment["subjects"]:
            for speed in experiment["speeds"]:
                for trial in experiment["trials"]:
                    key = (subject, speed, trial)
                    if key in absent:
                        continue
                    c3d.writestr(
                        "c3d files/Participant{}/Raw_Data/{}/{}.c3d".format(
                            subject, speed, trial
                        ),
                        b"c3d",
                    )
                    for component in ("Trajectories", "GRF", "FPM", "CoP"):
                        ascii_archive.writestr(
                            "ASCII files/Participant{}/Raw_Data/{}/{}/{}.txt".format(
                                subject, speed, component, trial
                            ),
                            b"source",
                        )
    return c3d_path, ascii_path, mat_path


def _spec(path):
    return {
        "local_name": path.name,
        "source_record_doi": "10.123/test",
        "size_bytes": path.stat().st_size,
        "checksum_algorithm": "md5",
        "checksum": hashlib.md5(path.read_bytes()).hexdigest(),
    }


def _runtime(version="4.5.2"):
    return {
        "probe_status": "pass",
        "runtime_version": version,
        "rra_available": True,
        "moco_inverse_available": True,
        "container_image": "test/image:4.5.2",
        "repo_digests": ["test/image@sha256:" + "1" * 64],
    }


def _validation_rows(registry):
    experiment = registry["experiments"]["E"]
    return [
        {
            "subject_id": str(subject),
            "session_id": "ses-{}-{}".format(speed, trial),
            "ik_rms_error": 0.01,
            "ik_max_error": 0.03,
            "residual_force_rms": 2.0,
            "ex_loads_rms": 100.0,
            "residual_moment_rms": 0.5,
            "ex_moment_rms": 100.0,
            "rra_angle_change_rms_deg": 4.0,
        }
        for subject in experiment["subjects"]
        for speed in experiment["speeds"]
        for trial in experiment["trials"]
    ]


def test_opensim_source_audit_retains_release_absence(monkeypatch, tmp_path):
    registry = load_registry(ROOT / "paper/validation_registry.yaml")
    absent = {
        (4, speed, trial)
        for speed in ("V35", "V4")
        for trial in ("T1", "T2", "T3")
    }
    c3d_path, ascii_path, mat_path = _make_archives(
        tmp_path, registry, absent=absent
    )
    specs = {
        "c3d": _spec(c3d_path),
        "ascii": _spec(ascii_path),
        "mat": _spec(mat_path),
    }
    monkeypatch.setattr(
        "harmo.opensim_quality._asset_spec",
        lambda _root, _study, asset: specs[asset],
    )

    ledger, summary = run_study31_opensim_quality(
        ROOT, registry, tmp_path, [], _runtime()
    )

    assert len(ledger) == 336
    assert summary["audit_complete"] is True
    assert summary["pass"] is False
    assert summary["source_denominator"]["source_available"] == 330
    assert summary["source_denominator"]["source_absent"] == 6
    assert summary["static_calibration"]["status"] == "source-absent"
    missing = [row for row in ledger if row["source_status"] == "source-absent"]
    assert {(int(row["subject_id"]), row["speed"], row["trial"]) for row in missing} == absent
    assert all(row["reason_code"] == "source-absent" for row in missing)
    subject4 = next(
        row
        for row in summary["analysis_denominator"]["by_subject"]
        if row["subject_id"] == "4"
    )
    assert subject4["frozen_trials"] == 21
    assert subject4["source_available"] == 15


def test_opensim_quality_only_passes_with_sources_static_runtime_and_all_metrics(
    monkeypatch, tmp_path
):
    registry = load_registry(ROOT / "paper/validation_registry.yaml")
    c3d_path, ascii_path, mat_path = _make_archives(
        tmp_path, registry, include_static=True
    )
    specs = {
        "c3d": _spec(c3d_path),
        "ascii": _spec(ascii_path),
        "mat": _spec(mat_path),
    }
    monkeypatch.setattr(
        "harmo.opensim_quality._asset_spec",
        lambda _root, _study, asset: specs[asset],
    )
    validation = tmp_path / "validation.json"
    validation.write_text(json.dumps(_validation_rows(registry)), encoding="utf-8")

    ledger, summary = run_study31_opensim_quality(
        ROOT, registry, tmp_path, [validation], _runtime()
    )

    assert len(ledger) == 336
    assert summary["analysis_denominator"]["trials_with_validation"] == 336
    assert summary["analysis_denominator"]["outcomes"] == {"pass": 336}
    assert all(summary["checks"].values())
    assert summary["pass"] is True


def test_opensim_quality_rejects_runtime_tag_content_mismatch(
    monkeypatch, tmp_path
):
    registry = load_registry(ROOT / "paper/validation_registry.yaml")
    c3d_path, ascii_path, mat_path = _make_archives(
        tmp_path, registry, include_static=True
    )
    specs = {
        "c3d": _spec(c3d_path),
        "ascii": _spec(ascii_path),
        "mat": _spec(mat_path),
    }
    monkeypatch.setattr(
        "harmo.opensim_quality._asset_spec",
        lambda _root, _study, asset: specs[asset],
    )

    _, summary = run_study31_opensim_quality(
        ROOT, registry, tmp_path, [], _runtime("4.3.1--")
    )

    assert summary["checks"]["runtime_probe_pass"] is True
    assert summary["checks"]["runtime_version_match"] is False
    assert summary["pass"] is False


def test_verified_external_static_bundle_can_satisfy_scaling_source_gate(
    monkeypatch, tmp_path
):
    registry = load_registry(ROOT / "paper/validation_registry.yaml")
    c3d_path, ascii_path, mat_path = _make_archives(tmp_path, registry)
    specs = {
        "c3d": _spec(c3d_path),
        "ascii": _spec(ascii_path),
        "mat": _spec(mat_path),
    }
    monkeypatch.setattr(
        "harmo.opensim_quality._asset_spec",
        lambda _root, _study, asset: specs[asset],
    )
    validation = tmp_path / "validation.json"
    validation.write_text(json.dumps(_validation_rows(registry)), encoding="utf-8")
    source_audit = {
        "pass": True,
        "scope": "study31-subject-specific-scaling-source-intake",
        "expected_subjects": [str(value) for value in registry["experiments"]["E"]["subjects"]],
        "manifest": "intake.yaml",
        "manifest_sha256": "1" * 64,
        "counts": {"accepted": 16},
    }

    _, summary = run_study31_opensim_quality(
        ROOT,
        registry,
        tmp_path,
        [validation],
        _runtime(),
        source_audit,
    )

    assert summary["static_calibration"]["released_complete"] is False
    assert summary["static_calibration"]["eligible_source"] == "verified-external-bundle"
    assert summary["checks"]["subject_specific_scaling_sources_available"] is True
    assert summary["checks"]["external_checkpoint_contract_complete"] is False
    assert summary["pass"] is False


def test_declared_source_absence_stays_in_denominator_without_blocking_330_assessments(
    monkeypatch, tmp_path
):
    registry = load_registry(ROOT / "paper/validation_registry.yaml")
    absent = {
        (4, speed, trial)
        for speed in ("V35", "V4")
        for trial in ("T1", "T2", "T3")
    }
    c3d_path, ascii_path, mat_path = _make_archives(
        tmp_path, registry, absent=absent, include_static=True
    )
    specs = {
        "c3d": _spec(c3d_path),
        "ascii": _spec(ascii_path),
        "mat": _spec(mat_path),
    }
    monkeypatch.setattr(
        "harmo.opensim_quality._asset_spec",
        lambda _root, _study, asset: specs[asset],
    )
    validation = tmp_path / "validation.json"
    validation.write_text(json.dumps(_validation_rows(registry)), encoding="utf-8")

    ledger, summary = run_study31_opensim_quality(
        ROOT, registry, tmp_path, [validation], _runtime()
    )

    assert len(ledger) == 336
    assert summary["source_denominator"]["source_available"] == 330
    assert summary["source_denominator"]["source_absent"] == 6
    assert summary["analysis_denominator"]["outcomes"] == {
        "pass": 330,
        "unassessed": 6,
    }
    assert summary["checks"]["source_denominator_resolved"] is True
    assert summary["pass"] is True
