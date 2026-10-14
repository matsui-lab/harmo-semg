from __future__ import annotations

import hashlib
import json
from pathlib import Path

import pytest
import yaml

from harmo.dynamic_calibration import validate_public_source_bundle
from harmo.dynamic_model import build_dynamic_model_plan, evaluate_dynamic_model


def _write_trc(path: Path, lengths):
    rows = [
        "{}\t{:.2f}\t0\t0\t0\t{}\t0\t0".format(
            index, index / 100.0, length
        )
        for index, length in enumerate(lengths)
    ]
    path.write_text(
        "PathFileType\t4\t(X/Y/Z)\t{}\n".format(path.name)
        + "DataRate\tCameraRate\tNumFrames\tNumMarkers\tUnits\tOrigDataRate\tOrigDataStartFrame\tOrigNumFrames\n"
        + "100\t100\t{}\t2\tm\t100\t0\t{}\n".format(len(rows), len(rows) - 1)
        + "Frame#\tTimes\t\tA\t\t\tB\t\t\n"
        + "\t\tX1\tY1\tZ1\tX2\tY2\tZ2\n\n"
        + "\n".join(rows)
        + "\n",
        encoding="utf-8",
    )


def _write_model(path: Path, marker_distance: float, mass: float = 10.0):
    path.write_text(
        """<?xml version="1.0" encoding="UTF-8" ?>
<OpenSimDocument Version="40500">
  <Model name="synthetic">
    <BodySet><objects><Body name="segment"><mass>{mass}</mass></Body></objects></BodySet>
    <MarkerSet><objects>
      <Marker name="A"><socket_parent_frame>/bodyset/segment</socket_parent_frame><location>0 0 0</location></Marker>
      <Marker name="B"><socket_parent_frame>/bodyset/segment</socket_parent_frame><location>{distance} 0 0</location></Marker>
    </objects></MarkerSet>
  </Model>
</OpenSimDocument>
""".format(mass=mass, distance=marker_distance),
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
    template = tmp_path / "template.osim"
    reference = tmp_path / "reference.osim"
    metadata = tmp_path / "metadata.xlsx"
    dynamic_a = tmp_path / "dynamic-a.trc"
    dynamic_b = tmp_path / "dynamic-b.trc"
    heldout = tmp_path / "heldout.trc"
    _write_model(template, 1.0)
    _write_model(reference, 1.08, mass=12.0)
    metadata.write_bytes(b"checksum-bound synthetic anthropometry")
    _write_trc(dynamic_a, [1.09, 1.10, 1.11])
    _write_trc(dynamic_b, [1.10, 1.11, 1.12])
    _write_trc(heldout, [1.10, 1.10, 1.10])
    manifest = tmp_path / "manifest.yaml"
    manifest.write_text(
        yaml.safe_dump(
            {
                "schema_version": "1.0.0",
                "source_record_doi": "10.example/test",
                "license": "CC0",
                "assets": [
                    _asset(template, "template"),
                    _asset(metadata, "anthropometry"),
                    _asset(dynamic_a, "dynamic_a"),
                    _asset(dynamic_b, "dynamic_b"),
                    _asset(heldout, "heldout"),
                    _asset(reference, "hidden_reference"),
                ],
            },
            sort_keys=False,
        ),
        encoding="utf-8",
    )
    protocol = tmp_path / "protocol.yaml"
    protocol.write_text(
        yaml.safe_dump(
            {
                "schema_version": "1.0.0",
                "design": {
                    "template_subject_id": "subject-a",
                    "target_subject_id": "subject-b",
                    "template_model_role": "template",
                    "anthropometry_source_role": "anthropometry",
                    "dynamic_calibration_roles": ["dynamic_a", "dynamic_b"],
                    "hidden_reference_model_role": "hidden_reference",
                    "heldout_dynamic_role": "heldout",
                    "estimation_input_roles": [
                        "template",
                        "anthropometry",
                        "dynamic_a",
                        "dynamic_b",
                    ],
                    "target_mass_kg": 12.0,
                    "target_height_m": 1.8,
                },
                "estimation": {"outlier_mad": 4.5},
                "model_scaling": {
                    "required_bodies": ["segment"],
                    "inheritance": {},
                },
                "thresholds": {
                    "direct_body_coverage": 1.0,
                    "median_body_scale_error": 0.03,
                    "p95_body_scale_error": 0.03,
                    "model_generation_error": 1e-9,
                    "mass_relative_error": 1e-9,
                },
                "measurements": [
                    {
                        "id": "segment-width",
                        "body": "segment",
                        "marker_a": "A",
                        "marker_b": "B",
                    }
                ],
            },
            sort_keys=False,
        ),
        encoding="utf-8",
    )
    return protocol, manifest, reference


def test_dynamic_model_plan_is_cross_subject_and_reference_hidden(tmp_path):
    protocol, manifest, _ = _fixture(tmp_path)

    plan = build_dynamic_model_plan(protocol, manifest, tmp_path)

    assert plan["template_subject_id"] == "subject-a"
    assert plan["target_subject_id"] == "subject-b"
    assert plan["hidden_reference_role"] not in plan["estimation_input_roles"]
    assert "reference.osim" not in json.dumps(plan)
    assert plan["direct_body_scale_factors"]["segment"] == pytest.approx(1.105)
    assert plan["body_scale_factors"]["segment"] == pytest.approx(1.105)


def test_dynamic_model_plan_does_not_parse_hidden_reference(tmp_path):
    protocol, manifest, reference = _fixture(tmp_path)
    reference.write_text("intentionally not model XML", encoding="utf-8")
    value = yaml.safe_load(manifest.read_text(encoding="utf-8"))
    for asset in value["assets"]:
        if asset["role"] == "hidden_reference":
            asset.update(_asset(reference, "hidden_reference"))
    manifest.write_text(yaml.safe_dump(value, sort_keys=False), encoding="utf-8")

    plan = build_dynamic_model_plan(protocol, manifest, tmp_path)

    assert plan["hidden_reference_sha256"] == hashlib.sha256(
        reference.read_bytes()
    ).hexdigest()


def test_dynamic_model_evaluation_reveals_reference_after_synthesis(tmp_path):
    protocol, manifest, _ = _fixture(tmp_path)
    plan = build_dynamic_model_plan(protocol, manifest, tmp_path)
    generated = tmp_path / "generated.osim"
    _write_model(generated, 1.105, mass=12.0)
    audit = validate_public_source_bundle(manifest, tmp_path)
    thresholds = yaml.safe_load(protocol.read_text(encoding="utf-8"))["thresholds"]

    result = evaluate_dynamic_model(
        plan,
        generated,
        audit["assets_by_role"]["hidden_reference"],
        thresholds,
    )

    assert result["pass"] is True
    assert result["checks"]["target_reference_hidden_during_estimation"] is True
    assert result["summary"][
        "median_static_reference_absolute_relative_error"
    ] == pytest.approx(abs(1.105 - 1.08) / 1.08)
    assert result["measurements"][0]["dynamic_measurement_factor"] == pytest.approx(
        1.105
    )
    assert result["measurements"][0]["planned_body_factor"] == pytest.approx(1.105)
    assert result["measurements"][0]["generation_relative_error"] == pytest.approx(0.0)
    assert result["summary"]["generated_mass_kg"] == pytest.approx(12.0)


def test_dynamic_model_plan_rejects_reference_leakage(tmp_path):
    protocol, manifest, _ = _fixture(tmp_path)
    value = yaml.safe_load(protocol.read_text(encoding="utf-8"))
    value["design"]["estimation_input_roles"].append("hidden_reference")
    protocol.write_text(yaml.safe_dump(value), encoding="utf-8")

    with pytest.raises(ValueError, match="estimation_input_roles"):
        build_dynamic_model_plan(protocol, manifest, tmp_path)


def test_dynamic_model_plan_rejects_same_subject_template(tmp_path):
    protocol, manifest, _ = _fixture(tmp_path)
    value = yaml.safe_load(protocol.read_text(encoding="utf-8"))
    value["design"]["target_subject_id"] = value["design"]["template_subject_id"]
    protocol.write_text(yaml.safe_dump(value), encoding="utf-8")

    with pytest.raises(ValueError, match="must differ"):
        build_dynamic_model_plan(protocol, manifest, tmp_path)
