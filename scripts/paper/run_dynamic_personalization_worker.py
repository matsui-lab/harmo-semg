#!/usr/bin/env python3
"""Execute the isolated G6d hybrid dynamic-personalization fit worker."""

from __future__ import annotations

import argparse
import json
import shutil
import subprocess
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
if str(ROOT / "src") not in sys.path:
    sys.path.insert(0, str(ROOT / "src"))

from harmo.dynamic_calibration import sha256_file  # noqa: E402
from harmo.dynamic_personalization import (  # noqa: E402
    addbiomechanics_docker_command,
    annotate_marker_roles,
    apply_identifiable_functional_anatomy,
    apply_marker_prior,
    estimate_functional_anatomy_from_motions,
    fit_anatomical_prior,
    forward_marker_equivalence,
    functional_identifiability,
    make_constraint_free_fit_model,
    predict_anatomical_prior,
    prepare_addbiomechanics_subject,
    transfer_fit_parameters_to_full_model,
    validate_fit_plan,
)
from harmo.mechanics_audit import canonical_sha256  # noqa: E402


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--plan", required=True)
    parser.add_argument("--work-dir", required=True)
    parser.add_argument("--output-model", required=True)
    parser.add_argument("--report", required=True)
    parser.add_argument("--engine", choices=("auto", "addbiomechanics", "harmo-native"), default="auto")
    parser.add_argument("--docker-timeout", type=int, default=14400)
    return parser


def _input_by_role(plan: dict, role: str):
    matches = [
        (asset_id, raw)
        for asset_id, raw in plan["inputs"].items()
        if raw.get("role") == role
    ]
    if len(matches) != 1:
        raise ValueError("expected one {} input".format(role))
    return matches[0]


def _training_models(plan: dict):
    rows = {}
    for raw in plan["inputs"].values():
        if raw.get("role") == "static_derived_model":
            rows[str(raw["subject_id"])] = Path(str(raw["path"])).resolve()
    if set(rows) != set(plan["training_subject_ids"]):
        raise ValueError("training model subjects differ from the frozen plan")
    return rows


def _trc_marker_names(path: Path):
    for line in path.read_text(encoding="utf-8").splitlines():
        if line.startswith("Frame#\tTime\t") or line.startswith("Frame#\tTimes\t"):
            return [
                value.strip()
                for value in line.split("\t")[2:]
                if value.strip()
            ]
    raise ValueError("dynamic TRC has no marker-name header")


def _native_registration_plan(plan: dict, prior_model: Path, prediction: dict) -> dict:
    native = plan.get("native_optimizer") or {}
    inputs = {
        "base_dynamic_model": str(prior_model.resolve()),
    }
    hashes = {"base_dynamic_model": sha256_file(prior_model)}
    roles = []
    marker_sets = []
    for trial in plan["trials"]:
        if trial.get("speed_m_s") is None:
            token = str(trial.get("trial_id", "trial"))
            role = "dynamic_{}".format(token.lower())
        else:
            role = "dynamic_{:.2f}".format(float(trial["speed_m_s"])).replace(".", "p")
        source = plan["inputs"][trial["markers_asset_id"]]
        inputs[role] = str(Path(source["path"]).resolve())
        hashes[role] = str(source["sha256"])
        roles.append(role)
        marker_sets.append(set(_trc_marker_names(Path(source["path"]))))
    ik_id, ik = _input_by_role(plan, "ik_setup")
    inputs["public_ik_setup"] = str(Path(ik["path"]).resolve())
    hashes["public_ik_setup"] = str(ik["sha256"])
    return {
        "schema_version": "1.0.0",
        "scope": "g6b-dynamic-marker-registration-plan",
        "target_subject_id": plan["subject_id"],
        "required_opensim_version": str(native.get("required_opensim_version", "4.5.2")),
        "calibration_roles": roles,
        "input_paths": inputs,
        "input_sha256": hashes,
        "active_markers": sorted(
            set.intersection(*marker_sets)
            & (
                set(plan["marker_classes"]["anatomical"])
                | set(plan["marker_classes"]["tracking"])
            )
        ),
        "start_time_s": float(native.get("calibration_window_s", [20.0, 30.0])[0]),
        "end_time_s": float(native.get("calibration_window_s", [20.0, 30.0])[1]),
        "registration": {
            "iteration_count": int(native.get("iteration_count", 3)),
            "sampled_frame_stride": int(native.get("sampled_frame_stride", 20)),
            "within_trial_outlier_mad": float(native.get("within_trial_outlier_mad", 4.5)),
            "relaxation": float(native.get("relaxation", 0.5)),
            "maximum_total_marker_displacement_m": float(
                native.get("maximum_total_marker_displacement_m", 0.12)
            ),
        },
        "marker_prior_prediction": prediction,
        "marker_classes": plan["marker_classes"],
        "objective": {
            "dynamic_weight": float(native.get("dynamic_weight", 1.0)),
            "anatomical_prior_weight": float(
                native.get("anatomical_prior_weight", 10.0)
            ),
            "tracking_prior_weight": float(native.get("tracking_prior_weight", 0.25)),
            "dynamic_noise_floor_m": float(native.get("dynamic_noise_floor_m", 0.005)),
            "rigid_cluster_weight": float(native.get("rigid_cluster_weight", 1.0)),
            "bilateral_symmetry_weight": float(
                native.get("bilateral_symmetry_weight", 0.0)
            ),
        },
        "prior_sha256": sha256_file(prior_model),
        "forbidden_input_attestation": plan["forbidden_input_attestation"],
    }


