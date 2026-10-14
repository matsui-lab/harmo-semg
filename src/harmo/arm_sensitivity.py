"""G6c-S6d bounded harmonic arm-kinematics sensitivity analysis."""

from __future__ import annotations

import csv
import json
import math
from pathlib import Path
from typing import Any, Dict, Mapping, Sequence

import numpy as np
import pandas as pd
import yaml

from harmo.dynamic_calibration import sha256_file


ARM_SENSITIVITY_SCHEMA_VERSION = "1.0.0"


def _load_yaml(path: Path) -> Dict[str, Any]:
    value = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    if not isinstance(value, dict):
        raise ValueError("G6c-S6d YAML root must be a mapping")
    return value


def _load_json(path: Path) -> Dict[str, Any]:
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise ValueError("G6c-S6d JSON root must be a mapping")
    return value


def _phase_rms(phase_fraction: np.ndarray, values: np.ndarray) -> float:
    return float(
        np.sqrt(
            np.trapz(values * values, phase_fraction)
            / (phase_fraction[-1] - phase_fraction[0])
        )
    )


def _phase_mean(phase_fraction: np.ndarray, values: np.ndarray) -> float:
    return float(
        np.trapz(values, phase_fraction)
        / (phase_fraction[-1] - phase_fraction[0])
    )


def _waveform_correlation(left: np.ndarray, right: np.ndarray) -> float:
    if float(np.std(left)) <= np.finfo(float).eps:
        return 0.0
    if float(np.std(right)) <= np.finfo(float).eps:
        return 0.0
    return float(np.corrcoef(left, right)[0, 1])


def harmonic_candidate(
    phase_fraction: np.ndarray,
    *,
    duration_s: float,
    reference_peak_to_peak_nms: float,
    amplitude_multiplier: float,
    phase_shift_deg: float,
) -> tuple[np.ndarray, np.ndarray]:
    """Create a zero-mean single-harmonic momentum and analytic torque."""
    phase = np.asarray(phase_fraction, dtype=float)
    if (
        phase.ndim != 1
        or phase.size < 8
        or np.any(np.diff(phase) <= 0)
        or not np.all(np.isfinite(phase))
    ):
        raise ValueError("G6c-S6d phase grid is invalid")
    if duration_s <= 0 or reference_peak_to_peak_nms <= 0:
        raise ValueError("G6c-S6d duration/reference amplitude is invalid")
    if amplitude_multiplier < 0 or not math.isfinite(amplitude_multiplier):
        raise ValueError("G6c-S6d amplitude multiplier is invalid")
    angle = 2.0 * np.pi * phase + np.deg2rad(float(phase_shift_deg))
    amplitude = 0.5 * reference_peak_to_peak_nms * amplitude_multiplier
    momentum = amplitude * np.sin(angle)
    momentum -= _phase_mean(phase, momentum)
    torque = amplitude * (2.0 * np.pi / duration_s) * np.cos(angle)
    return momentum, torque


def _validate_stride_table(
    table: pd.DataFrame,
    *,
    stride_ids: Sequence[str],
    phase_points: int,
    momentum_column: str,
    torque_column: str,
) -> Dict[str, Dict[str, np.ndarray]]:
    required = {"stride_id", "phase_percent", momentum_column, torque_column}
    if not required.issubset(table.columns):
        raise ValueError("G6c-S6d stride table is missing required columns")
    observed_ids = sorted(str(value) for value in table["stride_id"].unique())
    expected_ids = sorted(str(value) for value in stride_ids)
    if observed_ids != expected_ids:
        raise ValueError("G6c-S6d stride identities differ from the frozen result")
    output = {}
    expected_phase = np.linspace(0.0, 100.0, int(phase_points))
    for stride_id in expected_ids:
        subset = table.loc[
            table["stride_id"].astype(str) == stride_id
        ].sort_values("phase_percent")
        phase = subset["phase_percent"].to_numpy(dtype=float)
        momentum = subset[momentum_column].to_numpy(dtype=float)
        torque = subset[torque_column].to_numpy(dtype=float)
        if (
            phase.size != int(phase_points)
            or not np.allclose(phase, expected_phase, atol=1e-12, rtol=0.0)
            or not np.all(np.isfinite(momentum))
            or not np.all(np.isfinite(torque))
        ):
            raise ValueError("G6c-S6d stride grid or waveform is invalid")
        output[stride_id] = {
            "phase_fraction": phase / 100.0,
            "required_momentum_nms": momentum,
            "required_torque_nm": torque,
        }
    return output


