#!/usr/bin/env python3
"""Run the checksum-bound G6c OpenSim source-faithfulness audit."""

from __future__ import annotations

import argparse
import importlib.util
import json
import math
import shutil
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
if str(ROOT / "src") not in sys.path:
    sys.path.insert(0, str(ROOT / "src"))

from harmo.dynamic_calibration import sha256_file  # noqa: E402
from harmo.dynamic_model import model_total_mass  # noqa: E402
from harmo.g6_execution import rra_residual_metrics  # noqa: E402
from harmo.mechanics_audit import (  # noqa: E402
    assess_mass_recommendation,
    audit_c3d_mot_external_loads,
    audit_c3d_mot_combined_platform,
    canonicalize_opensim_log,
    compare_inverse_dynamics,
    diagnose_released_kinematics_closure,
    mean_total_vertical_grf,
    parse_rra_mass_recommendation,
    pelvis_mass_center,
    resample_storage_to_grid,
    verify_exact_grid,
)


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--plan", required=True)
    parser.add_argument("--work-dir", required=True)
    parser.add_argument("--report", required=True)
    return parser


def _load_legacy_worker():
    path = ROOT / "scripts/paper/run_dynamic_mechanics_worker.py"
    spec = importlib.util.spec_from_file_location("harmo_g6c_legacy_worker", str(path))
    if spec is None or spec.loader is None:
        raise RuntimeError("cannot load the OpenSim mechanics worker")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _external_audit(plan: dict) -> dict:
    config = plan.get("external_loads") or {}
    raw = plan["input_paths"].get("raw_c3d")
    if not raw:
        return {
            "pass": False,
            "status": "raw-c3d-absent",
            "selection_basis": "source-provenance-required",
        }
    if config.get("aggregation") == "combined_platform":
        return audit_c3d_mot_combined_platform(
            Path(raw),
            Path(plan["input_paths"]["ground_reaction"]),
            platform_index=int(config["platform_index_zero_based"]),
            published_prefixes=[str(value) for value in config["published_prefixes"]],
            c3d_to_ground_rotation=config["c3d_to_ground_rotation"],
            c3d_time_offset_s=float(config["c3d_time_offset_s"]),
            vertical_axis=int(plan["vertical_axis"]),
            force_nrmse_tolerance=float(config.get("force_nrmse_tolerance", 0.05)),
            cop_rmse_tolerance_m=float(config.get("cop_rmse_tolerance_m", 0.10)),
            minimum_component_correlation=float(
                config.get("minimum_component_correlation", 0.90)
            ),
            minimum_free_moment_correlation=float(
                config.get("minimum_free_moment_correlation", 0.70)
            ),
        )
    mapping = {
        int(index): str(prefix)
        for index, prefix in (config.get("plate_to_prefix") or {}).items()
    }
    if not mapping:
        raise ValueError("G6c raw C3D audit requires a declared plate-to-prefix map")
    return audit_c3d_mot_external_loads(
        Path(raw),
        Path(plan["input_paths"]["ground_reaction"]),
        mapping,
        c3d_to_ground_rotation=config.get("c3d_to_ground_rotation"),
        c3d_to_ground_translation_m=config.get(
            "c3d_to_ground_translation_m", [0.0, 0.0, 0.0]
        ),
        force_rmse_tolerance_n=float(config.get("force_rmse_tolerance_n", 1e-5)),
        point_rmse_tolerance_m=float(config.get("point_rmse_tolerance_m", 1e-6)),
        torque_rmse_tolerance_nm=float(config.get("torque_rmse_tolerance_nm", 1e-6)),
    )


