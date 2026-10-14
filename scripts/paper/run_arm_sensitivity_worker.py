#!/usr/bin/env python3
"""Run the checksum-bound G6c-S6d bounded arm sensitivity worker."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

import pandas as pd


ROOT = Path(__file__).resolve().parents[2]
if str(ROOT / "src") not in sys.path:
    sys.path.insert(0, str(ROOT / "src"))

from harmo.arm_sensitivity import (  # noqa: E402
    classify_bounded_sensitivity,
    run_bounded_harmonic_sensitivity,
    write_rows,
)
from harmo.dynamic_calibration import sha256_file  # noqa: E402


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
    if plan.get("scope") != "g6c-s6d-bounded-arm-sensitivity-plan":
        raise ValueError("worker received an invalid G6c-S6d plan")
    paths = {
        name: Path(str(value)).resolve()
        for name, value in (plan.get("input_paths") or {}).items()
    }
    observed_hashes = {name: sha256_file(path) for name, path in paths.items()}
    if observed_hashes != plan.get("input_sha256"):
        raise ValueError("G6c-S6d worker input checksum mismatch")
    table = pd.read_csv(paths["study31_stride_requirements"])
    grid = plan.get("grid") or {}
    columns = plan.get("columns") or {}
    summary, cell_rows, waveform_rows = run_bounded_harmonic_sensitivity(
        table,
        strides=plan.get("strides") or [],
        reference_peak_to_peak_nms=float(
            plan["reference_peak_to_peak_nms"]
        ),
        amplitude_multipliers=grid.get("amplitude_multipliers") or [],
        phase_shifts_deg=grid.get("phase_shifts_deg") or [],
        phase_points=int(columns["phase_points"]),
        momentum_column=str(columns["momentum"]),
        torque_column=str(columns["torque"]),
    )
    diagnosis = classify_bounded_sensitivity(
        summary,
        descriptive_thresholds=plan.get("descriptive_thresholds") or {},
    )
    cells_path = work_dir / "g6c_s6d_bounded_arm_cells.csv"
    waveform_path = work_dir / "g6c_s6d_bounded_arm_waveforms.csv"
    write_rows(
        cell_rows,
        cells_path,
        [
            "cell_id",
            "amplitude_multiplier",
            "phase_shift_deg",
            "candidate_peak_to_peak_nms",
            "stride_id",
            "momentum_unexplained_rms_ratio",
            "torque_unexplained_rms_ratio",
            "momentum_rms_relative_reduction",
            "torque_rms_relative_reduction",
            "momentum_waveform_correlation",
            "torque_waveform_correlation",
            "equal_weight_squared_ratio_objective",
        ],
    )
    write_rows(
        waveform_rows,
        waveform_path,
        [
            "cell_id",
            "amplitude_multiplier",
            "phase_shift_deg",
            "stride_id",
            "phase_percent",
            "required_momentum_nms",
            "candidate_momentum_nms",
            "unexplained_momentum_nms",
            "required_torque_nm",
            "candidate_torque_nm",
            "unexplained_torque_nm",
        ],
    )
    artifacts = {
        "plan_sha256": sha256_file(plan_path),
        "cells_sha256": sha256_file(cells_path),
        "waveforms_sha256": sha256_file(waveform_path),
    }
    worker = {
        "schema_version": "1.0.0",
        "scope": "g6c-s6d-bounded-arm-sensitivity-worker",
        "input_sha256": observed_hashes,
        "prior_result_sha256": plan.get("prior_result_sha256"),
        "summary": summary,
        "diagnosis": diagnosis,
        "analysis_complete": True,
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