def run_bounded_harmonic_sensitivity(
    stride_table: pd.DataFrame,
    *,
    strides: Sequence[Mapping[str, Any]],
    reference_peak_to_peak_nms: float,
    amplitude_multipliers: Sequence[float],
    phase_shifts_deg: Sequence[float],
    phase_points: int,
    momentum_column: str,
    torque_column: str,
) -> tuple[Dict[str, Any], list[Dict[str, Any]], list[Dict[str, Any]]]:
    """Evaluate every frozen harmonic cell and cross-stride selection."""
    stride_durations = {
        str(value["stride_id"]): float(value["duration_s"]) for value in strides
    }
    if len(stride_durations) < 2:
        raise ValueError("G6c-S6d requires at least two strides")
    data = _validate_stride_table(
        stride_table,
        stride_ids=list(stride_durations),
        phase_points=phase_points,
        momentum_column=momentum_column,
        torque_column=torque_column,
    )
    amplitudes = [float(value) for value in amplitude_multipliers]
    phases = [float(value) for value in phase_shifts_deg]
    if (
        not amplitudes
        or amplitudes[0] != 0.0
        or len(set(amplitudes)) != len(amplitudes)
        or any(value < 0 or not math.isfinite(value) for value in amplitudes)
        or not phases
        or len(set(phases)) != len(phases)
        or any(value < 0 or value >= 360 for value in phases)
    ):
        raise ValueError("G6c-S6d harmonic grid is invalid")

    cells = []
    waveform_rows = []
    for amplitude in amplitudes:
        cell_phases = [0.0] if amplitude == 0.0 else phases
        for phase_shift in cell_phases:
            cell_id = "amp-{:g}__phase-{:03d}".format(
                amplitude, int(round(phase_shift))
            )
            stride_metrics = []
            for stride_id in sorted(data):
                values = data[stride_id]
                phase = values["phase_fraction"]
                candidate_h, candidate_torque = harmonic_candidate(
                    phase,
                    duration_s=stride_durations[stride_id],
                    reference_peak_to_peak_nms=reference_peak_to_peak_nms,
                    amplitude_multiplier=amplitude,
                    phase_shift_deg=phase_shift,
                )
                required_h = values["required_momentum_nms"]
                required_torque = values["required_torque_nm"]
                unexplained_h = required_h - candidate_h
                unexplained_torque = required_torque - candidate_torque
                h_scale = _phase_rms(phase, required_h)
                torque_scale = _phase_rms(phase, required_torque)
                if h_scale <= 0 or torque_scale <= 0:
                    raise ValueError("G6c-S6d required waveform has zero RMS")
                h_ratio = _phase_rms(phase, unexplained_h) / h_scale
                torque_ratio = (
                    _phase_rms(phase, unexplained_torque) / torque_scale
                )
                objective = 0.5 * h_ratio**2 + 0.5 * torque_ratio**2
                metric = {
                    "stride_id": stride_id,
                    "momentum_unexplained_rms_ratio": float(h_ratio),
                    "torque_unexplained_rms_ratio": float(torque_ratio),
                    "momentum_rms_relative_reduction": float(1.0 - h_ratio),
                    "torque_rms_relative_reduction": float(1.0 - torque_ratio),
                    "momentum_waveform_correlation": _waveform_correlation(
                        required_h, candidate_h
                    ),
                    "torque_waveform_correlation": _waveform_correlation(
                        required_torque, candidate_torque
                    ),
                    "equal_weight_squared_ratio_objective": float(objective),
                }
                stride_metrics.append(metric)
                for index in range(phase.size):
                    waveform_rows.append(
                        {
                            "cell_id": cell_id,
                            "amplitude_multiplier": amplitude,
                            "phase_shift_deg": phase_shift,
                            "stride_id": stride_id,
                            "phase_percent": 100.0 * phase[index],
                            "required_momentum_nms": required_h[index],
                            "candidate_momentum_nms": candidate_h[index],
                            "unexplained_momentum_nms": unexplained_h[index],
                            "required_torque_nm": required_torque[index],
                            "candidate_torque_nm": candidate_torque[index],
                            "unexplained_torque_nm": unexplained_torque[index],
                        }
                    )
            cells.append(
                {
                    "cell_id": cell_id,
                    "amplitude_multiplier": amplitude,
                    "phase_shift_deg": phase_shift,
                    "candidate_peak_to_peak_nms": (
                        amplitude * reference_peak_to_peak_nms
                    ),
                    "mean_equal_weight_squared_ratio_objective": float(
                        np.mean(
                            [
                                value[
                                    "equal_weight_squared_ratio_objective"
                                ]
                                for value in stride_metrics
                            ]
                        )
                    ),
                    "strides": stride_metrics,
                }
            )

    def _metric(cell: Mapping[str, Any], stride_id: str) -> Mapping[str, Any]:
        return next(
            value for value in cell["strides"] if value["stride_id"] == stride_id
        )

    def _ranking_key(cell: Mapping[str, Any], stride_id: str | None = None):
        objective = (
            float(cell["mean_equal_weight_squared_ratio_objective"])
            if stride_id is None
            else float(
                _metric(cell, stride_id)[
                    "equal_weight_squared_ratio_objective"
                ]
            )
        )
        return (
            objective,
            float(cell["amplitude_multiplier"]),
            float(cell["phase_shift_deg"]),
        )

    pooled_best = min(cells, key=lambda value: _ranking_key(value))
    cross_stride = []
    stride_ids = sorted(data)
    for train_id in stride_ids:
        selected = min(cells, key=lambda value: _ranking_key(value, train_id))
        for heldout_id in stride_ids:
            if heldout_id == train_id:
                continue
            cross_stride.append(
                {
                    "training_stride_id": train_id,
                    "heldout_stride_id": heldout_id,
                    "selected_cell_id": selected["cell_id"],
                    "amplitude_multiplier": selected["amplitude_multiplier"],
                    "phase_shift_deg": selected["phase_shift_deg"],
                    "training_metrics": dict(_metric(selected, train_id)),
                    "heldout_metrics": dict(_metric(selected, heldout_id)),
                }
            )
    heldout_h = [
        value["heldout_metrics"]["momentum_rms_relative_reduction"]
        for value in cross_stride
    ]
    heldout_torque = [
        value["heldout_metrics"]["torque_rms_relative_reduction"]
        for value in cross_stride
    ]
    selected_amplitudes = [
        float(value["amplitude_multiplier"]) for value in cross_stride
    ]
    selected_phases = [float(value["phase_shift_deg"]) for value in cross_stride]
    phase_span = max(selected_phases) - min(selected_phases)
    circular_phase_span = min(phase_span, 360.0 - phase_span)
    summary = {
        "method": (
            "bounded-single-harmonic-grid-with-leave-one-stride-out-selection"
        ),
        "cell_count": len(cells),
        "stride_count": len(stride_ids),
        "reference_peak_to_peak_nms": float(reference_peak_to_peak_nms),
        "pooled_best_cell": pooled_best,
        "cross_stride_evaluations": cross_stride,
        "heldout_momentum_rms_relative_reduction_mean": float(
            np.mean(heldout_h)
        ),
        "heldout_momentum_rms_relative_reduction_minimum": float(
            np.min(heldout_h)
        ),
        "heldout_torque_rms_relative_reduction_mean": float(
            np.mean(heldout_torque)
        ),
        "heldout_torque_rms_relative_reduction_minimum": float(
            np.min(heldout_torque)
        ),
        "selected_amplitude_span": float(
            max(selected_amplitudes) - min(selected_amplitudes)
        ),
        "selected_circular_phase_span_deg": float(circular_phase_span),
        "all_cells_retained": True,
        "causal_test": False,
        "rra_executed": False,
    }
    cell_rows = []
    for cell in cells:
        for metric in cell["strides"]:
            cell_rows.append(
                {
                    "cell_id": cell["cell_id"],
                    "amplitude_multiplier": cell["amplitude_multiplier"],
                    "phase_shift_deg": cell["phase_shift_deg"],
                    "candidate_peak_to_peak_nms": cell[
                        "candidate_peak_to_peak_nms"
                    ],
                    **metric,
                }
            )
    return summary, cell_rows, waveform_rows


