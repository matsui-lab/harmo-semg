import hashlib
from pathlib import Path

import yaml

from harmo.opensim_sources import (
    MODEL_SOURCE,
    STATIC_SOURCE,
    audit_legacy_scaled_models,
    validate_opensim_source_bundle,
)


EXPECTED = list(range(1, 17))


def _artifact(root: Path, relative: str, content: bytes):
    path = root / relative
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(content)
    record = {
        "path": relative,
        "size_bytes": len(content),
        "sha256": hashlib.sha256(content).hexdigest(),
    }
    if Path(relative).suffix.lower() in {".c3d", ".trc"}:
        record.update(
            {
                "coordinate_unit": "mm",
                "coordinate_system_id": "data_31_rule",
                "sampling_rate_hz": 200,
            }
        )
    return record


def _provenance(subject):
    return {
        "static_trial_id": "Participant{}:Static".format(subject),
        "custodian": "dataset corresponding author",
        "acquisition_basis": "direct author transfer with research permission",
    }


def _static_manifest(root: Path):
    subjects = []
    for subject in EXPECTED:
        subjects.append(
            {
                "subject_id": subject,
                "source_kind": STATIC_SOURCE,
                "provenance": _provenance(subject),
                "artifacts": {
                    "static_trial": _artifact(
                        root,
                        "Participant{}/Static/calibration.c3d".format(subject),
                        "static-{}".format(subject).encode("ascii"),
                    )
                },
            }
        )
    return {
        "schema_version": "1.1.0",
        "study_id": "study31",
        "subjects": subjects,
    }


def _write_manifest(path: Path, value):
    path.write_text(yaml.safe_dump(value, sort_keys=False), encoding="utf-8")


def test_static_bundle_accepts_exactly_sixteen_checksum_bound_subjects(tmp_path):
    manifest = _static_manifest(tmp_path)
    manifest_path = tmp_path / "intake.yaml"
    _write_manifest(manifest_path, manifest)

    audit = validate_opensim_source_bundle(manifest_path, tmp_path, EXPECTED)

    assert audit["counts"] == {
        "expected": 16,
        "observed_records": 16,
        "accepted": 16,
        "rejected": 0,
    }
    assert all(audit["checks"].values())
    assert audit["pass"] is True


def test_bundle_rejects_missing_subject_bad_checksum_and_path_escape(tmp_path):
    manifest = _static_manifest(tmp_path)
    manifest["subjects"].pop()
    manifest["subjects"][0]["artifacts"]["static_trial"]["sha256"] = "0" * 64
    manifest["subjects"][1]["artifacts"]["static_trial"]["path"] = "../escape.c3d"
    manifest_path = tmp_path / "intake.yaml"
    _write_manifest(manifest_path, manifest)

    audit = validate_opensim_source_bundle(manifest_path, tmp_path, EXPECTED)

    assert audit["pass"] is False
    assert audit["checks"]["expected_subjects_exact"] is False
    rows = {row["subject_id"]: row for row in audit["subjects"]}
    assert "static_trial-checksum-mismatch" in rows["1"]["reason_codes"]
    assert "static_trial-path-outside-bundle" in rows["2"]["reason_codes"]
    assert "subject-record-missing" in rows["16"]["reason_codes"]


def test_bundle_rejects_static_trial_reuse_across_participants(tmp_path):
    manifest = _static_manifest(tmp_path)
    manifest["subjects"][1]["artifacts"]["static_trial"] = manifest["subjects"][0][
        "artifacts"
    ]["static_trial"]
    manifest_path = tmp_path / "intake.yaml"
    _write_manifest(manifest_path, manifest)

    audit = validate_opensim_source_bundle(manifest_path, tmp_path, EXPECTED)

    assert audit["pass"] is False
    for index in (0, 1):
        assert "static-trial-path-reused" in audit["subjects"][index]["reason_codes"]
        assert (
            "static-trial-checksum-reused" in audit["subjects"][index]["reason_codes"]
        )


def test_bundle_rejects_missing_static_coordinate_and_timebase_metadata(tmp_path):
    manifest = _static_manifest(tmp_path)
    artifact = manifest["subjects"][0]["artifacts"]["static_trial"]
    artifact.pop("coordinate_unit")
    artifact["coordinate_system_id"] = "unknown"
    artifact["sampling_rate_hz"] = 0
    manifest_path = tmp_path / "intake.yaml"
    _write_manifest(manifest_path, manifest)

    audit = validate_opensim_source_bundle(manifest_path, tmp_path, EXPECTED)

    reasons = audit["subjects"][0]["reason_codes"]
    assert "static_trial-coordinate-unit-invalid" in reasons
    assert "static_trial-coordinate-system-invalid" in reasons
    assert "static_trial-sampling-rate-invalid" in reasons
    assert audit["pass"] is False


def test_traceable_model_requires_static_and_complete_scaling_lineage(tmp_path):
    roles = {
        "static_trial": ("static.c3d", b"static"),
        "generic_model": ("generic.osim", b"generic"),
        "scale_setup": ("setup.xml", b"setup"),
        "scale_result": ("result.xml", b"result"),
        "marker_set": ("markers.xml", b"markers"),
        "scaled_model": ("scaled.osim", b"scaled"),
    }
    subjects = _static_manifest(tmp_path)["subjects"]
    subject = subjects[0]
    subject["source_kind"] = MODEL_SOURCE
    subject["artifacts"] = {
        role: _artifact(tmp_path, "model/{}".format(filename), content)
        for role, (filename, content) in roles.items()
    }
    subject["generation"] = {
        "software": "OpenSim",
        "software_version": "4.5.2",
        "generated_by": "dataset author",
        "generated_at": "2026-07-21T00:00:00Z",
        "source_static_trial_id": subject["provenance"]["static_trial_id"],
    }
    manifest = {
        "schema_version": "1.1.0",
        "study_id": "study31",
        "subjects": subjects,
    }
    manifest_path = tmp_path / "intake.yaml"
    _write_manifest(manifest_path, manifest)

    accepted = validate_opensim_source_bundle(manifest_path, tmp_path, EXPECTED)
    assert accepted["pass"] is True

    manifest["subjects"][0]["artifacts"].pop("scale_result")
    _write_manifest(manifest_path, manifest)
    rejected = validate_opensim_source_bundle(manifest_path, tmp_path, EXPECTED)
    assert rejected["pass"] is False
    assert "scale_result-record-missing" in rejected["subjects"][0]["reason_codes"]


def test_legacy_models_are_inventory_only_not_confirmatory_evidence(tmp_path):
    model_dir = tmp_path / "model"
    model_dir.mkdir()
    (model_dir / "31_1_scaled.osim").write_text("one", encoding="utf-8")
    (model_dir / "31_1_second_scaled.osim").write_text("two", encoding="utf-8")
    (model_dir / "31_2_scaled.osim").write_text("three", encoding="utf-8")

    audit = audit_legacy_scaled_models(model_dir, EXPECTED)

    assert audit["represented_subjects"] == ["1", "2"]
    assert audit["missing_subjects"] == [str(value) for value in range(3, 17)]
    assert audit["ambiguous_subjects"] == ["1"]
    assert audit["pass"] is False
