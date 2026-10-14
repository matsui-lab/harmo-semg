from __future__ import annotations

import hashlib
import json
from pathlib import Path

import numpy as np
import pytest
import yaml

from harmo.dynamic_personalization import (
    BLIND_SUBJECTS,
    addbiomechanics_docker_command,
    aggregate_blind_evaluations,
    annotate_marker_roles,
    apply_marker_prior,
    build_hidden_evaluation_plan,
    build_personalization_plan,
    evaluate_hidden_subject,
    estimate_functional_anatomy_from_motions,
    fit_anatomical_prior,
    functional_identifiability,
    make_constraint_free_fit_model,
    normalize_trc_time_header,
    predict_anatomical_prior,
    regularized_marker_update,
    sara_axis_repeatability,
    score_joint_center,
    seal_fit_result,
    study31_fixed_denominator,
    transfer_fit_parameters_to_full_model,
    validate_fit_plan,
)


def _sha(path: Path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _model(path: Path, offset=0.0, with_constraint=False):
    markers = "".join(
        '<Marker name="M{0}"><socket_parent_frame>/bodyset/pelvis</socket_parent_frame>'
        '<location>{1} {2} {3}</location><fixed>false</fixed></Marker>'.format(
            index, offset + index * 0.01, index * 0.002, index * -0.001
        )
        for index in range(8)
    )
    constraint = (
        '<CoordinateCouplerConstraint name="patella">'
        "<dependent_coordinate_name>q</dependent_coordinate_name>"
        "</CoordinateCouplerConstraint>"
        if with_constraint
        else ""
    )
    path.write_text(
        "<OpenSimDocument><Model><BodySet><objects>"
        '<Body name="pelvis"><mass>70</mass><mass_center>0 0 0</mass_center>'
        '<Joint><CustomJoint name="hip"><location_in_parent>{0} 0 0</location_in_parent>'
        '<location>0 0 0</location><orientation_in_parent>0 0 0</orientation_in_parent>'
        '<orientation>0 0 0</orientation><SpatialTransform>'
        '<TransformAxis name="rotation1"><axis>1 0 0</axis></TransformAxis>'
        '</SpatialTransform></CustomJoint></Joint></Body>'
        "</objects></BodySet><MarkerSet><objects>{1}</objects></MarkerSet>"
        "<ConstraintSet><objects>{2}</objects></ConstraintSet>"
        "</Model></OpenSimDocument>".format(offset, markers, constraint),
        encoding="utf-8",
    )


def _asset(path: Path, asset_id: str, role: str, subject=None, speed=None):
    row = {
        "asset_id": asset_id,
        "role": role,
        "path": path.name,
        "size_bytes": path.stat().st_size,
        "sha256": _sha(path),
    }
    if subject is not None:
        row["subject_id"] = subject
    if speed is not None:
        row["speed_m_s"] = speed
    return row


def _fixture(tmp_path):
    generic = tmp_path / "generic.osim"
    _model(generic, with_constraint=True)
    ik = tmp_path / "ik.xml"
    ik.write_text("<IK/>", encoding="utf-8")
    assets = [_asset(generic, "generic", "generic_model"), _asset(ik, "ik", "ik_setup")]
    subjects = []
    development = [
        value for value in ["{:02d}".format(index) for index in range(1, 20)]
        if value not in BLIND_SUBJECTS
    ]
    for index in range(1, 20):
        subject = "{:02d}".format(index)
        subjects.append(
            {
                "subject_id": subject,
                "height_m": 1.50 + index * 0.02,
                "mass_kg": 45.0 + index * 2.0,
                "sex": "male" if index % 2 else "female",
                "foot_length_m": 0.20 + index * 0.003,
            }
        )
        model = tmp_path / "training_{}.osim".format(subject)
        _model(model, offset=index * 0.0002)
        assets.append(
            _asset(model, "static_model_{}".format(subject), "static_derived_model", subject)
        )
    target = "05"
    for speed, token in ((2.78, "278"), (3.0, "300"), (3.33, "333")):
        marker = tmp_path / "sub{}_{}_markers.trc".format(target, token)
        grf = tmp_path / "sub{}_{}_grf.mot".format(target, token)
        marker.write_text("dynamic markers {}".format(speed), encoding="utf-8")
        grf.write_text("dynamic grf {}".format(speed), encoding="utf-8")
        assets.extend(
            [
                _asset(marker, "marker_{}".format(token), "dynamic_markers", target, speed),
                _asset(grf, "grf_{}".format(token), "dynamic_ground_reaction", target, speed),
            ]
        )
    for role, suffix in (
        ("static_markers", "static.trc"),
        ("heldout_markers", "heldout.trc"),
        ("heldout_ground_reaction", "heldout.mot"),
        ("heldout_ik", "heldout_ik.mot"),
        ("heldout_id", "heldout_id.sto"),
    ):
        path = tmp_path / "sub05_{}".format(suffix)
        path.write_text(role, encoding="utf-8")
        assets.append(
            _asset(
                path,
                role,
                role,
                target,
                4.0 if role in {"heldout_markers", "heldout_ground_reaction"} else None,
            )
        )
    inventory = tmp_path / "inventory.yaml"
    inventory.write_text(
        yaml.safe_dump(
            {"schema_version": "1.0.0", "subjects": subjects, "assets": assets},
            sort_keys=False,
        ),
        encoding="utf-8",
    )
    protocol = tmp_path / "protocol.yaml"
    protocol.write_text(
        yaml.safe_dump(
            {
                "schema_version": "1.0.0",
                "seed": 7,
                "design": {
                    "blind_subjects": list(BLIND_SUBJECTS),
                    "development_subjects": development,
                },
                "engine": {
                    "strategy": "hybrid",
                    "addbiomechanics_version": "v2.2",
                    "addbiomechanics_commit": "05890f1445e69bff582a6479249498824856fe04",
                    "container_image": "sha256:" + "a" * 64,
                    "container_platform": "linux/amd64",
                },
                "marker_classes": {
                    "anatomical": ["M{}".format(index) for index in range(8)],
                    "tracking": [],
                },
                "native_optimizer": {
                    "ridge_candidates": [0.0, 0.1],
                    "covariance_shrinkage": 0.5,
                },
                "thresholds": {},
            },
            sort_keys=False,
        ),
        encoding="utf-8",
    )
    return protocol, inventory, generic


def test_blind_fit_plan_excludes_target_static_and_heldout(tmp_path):
    protocol, inventory, _ = _fixture(tmp_path)

    plan = build_personalization_plan(
        protocol, inventory, tmp_path, subject_id="05", split="blind"
    )

    target_roles = {
        raw["role"]
        for raw in plan["inputs"].values()
        if raw.get("subject_id") == "05"
    }
    assert target_roles == {"dynamic_markers", "dynamic_ground_reaction"}
    assert len(plan["training_subject_ids"]) == 15
    assert len(plan["forbidden_input_sha256"]) == 6
    assert validate_fit_plan(plan)["pass"] is True


def test_fit_plan_rejects_target_static_and_hash_leakage(tmp_path):
    protocol, inventory, _ = _fixture(tmp_path)
    plan = build_personalization_plan(
        protocol, inventory, tmp_path, subject_id="05", split="blind"
    )
    hidden = yaml.safe_load(inventory.read_text(encoding="utf-8"))["assets"]
    static = next(
        row
        for row in hidden
        if row.get("subject_id") == "05" and row["role"] == "static_markers"
    )
    plan["inputs"]["leak"] = {
        "path": str(tmp_path / static["path"]),
        "sha256": static["sha256"],
        "role": "static_markers",
        "subject_id": "05",
        "speed_m_s": None,
    }
    with pytest.raises(ValueError, match="forbidden target role"):
        validate_fit_plan(plan)


def test_trc_time_header_normalization_is_token_limited(tmp_path):
    source = tmp_path / "public.trc"
    output = tmp_path / "worker.trc"
    source.write_bytes(
        b"PathFileType\t4\t(X/Y/Z)\tpublic.trc\n"
        b"Frame#\tTimes\tM1\t\t\n"
        b"1\t0.01\t1\t2\t3\n"
    )

    report = normalize_trc_time_header(source, output)

    assert b"Frame#\tTime\t" in output.read_bytes()
    assert output.read_bytes().splitlines()[-1] == source.read_bytes().splitlines()[-1]
    assert report["operation"] == "replace-Frame#-Times-with-Time"
    assert report["sample_bytes_modified"] is False


def test_study31_fit_uses_t1_and_hides_t2_t3(tmp_path):
    protocol, inventory, generic = _fixture(tmp_path)
    raw = yaml.safe_load(inventory.read_text(encoding="utf-8"))
    subject = "20"
    raw["subjects"].append(
        {
            "subject_id": subject,
            "height_m": 1.72,
            "mass_kg": 68.0,
            "sex": "male",
            "foot_length_m": 0.26,
        }
    )
    for trial in ("t1", "t2", "t3"):
        marker = tmp_path / "study31_{}_{}.trc".format(subject, trial)
        grf = tmp_path / "study31_{}_{}.mot".format(subject, trial)
        marker.write_text(
            "Frame#\tTime\tM0\t\t\n1\t0\t{}\t0\t0\n".format(trial),
            encoding="utf-8",
        )
        grf.write_text("endheader\ntime\n{}\n".format(trial), encoding="utf-8")
        raw["assets"].extend(
            [
                _asset(
                    marker,
                    "study31_{}_markers".format(trial),
                    "study31_{}_markers".format(trial),
                    subject,
                ),
                _asset(
                    grf,
                    "study31_{}_grf".format(trial),
                    "study31_{}_ground_reaction".format(trial),
                    subject,
                ),
            ]
        )
    inventory.write_text(yaml.safe_dump(raw, sort_keys=False), encoding="utf-8")

    plan = build_personalization_plan(
        protocol, inventory, tmp_path, subject_id=subject, split="study31"
    )
    fit_roles = {
        item["role"]
        for item in plan["inputs"].values()
        if item.get("subject_id") == subject
    }

    assert fit_roles == {"study31_t1_markers", "study31_t1_ground_reaction"}
    assert plan["trials"][0]["trial_id"] == "T1"
    assert plan["forbidden_input_attestation"]["target_t2_t3_absent"] is True
    assert len(plan["forbidden_input_sha256"]) == 4
    assert validate_fit_plan(plan)["pass"] is True


def test_subject_loo_ridge_prior_and_prediction_are_finite(tmp_path):
    models = {}
    anthropometry = {}
    for index in range(6):
        subject = "{:02d}".format(index + 1)
        path = tmp_path / "{}.osim".format(subject)
        height = 1.5 + index * 0.1
        _model(path, offset=0.01 * height)
        models[subject] = path
        anthropometry[subject] = {
            "subject_id": subject,
            "height_m": height,
            "mass_kg": 50 + index * 5,
            "sex": "male" if index % 2 else "female",
            "foot_length_m": 0.22 + index * 0.005,
        }

    prior = fit_anatomical_prior(
        models,
        anthropometry,
        ridge_candidates=[0.0, 0.1, 1.0],
        covariance_shrinkage=0.5,
    )
    target = dict(anthropometry["03"])
    target["subject_id"] = "20"
    prediction = predict_anatomical_prior(prior, target)

    assert prior["ridge_selection"]["method"] == "subject-level-leave-one-out"
    assert len(prediction["markers"]) == 8
    assert "pelvis" in prediction["segments"]
    assert "hip" in prediction["joints"]
    assert np.all(
        np.isfinite(prediction["markers"]["M0"]["standard_deviation_m"])
    )
    initialized = tmp_path / "initialized.osim"
    application = apply_marker_prior(models["03"], initialized, prediction)
    assert application["updated_joint_property_count"] >= 1
    assert application["segment_scale_proxies"][0]["body"] == "pelvis"


def test_marker_annotations_and_constraint_free_model(tmp_path):
    model = tmp_path / "model.osim"
    _model(model, with_constraint=True)
    classified = tmp_path / "classified.osim"
    annotation = annotate_marker_roles(
        model, classified, ["M{}".format(index) for index in range(8)], []
    )
    fit = tmp_path / "fit.osim"
    conversion = make_constraint_free_fit_model(classified, fit)

    assert annotation["anatomical_marker_count"] == 8
    assert conversion["removed_coordinate_couplers"] == ["patella"]
    assert "CoordinateCouplerConstraint" not in fit.read_text(encoding="utf-8")


def test_fit_parameters_transfer_back_to_full_constraint_model(tmp_path):
    full = tmp_path / "full.osim"
    fitted = tmp_path / "fitted.osim"
    output = tmp_path / "transferred.osim"
    _model(full, offset=0.0, with_constraint=True)
    _model(fitted, offset=0.03, with_constraint=False)

    report = transfer_fit_parameters_to_full_model(fitted, full, output)

    text = output.read_text(encoding="utf-8")
    assert "CoordinateCouplerConstraint" in text
    assert report["retained_coordinate_couplers"] == ["patella"]
    assert report["updated_marker_count"] == 8


def test_native_regularized_objective_uses_prior_and_marker_classes():
    prediction = {
        "markers": {
            "LASIS": {
                "mean_location_m": [0.0, 0.0, -0.1],
                "covariance_m2": (np.eye(3) * 1e-6).tolist(),
            },
            "RASIS": {
                "mean_location_m": [0.0, 0.0, 0.1],
                "covariance_m2": (np.eye(3) * 1e-6).tolist(),
            },
            "LLTHI": {
                "mean_location_m": [0.0, 0.0, -0.05],
                "covariance_m2": (np.eye(3) * 1e-4).tolist(),
            },
        }
    }
    estimates = {
        "LASIS": [[0.03, 0.0, -0.08], [0.02, 0.0, -0.09]],
        "RASIS": [[0.03, 0.0, 0.08], [0.02, 0.0, 0.09]],
        "LLTHI": [[0.03, 0.0, -0.03], [0.02, 0.0, -0.04]],
    }
    result = regularized_marker_update(
        estimates,
        prediction,
        {"anatomical": ["LASIS", "RASIS"], "tracking": ["LLTHI"]},
        dynamic_weight=1.0,
        anatomical_prior_weight=10.0,
        tracking_prior_weight=0.25,
        bilateral_symmetry_weight=0.5,
    )

    assert result["objective"].endswith("bilateral-symmetry")
    assert result["bilateral_pairs"] == [
        {"left_marker": "LASIS", "right_marker": "RASIS"}
    ]
    assert abs(result["markers"]["LASIS"]["location_m"][0]) < abs(
        result["markers"]["LLTHI"]["location_m"][0]
    )


def test_score_and_sara_identifiability_gates():
    angles = np.linspace(-0.8, 0.8, 30)
    rotations_a = np.repeat(np.eye(3)[None, :, :], 30, axis=0)
    rotations_b = np.asarray(
        [
            [[np.cos(a), -np.sin(a), 0], [np.sin(a), np.cos(a), 0], [0, 0, 1]]
            for a in angles
        ]
    )
    center_a = np.asarray([0.1, -0.2, 0.3])
    center_b = np.asarray([-0.2, 0.1, 0.05])
    translation_a = np.zeros((30, 3))
    translation_b = (
        translation_a
        + np.einsum("nij,j->ni", rotations_a, center_a)
        - np.einsum("nij,j->ni", rotations_b, center_b)
    )
    score = score_joint_center(
        rotations_a,
        translation_a,
        rotations_b,
        translation_b,
        bootstrap_iterations=50,
        seed=2,
    )
    sara = sara_axis_repeatability([[0, 0, 1], [0.01, 0, 1], [-0.01, 0, 1]])
    gate = functional_identifiability(score, sara)

    assert score["residual_rms_m"] < 1e-10
    assert sara["identifiable"] is True
    assert gate["functional_unidentifiable"] is False


def test_functional_estimator_rejects_circular_ik_only_input(tmp_path):
    model = tmp_path / "model.osim"
    motion = tmp_path / "ik.mot"
    _model(model)
    motion.write_text("endheader\ntime\n0\n", encoding="utf-8")

    with pytest.raises(ValueError, match="independent marker trial"):
        estimate_functional_anatomy_from_motions(model, [motion])


def test_seal_precedes_hidden_evaluation_and_container_is_offline(tmp_path):
    protocol, inventory, generic = _fixture(tmp_path)
    plan = build_personalization_plan(
        protocol, inventory, tmp_path, subject_id="05", split="blind"
    )
    worker = {"engine_identity": {"selected": "harmo-native"}, "result": "frozen"}
    seal = seal_fit_result(plan, worker, generic)
    hidden = build_hidden_evaluation_plan(protocol, inventory, tmp_path, seal, generic)
    command = addbiomechanics_docker_command(plan, tmp_path / "subject")

    assert hidden["evaluation_opened_after_seal"] is True
    assert hidden["candidate_model_sha256"] == seal["candidate_model_sha256"]
    assert command[command.index("--network") + 1] == "none"
    assert plan["engine"]["container_image"] in command


def test_hidden_thresholds_blind_all_or_none_and_study31_denominator(tmp_path):
    protocol, inventory, generic = _fixture(tmp_path)
    plan = build_personalization_plan(
        protocol, inventory, tmp_path, subject_id="05", split="blind"
    )
    seal = seal_fit_result(plan, {"engine_identity": {}}, generic)
    hidden = build_hidden_evaluation_plan(protocol, inventory, tmp_path, seal, generic)
    passing_metrics = {
        "provenance": {
            "candidate_model_sha256": hidden["candidate_model_sha256"],
            "hidden_input_sha256": hidden["hidden_input_sha256"],
            "evaluator_identity": "test-hidden-evaluator",
        },
        "segment_scale_median_relative_error": 0.0,
        "segment_scale_p95_relative_error": 0.0,
        "ik_rms_error_m": 0.0,
        "ik_max_error_m": 0.0,
        "rra_angle_change_rms_deg": 0.0,
        "residual_force_fraction": 0.0,
        "residual_moment_fraction": 0.0,
        "joint_angle_median_rmse_deg": 0.0,
        "joint_angle_max_rmse_deg": 0.0,
        "joint_moment_median_nrmse": 0.0,
        "joint_moment_max_nrmse": 0.0,
        "minimum_joint_moment_correlation": 1.0,
    }
    first = evaluate_hidden_subject(hidden, passing_metrics)
    unbound = evaluate_hidden_subject(
        hidden, {key: value for key, value in passing_metrics.items() if key != "provenance"}
    )
    assert unbound["pass"] is False
    assert unbound["checks"]["metrics_hidden_inputs_bound"] is False
    rows = [{**first, "subject_id": subject} for subject in BLIND_SUBJECTS]
    assert aggregate_blind_evaluations(rows)["study31_authorized"] is True
    rows[-1]["pass"] = False
    assert aggregate_blind_evaluations(rows)["study31_authorized"] is False
    ledger = study31_fixed_denominator(
        [
            {"trial_id": "trial-{}".format(index), "quality_status": "pass"}
            for index in range(330)
        ]
    )
    assert ledger["processed_trial_count"] == 330
    assert ledger["source_absent_count"] == 6
    assert ledger["expected_trial_count"] == 336
    assert ledger["denominator_preserved"] is True