def classify_bounded_sensitivity(
    summary: Mapping[str, Any], *, descriptive_thresholds: Mapping[str, Any]
) -> Dict[str, Any]:
    h_threshold = float(
        descriptive_thresholds.get(
            "momentum_relative_reduction_reference", math.inf
        )
    )
    torque_threshold = float(
        descriptive_thresholds.get(
            "torque_relative_reduction_reference", math.inf
        )
    )
    amplitude_span_limit = float(
        descriptive_thresholds.get(
            "maximum_selected_amplitude_span", -math.inf
        )
    )
    phase_span_limit = float(
        descriptive_thresholds.get(
            "maximum_selected_circular_phase_span_deg", -math.inf
        )
    )
    checks = {
        "all_cells_retained": summary.get("all_cells_retained") is True,
        "cross_stride_momentum_reference": float(
            summary.get(
                "heldout_momentum_rms_relative_reduction_minimum", -math.inf
            )
        )
        >= h_threshold,
        "cross_stride_torque_reference": float(
            summary.get(
                "heldout_torque_rms_relative_reduction_minimum", -math.inf
            )
        )
        >= torque_threshold,
        "selected_amplitude_stability": float(
            summary.get("selected_amplitude_span", math.inf)
        )
        <= amplitude_span_limit,
        "selected_phase_stability": float(
            summary.get("selected_circular_phase_span_deg", math.inf)
        )
        <= phase_span_limit,
    }
    if (
        checks["cross_stride_momentum_reference"]
        and checks["cross_stride_torque_reference"]
    ):
        pattern = "momentum-and-torque-compatible-with-bounded-harmonic"
    elif checks["cross_stride_momentum_reference"]:
        pattern = "momentum-compatible-torque-not-compatible"
    else:
        pattern = "limited-bounded-harmonic-compatibility"
    return {
        "descriptive_checks": checks,
        "descriptive_pattern": pattern,
        "causal_structural_attribution": False,
        "full_body_rra_authorized": False,
        "mechanics_closed": False,
        "validation_pass": False,
    }


