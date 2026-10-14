#!/usr/bin/env python3
"""Run the checksum-bound G6c-S6 structural-adequacy analysis."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

import numpy as np
import pandas as pd


ROOT = Path(__file__).resolve().parents[2]
if str(ROOT / "src") not in sys.path:
    sys.path.insert(0, str(ROOT / "src"))

from harmo.dynamic_calibration import sha256_file  # noqa: E402
from harmo.ingest.readers.sto import read_opensim_sto_trc  # noqa: E402
from harmo.structural_adequacy import (  # noqa: E402
    build_stride_intervals,
    classify_structural_adequacy,
    compute_upper_limb_orbital_proxy,
    detect_sustained_contacts,
    inspect_full_body_c3d,
    summarize_study31_strides,
)


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--plan", required=True)
    parser.add_argument("--work-dir", required=True)
    parser.add_argument("--report", required=True)
    return parser


def _numeric_storage(path: Path) -> pd.DataFrame:
    _, table = read_opensim_sto_trc(str(path))
    numeric = table.apply(pd.to_numeric, errors="coerce")
    if "time" not in numeric:
        raise ValueError("G6c-S6 storage lacks time")
    time = numeric["time"].to_numpy(float)
    if (
        time.size < 16
        or np.any(np.diff(time) <= 0)
        or not np.all(np.isfinite(numeric.to_numpy(float)))
    ):
        raise ValueError("G6c-S6 storage must be finite and ordered")
    return numeric


def _write_rows(rows, path: Path) -> None:
    if not rows:
        raise ValueError("G6c-S6 output table cannot be empty")
    path.parent.mkdir(parents=True, exist_ok=True)
    pd.DataFrame(rows).to_csv(
        path,
        index=False,
        float_format="%.12g",
        lineterminator="\n",
    )


def main() -> int:
    args = _parser().parse_args()
    plan_path = Path(args.plan).resolve()
    work_dir = Path(args.work_dir).resolve()
    report_path = Path(args.report).resolve()
    work_dir.mkdir(parents=True, exist_ok=True)
    plan = json.loads(plan_path.read_text(encoding="utf-8"))
    if plan.get("scope") != "g6c-s6-structural-adequacy-plan":
        raise ValueError("worker received an invalid G6c-S6 plan")
    paths = {
        name: Path(value).resolve()
        for name, value in (plan.get("input_paths") or {}).items()
    }
    observed_hashes = {name: sha256_file(path) for name, path in paths.items()}
    if observed_hashes != plan.get("input_sha256"):
        raise ValueError("G6c-S6 worker input checksum mismatch")

    study31_config = plan.get("study31") or {}
    contact_config = study31_config.get("contact") or {}
    stride_config = study31_config.get("stride") or {}
    ground = _numeric_storage(paths["ground_reaction"])
    force_column = str(study31_config.get("vertical_force_column", "")).lower()
    if force_column not in ground:
        raise ValueError("G6c-S6 ground reaction lacks the frozen vertical force")
    contact_report = detect_sustained_contacts(
        ground["time"].to_numpy(float),
        ground[force_column].to_numpy(float),
        threshold_n=float(contact_config["threshold_n"]),
        minimum_duration_s=float(contact_config["minimum_duration_s"]),
        cutoff_hz=float(contact_config["lowpass_cutoff_hz"]),
        filter_order=int(contact_config["lowpass_order"]),
        window_s=study31_config["analysis_window_s"],
    )
    strides = build_stride_intervals(
        contact_report["contacts"],
        minimum_stride_s=float(stride_config["minimum_duration_s"]),
        maximum_stride_s=float(stride_config["maximum_duration_s"]),
    )

    actuation = _numeric_storage(paths["rra_actuation"])
    moment_columns = [
        str(value).lower()
        for value in study31_config.get("residual_moment_columns", [])
    ]
    if len(moment_columns) != 3 or any(
        column not in actuation for column in moment_columns
    ):
        raise ValueError("G6c-S6 RRA actuation lacks residual moment columns")
    study31, study31_rows = summarize_study31_strides(
        actuation["time"].to_numpy(float),
        actuation[moment_columns].to_numpy(float),
        strides,
        phase_points=int(stride_config["phase_points"]),
        sample_interval_s=float(stride_config["sample_interval_s"]),
    )
    study31["contact_detection"] = contact_report
    study31["actuation_sha256"] = observed_hashes["rra_actuation"]
    study31["ground_reaction_sha256"] = observed_hashes["ground_reaction"]
    study31_path = work_dir / "g6c_s6_study31_stride_requirements.csv"
    _write_rows(study31_rows, study31_path)

    study1_config = plan.get("study1_observational_control") or {}
    c3d_report, markers, study1_time = inspect_full_body_c3d(
        paths["study1_c3d"],
        marker_groups=study1_config.get("marker_groups") or {},
        calculated_markers=study1_config.get("calculated_markers") or [],
        minimum_valid_fraction=float(
            plan["thresholds"]["minimum_marker_valid_fraction"]
        ),
        expected_analog_channels=study1_config.get(
            "expected_force_moment_analog_channels"
        )
        or [],
        maximum_platform_roundtrip_error_nmm=float(
            plan["thresholds"]["maximum_force_platform_roundtrip_error_nmm"]
        ),
    )
    proxy_config = study1_config.get("upper_limb_proxy") or {}
    reference = proxy_config.get("reference_model") or {}
    proxy, study1_rows = compute_upper_limb_orbital_proxy(
        study1_time,
        markers,
        subject_mass_kg=float(plan["study1_subject_mass_kg"]),
        reference_total_mass_kg=float(reference["total_mass_kg"]),
        reference_segment_masses_kg=reference["one_side_segment_masses_kg"],
        cutoff_hz=float(proxy_config["lowpass_cutoff_hz"]),
        filter_order=int(proxy_config["lowpass_order"]),
        edge_exclusion_s=float(proxy_config["edge_exclusion_s"]),
    )
    proxy["reference_model"] = reference
    study1 = dict(c3d_report)
    study1["upper_limb_proxy"] = proxy
    study1["coordinate_correspondence"] = study1_config.get(
        "coordinate_correspondence"
    )
    study1["paired_rra_requirements"] = study1_config.get(
        "paired_rra_requirements"
    )
    study1["subject_matched_static_calibration_ready"] = False
    study1["articulated_and_arm_lumped_model_pair_ready"] = False
    study1["paired_full_body_rra_ready"] = bool(
        study1.get("external_load_roundtrip_ready")
        and study1["subject_matched_static_calibration_ready"]
        and study1["articulated_and_arm_lumped_model_pair_ready"]
    )
    study1_path = work_dir / "g6c_s6_study1_upper_limb_proxy.csv"
    _write_rows(study1_rows, study1_path)

    diagnosis = classify_structural_adequacy(
        study31,
        study1,
        thresholds=plan.get("thresholds") or {},
    )
    artifacts = {
        "plan_sha256": sha256_file(plan_path),
        "study31_stride_requirements_sha256": sha256_file(study31_path),
        "study1_upper_limb_proxy_sha256": sha256_file(study1_path),
    }
    worker = {
        "schema_version": "1.0.0",
        "scope": "g6c-s6-structural-adequacy-worker",
        "input_sha256": observed_hashes,
        "prior_result_sha256": plan.get("prior_result_sha256"),
        "baseline_actuation_verified": (
            observed_hashes["rra_actuation"]
            == plan["input_sha256"]["rra_actuation"]
        ),
        "contact_selection_residual_blind": (
            contact_report.get("residual_blind") is True
            and contact_report.get("selection_input")
            == "vertical-ground-reaction-force-only"
        ),
        "study31_analysis_complete": bool(study31.get("stride_count", 0)),
        "study1_analysis_complete": bool(
            study1.get("full_body_markers_ready")
            and (study1.get("upper_limb_proxy") or {}).get(
                "retained_sample_count", 0
            )
        ),
        "artifacts_complete": all(
            len(value) == 64 for value in artifacts.values()
        ),
        "study31": study31,
        "study1": study1,
        "diagnosis": diagnosis,
        "artifacts": artifacts,
    }
    report_path.parent.mkdir(parents=True, exist_ok=True)
    report_path.write_text(
        json.dumps(worker, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
