from pathlib import Path

import numpy as np

from harmo.physiology_control import (
    canonical_gait120_channel,
    evaluate_physiology_control,
    load_physiology_protocol,
    preprocess_study38_cycle,
)


ROOT = Path(__file__).resolve().parents[1]
PROTOCOL = ROOT / "paper/physiology_positive_control_protocol.yaml"


def _curve(muscle):
    phase = np.linspace(0.0, 100.0, 101)
    if muscle == "tibialis anterior":
        values = (
            0.1
            + np.exp(-0.5 * ((phase - 8.0) / 6.0) ** 2)
            + 1.2 * np.exp(-0.5 * ((phase - 85.0) / 8.0) ** 2)
        )
    else:
        values = 0.1 + np.exp(-0.5 * ((phase - 42.0) / 9.0) ** 2)
    return (values / values.max()).tolist()


def _rows(protocol):
    rows = []
    for study_id, study in protocol["required_studies"].items():
        asset = (
            "Gait120_011_to_020.zip"
            if study_id == "study_120"
            else None
        )
        for subject_id in study["subjects"]:
            for muscle in protocol["muscles"]:
                rows.append(
                    {
                        "study_id": study_id,
                        "subject_id": str(subject_id),
                        "muscle": muscle,
                        "side": "right",
                        "normalization_method": "timing-only",
                        "representation": "test",
                        "cycle_count": 5,
                        "values": _curve(muscle),
                        "source_asset": asset
                        or "Subject{}.mat".format(subject_id),
                        "source_asset_md5": "1" * 32,
                        "source_asset_sha256": "2" * 64,
                    }
                )
    return rows


def test_frozen_positive_control_passes_complete_prespecified_patterns():
    protocol = load_physiology_protocol(PROTOCOL)
    result = evaluate_physiology_control(
        _rows(protocol),
        protocol,
        [{"study_id": "study_38", "pass": True}, {"study_id": "study_120", "pass": True}],
    )
    assert result["reporting_complete"] is True
    assert result["scientific_endpoint_pass"] is True
    assert result["amplitude_pooled_across_studies"] is False
    assert len(result["metrics"]) == 40
    assert all(row["pattern_pass"] for row in result["metrics"])


def test_positive_control_retains_missing_subject_in_failed_denominator():
    protocol = load_physiology_protocol(PROTOCOL)
    rows = [
        row
        for row in _rows(protocol)
        if not (
            row["study_id"] == "study_38"
            and row["subject_id"] == "10"
        )
    ]
    result = evaluate_physiology_control(
        rows,
        protocol,
        [{"study_id": "study_38", "pass": True}, {"study_id": "study_120", "pass": True}],
    )
    assert result["reporting_complete"] is False
    assert result["scientific_endpoint_pass"] is False
    study38 = next(
        row for row in result["study_eligibility"] if row["study_id"] == "study_38"
    )
    assert study38["expected_subject_count"] == 10
    assert study38["present_subject_count"] == 9


def test_study38_envelope_is_finite_and_time_normalized():
    protocol = load_physiology_protocol(PROTOCOL)
    fs = 1000.0
    time = np.arange(1000) / fs
    carrier = np.sin(2 * np.pi * 100.0 * time)
    modulation = 0.2 + np.exp(-0.5 * ((time - 0.4) / 0.08) ** 2)
    result = preprocess_study38_cycle(
        carrier * modulation,
        fs,
        protocol["study_38_envelope"],
    )
    assert result.shape == (101,)
    assert np.isfinite(result).all()
    assert result.min() >= 0.0
    assert result.max() == 1.0


def test_gait120_channels_use_source_labels_not_positional_order():
    assert canonical_gait120_channel("TibialisAnterior") == "tibialis anterior"
    assert (
        canonical_gait120_channel("GastrocnemuisMedialis")
        == "medial gastrocnemius"
    )