def build_arm_sensitivity_plan(
    protocol_path: Path,
    s6_result_path: Path,
    s6b_result_path: Path,
    s6c_result_path: Path,
    s6_work_dir: Path,
) -> Dict[str, Any]:
    """Build the checksum-bound G6c-S6d exploratory sensitivity plan."""
    protocol_path = protocol_path.resolve()
    s6_result_path = s6_result_path.resolve()
    s6b_result_path = s6b_result_path.resolve()
    s6c_result_path = s6c_result_path.resolve()
    s6_work_dir = s6_work_dir.resolve()
    protocol = _load_yaml(protocol_path)
    if str(protocol.get("schema_version")) != ARM_SENSITIVITY_SCHEMA_VERSION:
        raise ValueError("unsupported G6c-S6d protocol version")
    s6 = _load_json(s6_result_path)
    s6b = _load_json(s6b_result_path)
    s6c = _load_json(s6c_result_path)
    if (
        s6.get("scope") != "g6c-s6-structural-adequacy-audit"
        or s6.get("diagnostic_complete") is not True
    ):
        raise ValueError("G6c-S6d requires the complete S6 result")
    if (
        s6b.get("scope") != "g6c-s6b-structural-contrast-audit"
        or s6b.get("material_effect_detected") is not True
    ):
        raise ValueError("G6c-S6d requires the material S6b contrast")
    if (
        s6c.get("scope") != "g6c-s6c-structural-identifiability-audit"
        or s6c.get("diagnostic_complete") is not True
        or s6c.get("full_body_rra_authorized") is not False
    ):
        raise ValueError("G6c-S6d requires the retained S6c block")
    s6c_hashes = (s6c.get("worker") or {}).get("input_sha256") or {}
    if (
        str(s6c_hashes.get("s6_result", "")) != sha256_file(s6_result_path)
        or str(s6c_hashes.get("s6b_result", ""))
        != sha256_file(s6b_result_path)
    ):
        raise ValueError("G6c-S6d prior-result chain is broken")
    design = protocol.get("design") or {}
    table_path = s6_work_dir / str(
        design.get("study31_stride_requirements_artifact", "")
    )
    if not table_path.is_file():
        raise ValueError("G6c-S6d S6 stride table is missing")
    expected_table_hash = str(
        ((s6.get("worker") or {}).get("artifacts") or {}).get(
            "study31_stride_requirements_sha256", ""
        )
    )
    if sha256_file(table_path) != expected_table_hash:
        raise ValueError("G6c-S6d S6 stride table hash mismatch")
    reference = (
        ((s6b.get("worker") or {}).get("contrast") or {}).get("contrast") or {}
    )
    reference_p2p = float(
        (reference.get("angular_momentum_peak_to_peak_nms") or [math.nan] * 3)[
            2
        ]
    )
    if not math.isfinite(reference_p2p) or reference_p2p <= 0:
        raise ValueError("G6c-S6d S6b reference amplitude is invalid")
    paths = {
        "s6_result": s6_result_path,
        "s6b_result": s6b_result_path,
        "s6c_result": s6c_result_path,
        "study31_stride_requirements": table_path.resolve(),
    }
    study31 = (s6.get("worker") or {}).get("study31") or {}
    return {
        "schema_version": ARM_SENSITIVITY_SCHEMA_VERSION,
        "scope": "g6c-s6d-bounded-arm-sensitivity-plan",
        "protocol_sha256": sha256_file(protocol_path),
        "prior_result_sha256": {
            "s6": sha256_file(s6_result_path),
            "s6b": sha256_file(s6b_result_path),
            "s6c": sha256_file(s6c_result_path),
        },
        "input_paths": {name: str(path) for name, path in paths.items()},
        "input_sha256": {name: sha256_file(path) for name, path in paths.items()},
        "strides": [
            {
                "stride_id": str(value["stride_id"]),
                "duration_s": float(value["duration_s"]),
            }
            for value in study31.get("strides", [])
        ],
        "reference_peak_to_peak_nms": reference_p2p,
        "grid": dict(design.get("grid") or {}),
        "columns": dict(design.get("columns") or {}),
        "descriptive_thresholds": dict(
            protocol.get("descriptive_thresholds") or {}
        ),
        "post_outcome_status": str(protocol.get("post_outcome_status", "")),
        "claim_boundary": str(protocol.get("claim_boundary", "")),
    }