def _run_native(
    plan: dict,
    prior_model: Path,
    prediction: dict,
    work_dir: Path,
    output_model: Path,
):
    registration_plan = _native_registration_plan(plan, prior_model, prediction)
    plan_path = work_dir / "harmo_native_registration_plan.json"
    report_path = work_dir / "harmo_native_registration_report.json"
    plan_path.write_text(
        json.dumps(registration_plan, indent=2, sort_keys=True) + "\n", encoding="utf-8"
    )
    command = [
        sys.executable,
        str(ROOT / "scripts/paper/run_dynamic_registration_worker.py"),
        "--plan",
        str(plan_path),
        "--work-dir",
        str(work_dir / "harmo_native"),
        "--output-model",
        str(output_model),
        "--report",
        str(report_path),
    ]
    completed = subprocess.run(command, capture_output=True, text=True, check=False)
    (work_dir / "harmo_native_stdout.log").write_text(completed.stdout, encoding="utf-8")
    (work_dir / "harmo_native_stderr.log").write_text(completed.stderr, encoding="utf-8")
    if completed.returncode != 0 or not output_model.is_file() or not report_path.is_file():
        raise RuntimeError(
            "harmo-native registration failed with exit code {}".format(completed.returncode)
        )
    report = json.loads(report_path.read_text(encoding="utf-8"))
    if report.get("hidden_inputs_available") is not False:
        raise ValueError("harmo-native worker did not attest hidden-input isolation")
    motion_paths = [
        work_dir / "harmo_native" / "final" / role / "ik_motion.mot"
        for role in registration_plan["calibration_roles"]
    ]
    if not all(path.is_file() for path in motion_paths):
        raise ValueError("harmo-native final IK artifacts are incomplete")
    return {
        "engine": "harmo-native",
        "command": command,
        "registration_plan_sha256": sha256_file(plan_path),
        "registration_report": report,
        "stdout_sha256": sha256_file(work_dir / "harmo_native_stdout.log"),
        "stderr_sha256": sha256_file(work_dir / "harmo_native_stderr.log"),
        "functional_motion_paths": [str(path) for path in motion_paths],
        "functional_marker_paths": [
            registration_plan["input_paths"][role]
            for role in registration_plan["calibration_roles"]
        ],
    }