def main(argv=None) -> int:
    args = _parser().parse_args(argv)
    plan_path = Path(args.plan).resolve()
    work_dir = Path(args.work_dir).resolve()
    report_path = Path(args.report).resolve()
    plan = json.loads(plan_path.read_text(encoding="utf-8"))
    if plan.get("scope") != "g6c-mechanics-audit-plan":
        raise ValueError("worker received an invalid G6c plan")
    for role, expected in plan.get("input_sha256", {}).items():
        path = Path(str(plan["input_paths"][role])).resolve()
        if not path.is_file() or sha256_file(path) != str(expected):
            raise ValueError("G6c worker input checksum mismatch: {}".format(role))

    import opensim

    version = str(opensim.GetVersion())
    required = str((plan.get("opensim") or {}).get("required_version", "4.5.2"))
    if not version.startswith(required):
        raise RuntimeError("G6c requires OpenSim {}, observed {}".format(required, version))
    work_dir.mkdir(parents=True, exist_ok=True)
    log_path = work_dir / "opensim_g6c.log"
    logger_sink = False
    if hasattr(opensim, "Logger") and hasattr(opensim.Logger, "addFileSink"):
        opensim.Logger.addFileSink(str(log_path))
        logger_sink = True
    try:
        legacy = _load_legacy_worker()
        paths = {key: Path(value).resolve() for key, value in plan["input_paths"].items()}
        model_path = paths["model"]
        target_grid = [float(value) for value in plan["source_time_grid_s"]]
        public_grid = [float(value) for value in plan["public_id_time_grid_s"]]
        public_ik_grid = work_dir / "public_ik_source_grid.mot"
        grid_report = resample_storage_to_grid(
            paths["public_ik"], public_grid, public_ik_grid
        )
        loads_path = work_dir / "external_loads.xml"
        legacy._generate_external_loads(opensim, paths["ground_reaction"], loads_path)

        id_dir = work_dir / "source_faithfulness_id"
        id_dir.mkdir(parents=True, exist_ok=True)
        reproduced_id = legacy._run_id(
            opensim,
            model_path,
            public_ik_grid,
            loads_path,
            paths["id_setup"],
            float((plan.get("opensim") or {}).get("id_lowpass_cutoff_hz", 15.0)),
            float(public_grid[0]),
            float(public_grid[-1]),
            id_dir,
        )
        public_id_grid_exact = verify_exact_grid(reproduced_id, public_grid)
        comparison = compare_inverse_dynamics(
            paths["public_id"], reproduced_id, plan["major_moments"]
        )

        closure_config = plan.get("closure_diagnostic") or {}
        diagnostic_window = [
            float(value) for value in closure_config["marker_rederived_window_s"]
        ]
        comparison_window = [
            float(value) for value in closure_config["comparison_window_s"]
        ]
        closure_dir = work_dir / "kinematics_id_closure"
        closure_dir.mkdir(parents=True, exist_ok=True)
        closure_markers = closure_dir / "markers_opensim_compatible.trc"
        closure_marker_normalization = legacy.normalize_trc_time_header(
            paths["markers"], closure_markers
        )
        marker_rederived_ik, marker_rederived_ik_errors = legacy._run_ik(
            opensim,
            model_path,
            closure_markers,
            paths["ik_setup"],
            diagnostic_window[0],
            diagnostic_window[1],
            closure_dir,
            "closure_diagnostic",
        )
        marker_rederived_id_dir = closure_dir / "marker_rederived_id"
        marker_rederived_id_dir.mkdir(parents=True, exist_ok=True)
        marker_rederived_id = legacy._run_id(
            opensim,
            model_path,
            marker_rederived_ik,
            loads_path,
            paths["id_setup"],
            float((plan.get("opensim") or {}).get("id_lowpass_cutoff_hz", 20.0)),
            diagnostic_window[0],
            diagnostic_window[1],
            marker_rederived_id_dir,
        )
        marker_rederived_comparison = compare_inverse_dynamics(
            paths["public_id"],
            marker_rederived_id,
            plan["major_moments"],
            comparison_window_s=comparison_window,
        )
        kinematics_id_closure = diagnose_released_kinematics_closure(
            comparison,
            marker_rederived_comparison,
            distal_coordinates=closure_config["distal_moments"],
            proximal_coordinates=closure_config["proximal_moments"],
            thresholds=plan.get("thresholds") or {},
            minimum_proximal_improvement_factor=float(
                closure_config["minimum_proximal_improvement_factor"]
            ),
        )

        rra_dir = work_dir / "static_reference_rra"
        rra_dir.mkdir(parents=True, exist_ok=True)
        normalized_markers = rra_dir / "markers_opensim_compatible.trc"
        marker_normalization = legacy.normalize_trc_time_header(
            paths["markers"], normalized_markers
        )
        target_ik, target_ik_errors = legacy._run_ik(
            opensim,
            model_path,
            normalized_markers,
            paths["ik_setup"],
            float(plan["start_time_s"]),
            float(plan["end_time_s"]),
            rra_dir,
            "static_reference",
        )
        rra_config = (plan.get("opensim") or {}).get("rra") or {}
        actuators, tasks, residual_point = legacy._generate_rra_files(
            opensim, model_path, rra_config, rra_dir
        )
        current_model = model_path
        final_kinematics = None
        final_actuation = None
        for pass_number in (1, 2):
            current_model, final_kinematics, final_actuation = legacy._run_rra_pass(
                opensim,
                current_model,
                target_ik,
                loads_path,
                actuators,
                tasks,
                rra_config,
                float(plan["start_time_s"]),
                float(plan["end_time_s"]),
                rra_dir,
                pass_number,
            )
        residuals = rra_residual_metrics(Path(final_actuation))
        adaptive_kinematics = rra_dir / "rra_kinematics_adaptive.sto"
        source_grid_kinematics = rra_dir / "rra_kinematics_source_grid.sto"
        shutil.copy2(str(final_kinematics), str(adaptive_kinematics))
        rra_grid_report = resample_storage_to_grid(
            adaptive_kinematics, target_grid, source_grid_kinematics
        )
        final_id_dir = rra_dir / "source_grid_id"
        final_id_dir.mkdir(parents=True, exist_ok=True)
        final_id = legacy._run_id(
            opensim,
            Path(current_model),
            source_grid_kinematics,
            loads_path,
            paths["id_setup"],
            float((plan.get("opensim") or {}).get("id_lowpass_cutoff_hz", 20.0)),
            float(target_grid[0]),
            float(target_grid[-1]),
            final_id_dir,
        )
        source_grid_exact = verify_exact_grid(final_id, target_grid)
        model_mass = model_total_mass(model_path)
        com_height = legacy._com_height(opensim, Path(current_model), Path(final_kinematics))
        force_fraction = residuals["residual_force_rms"] / (model_mass * 9.80665)
        moment_fraction = residuals["residual_moment_rms"] / (
            model_mass * 9.80665 * com_height
        )
        thresholds = plan.get("thresholds") or {}
        common_residual_explained = (
            force_fraction <= float(thresholds.get("residual_force_fraction", 0.05))
            and moment_fraction
            <= float(thresholds.get("residual_moment_fraction", 0.01))
        )
        expected_com = pelvis_mass_center(model_path)
        point_matches = len(residual_point) == 3 and all(
            abs(left - right) <= 1e-12
            for left, right in zip(residual_point, expected_com)
        )
    finally:
        if logger_sink and hasattr(opensim.Logger, "removeFileSink"):
            opensim.Logger.removeFileSink()

    log_text = log_path.read_text(encoding="utf-8", errors="replace") if log_path.is_file() else ""
    canonical_log_path = work_dir / "opensim_g6c_canonical.log"
    canonical_log_path.write_text(
        canonicalize_opensim_log(log_text), encoding="utf-8", newline="\n"
    )
    mass_recommendation = parse_rra_mass_recommendation(log_text)
    # The vertical GRF comparison is meaningful only after the raw-source
    # convention passes.  It is recorded separately and never auto-applied.
    grf_mass_source = mean_total_vertical_grf(
        paths["ground_reaction"],
        start_time_s=float(plan["start_time_s"]),
        end_time_s=float(plan["end_time_s"]),
        vertical_axis=int(plan["vertical_axis"]),
        prefixes=[str(value) for value in plan["external_loads"]["published_prefixes"]],
    )
    mass_assessment = assess_mass_recommendation(
        mass_recommendation["recommended_mass_change_kg"],
        model_mass,
        grf_mass_source["mean_total_vertical_grf_n"],
    )
    external_audit = _external_audit(plan)
    report = {
        "schema_version": "1.0.0",
        "scope": "g6c-mechanics-audit-worker",
        "opensim_version": version,
        "input_sha256": plan["input_sha256"],
        "source_grid_resampling": grid_report,
        "public_id_grid_exact": public_id_grid_exact,
        "source_grid_exact": source_grid_exact,
        "public_id_reproduction": comparison,
        "kinematics_id_closure": {
            **kinematics_id_closure,
            "diagnostic_window_s": diagnostic_window,
            "comparison_window_s": comparison_window,
            "marker_rederived_id_comparison": marker_rederived_comparison,
            "marker_normalization": closure_marker_normalization,
            "marker_rederived_ik_sha256": sha256_file(Path(marker_rederived_ik)),
            "marker_rederived_ik_errors_sha256": sha256_file(
                Path(marker_rederived_ik_errors)
            ),
            "marker_rederived_id_sha256": sha256_file(Path(marker_rederived_id)),
        },
        "reproduced_id": {
            "path": str(reproduced_id),
            "sha256": sha256_file(reproduced_id),
        },
        "external_load_audit": external_audit,
        "residual_force_application_point": {
            "body": "pelvis",
            "frame": "pelvis",
            "point_m": residual_point,
            "source": "model-pelvis-mass-center",
        },
        "residual_point_is_pelvis_com": point_matches,
        "static_reference_rra": {
            **residuals,
            "residual_force_fraction": force_fraction,
            "residual_moment_fraction": moment_fraction,
            "com_height_m": com_height,
            "marker_normalization": marker_normalization,
            "ik_marker_errors_sha256": sha256_file(Path(target_ik_errors)),
            "rra_source_grid_resampling": rra_grid_report,
        },
        "common_residual_explained": common_residual_explained,
        "mass_recommendation": {
            **mass_recommendation,
            "vertical_grf_source": grf_mass_source,
            "assessment": mass_assessment,
        },
        "artifacts": {
            "plan_sha256": sha256_file(plan_path),
            "external_loads_sha256": sha256_file(loads_path),
            "actuators_sha256": sha256_file(actuators),
            "tasks_sha256": sha256_file(tasks),
            "final_rra_model_sha256": sha256_file(Path(current_model)),
            "final_rra_kinematics_sha256": sha256_file(Path(final_kinematics)),
            "final_rra_actuation_sha256": sha256_file(Path(final_actuation)),
            "final_source_grid_id_sha256": sha256_file(Path(final_id)),
            "closure_marker_rederived_ik_sha256": sha256_file(
                Path(marker_rederived_ik)
            ),
            "closure_marker_rederived_id_sha256": sha256_file(
                Path(marker_rederived_id)
            ),
            "opensim_canonical_log_sha256": sha256_file(canonical_log_path),
            "raw_opensim_log": {
                "retained_in_work_directory": log_path.is_file(),
                "hash_in_scientific_lineage": False,
                "reason": "wall-clock timestamps and elapsed runtimes are nondeterministic",
            },
        },
    }
    report_path.parent.mkdir(parents=True, exist_ok=True)
    report_path.write_text(
        json.dumps(report, indent=2, sort_keys=True) + "\n", encoding="utf-8"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
