import json

import pandas as pd
import pytest

from harmo.evidence.adapters import bundle_from_wide_dataframe
from harmo.evidence.opensim import opensim_evidence_from_validation
from harmo.evidence.models import TrustStatus
from harmo.evidence.validation import RecordingProfile, assess_bundle


def test_wide_dataframe_preserves_time_channels_units_and_hashes(tmp_path):
    source = tmp_path / "raw.csv"
    source.write_text("time,TA,GM\n0,1,2\n0.001,3,4\n", encoding="utf-8")
    normalized = tmp_path / "normalized.parquet"
    normalized.write_bytes(b"normalized")
    frame = pd.DataFrame({"time": [0.0, 0.001], "TA": [1.0, 3.0], "GM": [2.0, 4.0]})

    bundle, warnings = bundle_from_wide_dataframe(
        frame,
        recording_id="study-1/sub-1/ses-1/emg",
        study_id="study-1",
        subject_id="sub-1",
        session_id="ses-1",
        source_path=str(source),
        array_uri=normalized.as_uri(),
        channel_units={"TA": "V", "GM": "V"},
        channel_metadata={"TA": {"muscle": "tibialis-anterior", "side": "left"}},
    )

    assert bundle.timebase.sampling_rate_hz == pytest.approx(1000.0)
    assert bundle.samples.shape == [2, 2]
    assert bundle.samples.checksum
    assert bundle.sources[0].sha256
    assert bundle.channels[0].muscle == "tibialis-anterior"
    assert warnings == []


def test_file_uri_with_escaped_path_retains_array_checksum(tmp_path):
    source = tmp_path / "raw source.csv"
    source.write_text("raw", encoding="utf-8")
    normalized = tmp_path / "normalized array.parquet"
    normalized.write_bytes(b"normalized")
    frame = pd.DataFrame({"time": [0.0, 0.001], 1: [1.0, 2.0]})

    bundle, _ = bundle_from_wide_dataframe(
        frame,
        recording_id="rec",
        study_id="study",
        subject_id="sub",
        session_id="ses",
        source_path=str(source),
        array_uri=normalized.as_uri(),
        channel_units={"1": "V"},
    )

    assert bundle.samples.checksum
    assert bundle.channels[0].name == "1"


def test_wide_dataframe_rejects_silent_channel_unit_mismatch(tmp_path):
    source = tmp_path / "raw.csv"
    source.write_text("raw", encoding="utf-8")
    frame = pd.DataFrame({"time": [0.0, 0.001], "TA": [1.0, 2.0], "GM": [3.0, 4.0]})
    with pytest.raises(ValueError, match="channel/unit mismatch"):
        bundle_from_wide_dataframe(
            frame,
            recording_id="rec",
            study_id="study",
            subject_id="sub",
            session_id="ses",
            source_path=str(source),
            array_uri="s3://atlas/array.parquet",
            channel_units={"TA": "V"},
        )


def test_irregular_timebase_is_explicit_not_silently_regularized(tmp_path):
    source = tmp_path / "raw.csv"
    source.write_text("raw", encoding="utf-8")
    frame = pd.DataFrame({"time": [0.0, 0.001, 0.0022], "TA": [1.0, 2.0, 3.0]})
    bundle, warnings = bundle_from_wide_dataframe(
        frame,
        recording_id="rec",
        study_id="study",
        subject_id="sub",
        session_id="ses",
        source_path=str(source),
        array_uri="s3://atlas/array.parquet",
        channel_units={"TA": "V"},
    )
    assert warnings[0].code == "irregular-timebase"
    assert bundle.timebase.timestamps_uri == "s3://atlas/array.parquet#time"
    report = assess_bundle(bundle, RecordingProfile(require_array_checksum=False))
    assert report.dimension_status("semantic_fidelity") == TrustStatus.WARN


def test_missing_samples_are_scientific_warning_and_non_numeric_is_rejected(tmp_path):
    source = tmp_path / "raw.csv"
    source.write_text("raw", encoding="utf-8")
    missing = pd.DataFrame({"time": [0.0, 0.001], "TA": [1.0, None]})
    bundle, warnings = bundle_from_wide_dataframe(
        missing,
        recording_id="rec",
        study_id="study",
        subject_id="sub",
        session_id="ses",
        source_path=str(source),
        array_uri="s3://atlas/array.parquet",
        channel_units={"TA": "V"},
    )
    assert warnings[0].dimension == "scientific_validity"
    report = assess_bundle(bundle, RecordingProfile(require_array_checksum=False))
    assert report.dimension_status("scientific_validity") == TrustStatus.WARN

    invalid = pd.DataFrame({"time": [0.0, 0.001], "TA": [1.0, "bad"]})
    with pytest.raises(ValueError, match="non-numeric"):
        bundle_from_wide_dataframe(
            invalid,
            recording_id="rec",
            study_id="study",
            subject_id="sub",
            session_id="ses",
            source_path=str(source),
            array_uri="s3://atlas/array.parquet",
            channel_units={"TA": "V"},
        )


def test_opensim_validation_ratios_and_unassessed_are_preserved(tmp_path):
    validation = tmp_path / "validation.json"
    validation.write_text(
        json.dumps(
            {
                "run_id": "run-7",
                "ik_rms_error": 0.01,
                "ik_max_error": 0.03,
                "residual_force_peak": 40.0,
                "ex_loads_peak": 1000.0,
                "residual_force_rms": 20.0,
                "ex_loads_rms": 500.0,
            }
        ),
        encoding="utf-8",
    )
    model = tmp_path / "model.osim"
    model.write_text("<OpenSimDocument/>", encoding="utf-8")

    artifact, report = opensim_evidence_from_validation(
        str(validation), "osim-7", "rec-7", str(model)
    )
    evidence = {item.rule_id: item.status for item in report.evidence}
    assert artifact.residual_force_fraction_peak == pytest.approx(0.04)
    assert evidence["opensim.residual_force_peak"] == TrustStatus.PASS
    assert evidence["opensim.residual_moment_peak"] == TrustStatus.UNASSESSED
    assert artifact.manifest_run_id == "run-7"
    assert artifact.outputs[0].artifact_id == "opensim-validation"
    assert artifact.outputs[0].sha256
    assert report.dimension_status("traceability") == TrustStatus.PASS
    assert report.dimension_status("computational_reproducibility") == TrustStatus.PASS


def test_opensim_adapter_rejects_missing_model(tmp_path):
    validation = tmp_path / "validation.json"
    validation.write_text("{}", encoding="utf-8")
    with pytest.raises(ValueError, match="does not exist"):
        opensim_evidence_from_validation(
            str(validation), "osim-7", "rec-7", str(tmp_path / "missing.osim")
        )