def _run_addbiomechanics(
    plan: dict,
    fit_model: Path,
    full_model: Path,
    work_dir: Path,
    output_model: Path,
    timeout: int,
):
    subject_dir = work_dir / "addbiomechanics_subject"
    preparation = prepare_addbiomechanics_subject(plan, fit_model, subject_dir)
    command = addbiomechanics_docker_command(plan, subject_dir)
    completed = subprocess.run(
        command, capture_output=True, text=True, check=False, timeout=int(timeout)
    )
    stdout = work_dir / "addbiomechanics_stdout.log"
    stderr = work_dir / "addbiomechanics_stderr.log"
    stdout.write_text(completed.stdout, encoding="utf-8")
    stderr.write_text(completed.stderr, encoding="utf-8")
    models = subject_dir / "osim_results" / "Models"
    candidate_names = (
        "match_markers_and_physics.osim",
        "match_markers_but_ignore_physics.osim",
    )
    candidate = next((models / name for name in candidate_names if (models / name).is_file()), None)
    if completed.returncode != 0 or candidate is None:
        raise RuntimeError(
            "AddBiomechanics v2.2 failed with exit code {}".format(completed.returncode)
        )
    fitted_candidate = work_dir / "addbiomechanics_fitted_constraint_free.osim"
    shutil.copy2(str(candidate), str(fitted_candidate))
    transfer = transfer_fit_parameters_to_full_model(
        fitted_candidate, full_model, output_model
    )
    transfer_equivalence = forward_marker_equivalence(
        output_model,
        fitted_candidate,
        pose_count=100,
        seed=int(plan["seed"]),
        tolerance_m=1e-6,
    )
    if not transfer_equivalence["pass"]:
        raise ValueError(
            "AddBiomechanics fit parameters are not forward-marker equivalent after HBM2 transfer"
        )
    return {
        "engine": "addbiomechanics-v2.2",
        "command": command,
        "container_image": plan["engine"]["container_image"],
        "commit": plan["engine"]["addbiomechanics_commit"],
        "subject_preparation": preparation,
        "source_output_model_sha256": sha256_file(candidate),
        "parameter_transfer": transfer,
        "transfer_forward_marker_equivalence": transfer_equivalence,
        "stdout_sha256": sha256_file(stdout),
        "stderr_sha256": sha256_file(stderr),
    }