def evaluate_arm_sensitivity(
    plan: Mapping[str, Any], worker: Mapping[str, Any]
) -> Dict[str, Any]:
    checks = {
        "worker_scope": worker.get("scope")
        == "g6c-s6d-bounded-arm-sensitivity-worker",
        "worker_inputs": worker.get("input_sha256") == plan.get("input_sha256"),
        "prior_results": worker.get("prior_result_sha256")
        == plan.get("prior_result_sha256"),
        "analysis_complete": worker.get("analysis_complete") is True,
        "artifacts_complete": worker.get("artifacts_complete") is True,
    }
    diagnostic_complete = all(checks.values())
    return {
        "schema_version": ARM_SENSITIVITY_SCHEMA_VERSION,
        "scope": "g6c-s6d-bounded-arm-sensitivity-audit",
        "pass": False,
        "diagnostic_complete": diagnostic_complete,
        "causal_structural_attribution": False,
        "full_body_rra_authorized": False,
        "mechanics_closed": False,
        "checks": checks,
        "post_outcome_status": str(plan.get("post_outcome_status", "")),
        "worker": dict(worker),
        "scientific_claim_boundary": str(plan.get("claim_boundary", "")),
        "next_gate": (
            "stop-g6c-structural-causal-branch-requires-new-observed-data"
            if diagnostic_complete
            else "blocked-at-g6c-s6d-analysis"
        ),
    }


def write_arm_sensitivity(result: Mapping[str, Any], output: Path) -> None:
    output = output.resolve()
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(
        json.dumps(result, indent=2, sort_keys=True) + "\n", encoding="utf-8"
    )


def write_rows(
    rows: Sequence[Mapping[str, Any]], path: Path, fieldnames: Sequence[str]
) -> None:
    path = path.resolve()
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8", newline="") as stream:
        writer = csv.DictWriter(stream, fieldnames=list(fieldnames))
        writer.writeheader()
        for row in rows:
            writer.writerow({name: row.get(name) for name in fieldnames})
