#!/usr/bin/env python3
"""Run the checksum-bound G6c-S6b paired structural counterfactual."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

import pandas as pd


ROOT = Path(__file__).resolve().parents[2]
if str(ROOT / "src") not in sys.path:
    sys.path.insert(0, str(ROOT / "src"))

from harmo.dynamic_calibration import sha256_file  # noqa: E402
from harmo.structural_adequacy import inspect_full_body_c3d  # noqa: E402
from harmo.structural_contrast import (  # noqa: E402
    classify_structural_contrast,
    paired_upper_limb_structural_contrast,
)


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--plan", required=True)
    parser.add_argument("--work-dir", required=True)
    parser.add_argument("--report", required=True)
    return parser


def main() -> int:
    args = _parser().parse_args()
    plan_path = Path(args.plan).resolve()
    work_dir = Path(args.work_dir).resolve()
    report_path = Path(args.report).resolve()
    work_dir.mkdir(parents=True, exist_ok=True)
    plan = json.loads(plan_path.read_text(encoding="utf-8"))
    if plan.get("scope") != "g6c-s6b-structural-contrast-plan":
        raise ValueError("worker received an invalid G6c-S6b plan")
    paths = {
        name: Path(value).resolve()
        for name, value in (plan.get("input_paths") or {}).items()
    }
    observed_hashes = {name: sha256_file(path) for name, path in paths.items()}
    if observed_hashes != plan.get("input_sha256"):
        raise ValueError("G6c-S6b worker input checksum mismatch")

    study1 = plan.get("study1") or {}
    thresholds = plan.get("thresholds") or {}
    source_report, markers, time = inspect_full_body_c3d(
        paths["study1_c3d"],
        marker_groups=study1.get("marker_groups") or {},
        calculated_markers=study1.get("calculated_markers") or [],
        minimum_valid_fraction=float(
            thresholds["minimum_marker_valid_fraction"]
        ),
        expected_analog_channels=[],
        maximum_platform_roundtrip_error_nmm=float(
            thresholds["maximum_force_platform_roundtrip_error_nmm"]
        ),
    )
    reference = study1.get("reference_model") or {}
    contrast, rows = paired_upper_limb_structural_contrast(
        time,
        markers,
        subject_mass_kg=float(plan["study1_subject_mass_kg"]),
        reference_total_mass_kg=float(reference["total_mass_kg"]),
        reference_segment_masses_kg=reference[
            "one_side_segment_masses_kg"
        ],
        reference_frame_rule=str(study1["reference_frame_rule"]),
        cutoff_hz=float(study1["lowpass_cutoff_hz"]),
        filter_order=int(study1["lowpass_order"]),
        edge_exclusion_s=float(study1["edge_exclusion_s"]),
    )
    study31 = plan.get("study31_descriptive_reference") or {}
    contrast_h = float(
        contrast["contrast"][
            "angular_momentum_peak_to_peak_nms"
        ][2]
    )
    contrast_torque = float(
        contrast["contrast"]["orbital_torque_axis_rms_nm"][2]
    )
    contrast["study31_descriptive_reference"] = {
        **study31,
        "study1_contrast_to_study31_required_momentum_ratio": (
            contrast_h
            / float(
                study31[
                    "vertical_required_angular_momentum_peak_to_peak_mean_nms"
                ]
            )
        ),
        "study1_contrast_to_study31_periodic_moment_ratio": (
            contrast_torque
            / float(study31["vertical_periodic_moment_rms_mean_nm"])
        ),
        "interpretation": (
            "different-subject-and-activity-descriptive-only-not-causal"
        ),
    }
    diagnosis = classify_structural_contrast(
        contrast, thresholds=thresholds
    )
    table_path = work_dir / "g6c_s6b_paired_structural_contrast.csv"
    pd.DataFrame(rows).to_csv(
        table_path,
        index=False,
        float_format="%.12g",
        lineterminator="\n",
    )
    artifacts = {
        "plan_sha256": sha256_file(plan_path),
        "paired_structural_contrast_sha256": sha256_file(table_path),
    }
    worker = {
        "schema_version": "1.0.0",
        "scope": "g6c-s6b-structural-contrast-worker",
        "input_sha256": observed_hashes,
        "prior_result_sha256": plan.get("prior_result_sha256"),
        "source_report": source_report,
        "source_traceability_ready": bool(
            source_report.get("full_body_markers_ready")
            and source_report.get("external_load_roundtrip_ready")
        ),
        "contrast": contrast,
        "diagnosis": diagnosis,
        "analysis_complete": bool(
            source_report.get("full_body_markers_ready")
            and source_report.get("external_load_roundtrip_ready")
            and contrast.get("processing", {}).get("retained_sample_count", 0)
        ),
        "artifacts_complete": all(
            len(value) == 64 for value in artifacts.values()
        ),
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