def main(argv=None) -> int:
    args = _parser().parse_args(argv)
    plan_path = Path(args.plan).resolve()
    work_dir = Path(args.work_dir).resolve()
    output_model = Path(args.output_model).resolve()
    report_path = Path(args.report).resolve()
    plan = json.loads(plan_path.read_text(encoding="utf-8"))
    leakage_audit = validate_fit_plan(plan)
    if plan.get("plan_sha256") != canonical_sha256(
        {key: value for key, value in plan.items() if key != "plan_sha256"}
    ):
        raise ValueError("G6d fit plan changed after construction")
    work_dir.mkdir(parents=True, exist_ok=True)

    training_models = _training_models(plan)
    native = plan.get("native_optimizer") or {}
    prior = fit_anatomical_prior(
        training_models,
        plan["training_anthropometry"],
        ridge_candidates=native.get("ridge_candidates", [0.0, 0.01, 0.1, 1.0, 10.0]),
        covariance_shrinkage=float(native.get("covariance_shrinkage", 0.5)),
    )
    prior_path = work_dir / "anatomical_prior.json"
    prior_path.write_text(json.dumps(prior, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    prediction = predict_anatomical_prior(prior, plan["anthropometry"])
    prediction_path = work_dir / "subject_prior.json"
    prediction_path.write_text(
        json.dumps(prediction, indent=2, sort_keys=True) + "\n", encoding="utf-8"
    )
    _, generic = _input_by_role(plan, "generic_model")
    prior_model = work_dir / "prior_initialized.osim"
    prior_application = apply_marker_prior(Path(generic["path"]), prior_model, prediction)
    classified_model = work_dir / "prior_initialized_classified.osim"
    marker_annotation = annotate_marker_roles(
        prior_model,
        classified_model,
        plan["marker_classes"]["anatomical"],
        plan["marker_classes"]["tracking"],
    )
    fit_model = work_dir / "fit_constraint_free.osim"
    constraint_conversion = make_constraint_free_fit_model(classified_model, fit_model)
    equivalence = forward_marker_equivalence(
        classified_model,
        fit_model,
        pose_count=100,
        seed=int(plan["seed"]),
        tolerance_m=1e-6,
    )
    if not equivalence["pass"]:
        raise ValueError("fit-only model is not forward-marker equivalent to full HBM2")

    attempts = []
    engine_result = None
    if args.engine in {"auto", "addbiomechanics"}:
        try:
            engine_result = _run_addbiomechanics(
                plan,
                fit_model,
                classified_model,
                work_dir,
                output_model,
                args.docker_timeout,
            )
        except Exception as exc:
            attempts.append(
                {"engine": "addbiomechanics-v2.2", "success": False, "error": str(exc)}
            )
            if args.engine == "addbiomechanics":
                raise
    if engine_result is None:
        engine_result = _run_native(
            plan, classified_model, prediction, work_dir, output_model
        )
    attempts.append({"engine": engine_result["engine"], "success": True})

    # Functional centers/axes are admitted only when the dynamic-IK SCoRE/SARA
    # quality gates pass bilaterally.  Otherwise the population prior already
    # present in the output model remains authoritative.
    functional_transfer = None
    if engine_result["engine"] == "harmo-native":
        functional = plan.get("functional_calibration") or {}
        try:
            identifiability = estimate_functional_anatomy_from_motions(
                output_model,
                [Path(path) for path in engine_result["functional_motion_paths"]],
                [Path(path) for path in engine_result["functional_marker_paths"]],
                score_threshold_m=float(
                    functional.get("score_bootstrap_residual_threshold_m", 0.006)
                ),
                sara_threshold_deg=float(
                    functional.get("sara_trial_axis_difference_threshold_deg", 5.0)
                ),
                seed=int(plan["seed"]),
            )
            if identifiability["functional_unidentifiable"] is False:
                functional_model = work_dir / "functional_anatomy_candidate.osim"
                functional_transfer = apply_identifiable_functional_anatomy(
                    output_model, functional_model, identifiability
                )
                import opensim

                checked = opensim.Model(str(functional_model))
                checked.initSystem()
                shutil.copy2(str(functional_model), str(output_model))
                functional_transfer["validated_opensim_load"] = True
                functional_transfer["final_model_sha256"] = sha256_file(output_model)
        except Exception as exc:
            identifiability = functional_identifiability(None, None)
            identifiability["attempted"] = True
            identifiability["error"] = str(exc)
    else:
        identifiability = functional_identifiability(None, None)
        identifiability["attempted"] = False
        identifiability["reason"] = "AddBiomechanics jointly optimized model; fallback SCoRE/SARA not invoked"
    report = {
        "schema_version": "1.0.0",
        "scope": "g6d-dynamic-personalization-fit-worker",
        "subject_id": plan["subject_id"],
        "split": plan["split"],
        "input_sha256": plan["input_sha256"],
        "forbidden_input_attestation": plan["forbidden_input_attestation"],
        "leakage_audit": leakage_audit,
        "engine_identity": {
            "selected": engine_result["engine"],
            "addbiomechanics_version": plan["engine"]["addbiomechanics_version"],
            "addbiomechanics_commit": plan["engine"]["addbiomechanics_commit"],
            "container_image": plan["engine"]["container_image"],
            "seed": int(plan["seed"]),
        },
        "engine_attempts": attempts,
        "engine_result": engine_result,
        "prior": {
            "training_subject_ids": prior["training_subject_ids"],
            "ridge_selection": prior["ridge_selection"],
            "covariance_shrinkage": prior["covariance_shrinkage"],
            "artifact_sha256": sha256_file(prior_path),
            "prediction_sha256": sha256_file(prediction_path),
        },
        "prior_application": prior_application,
        "marker_annotation": marker_annotation,
        "constraint_conversion": constraint_conversion,
        "forward_marker_equivalence": equivalence,
        "identifiability": identifiability,
        "functional_transfer": functional_transfer,
        "uncertainty": {
            "markers_standard_deviation_m": {
                marker: raw["standard_deviation_m"]
                for marker, raw in prediction["markers"].items()
            },
            "segment_scale_proxies": prediction.get("segments", {}),
            "joint_centers_and_axes": prediction.get("joints", {}),
        },
        "output_model_sha256": sha256_file(output_model),
        "artifact_lineage": {
            "plan_sha256": sha256_file(plan_path),
            "prior_model_sha256": sha256_file(prior_model),
            "classified_model_sha256": sha256_file(classified_model),
            "fit_model_sha256": sha256_file(fit_model),
            "output_model_sha256": sha256_file(output_model),
        },
    }
    report["canonical_result_sha256"] = canonical_sha256(report)
    report_path.parent.mkdir(parents=True, exist_ok=True)
    report_path.write_text(
        json.dumps(report, indent=2, sort_keys=True) + "\n", encoding="utf-8"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
