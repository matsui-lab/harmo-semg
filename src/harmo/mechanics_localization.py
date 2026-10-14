"""G6c-S2 localization of synchronization, kinematics, and model effects."""

from __future__ import annotations

import json
import math
import xml.etree.ElementTree as ET
from pathlib import Path
from typing import Any, Dict, Mapping, Sequence

import numpy as np
import yaml

from harmo.dynamic_calibration import sha256_file, validate_public_source_bundle
from harmo.mechanics_audit import canonical_sha256, trc_time_grid
from harmo.whole_body_balance import (
    calculate_whole_body_balance,
    time_derivative,
)


MECHANICS_LOCALIZATION_SCHEMA_VERSION = "1.0.0"


def _load_yaml(path: Path) -> Dict[str, Any]:
    value = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    if not isinstance(value, dict):
        raise ValueError("G6c-S2 YAML root must be a mapping")
    return value


def shift_wrench_on_grid(
    time_s: Sequence[float],
    force_n: Sequence[Sequence[float]],
    moment_origin_nm: Sequence[Sequence[float]],
    lag_s: float,
) -> Dict[str, np.ndarray]:
    """Sample a prefiltered wrench at ``time + lag`` without extrapolation search."""
    time = np.asarray(time_s, dtype=float)
    force = np.asarray(force_n, dtype=float)
    moment = np.asarray(moment_origin_nm, dtype=float)
    if force.shape != moment.shape or force.shape != (time.size, 3):
        raise ValueError("wrench arrays must have shape (n_time, 3)")
    if np.any(np.diff(time) <= 0) or not all(
        np.all(np.isfinite(value)) for value in (time, force, moment)
    ):
        raise ValueError("wrench time and values must be finite and ordered")
    # The full shifted vector can extend beyond the sampled range. G6c-S2
    # evaluates only predeclared interior windows; that invariant is checked
    # in ``synchronization_cross_validation`` before this helper is called.
    shifted_time = time + float(lag_s)
    return {
        "force_n": np.column_stack(
            [np.interp(shifted_time, time, force[:, axis]) for axis in range(3)]
        ),
        "moment_origin_nm": np.column_stack(
            [np.interp(shifted_time, time, moment[:, axis]) for axis in range(3)]
        ),
    }


def _balance_summary(value: Dict[str, Any]) -> Dict[str, Any]:
    value = dict(value)
    value.pop("timeseries", None)
    return value


def synchronization_cross_validation(
    kinematics: Mapping[str, Any],
    force_n: Sequence[Sequence[float]],
    moment_origin_nm: Sequence[Sequence[float]],
    *,
    calibration_windows_s: Sequence[Sequence[float]],
    target_window_s: Sequence[float],
    minimum_lag_s: float,
    maximum_lag_s: float,
    lag_step_s: float,
    maximum_calibration_disagreement_s: float,
    minimum_target_force_reduction: float,
    force_fraction_threshold: float,
    moment_fraction_threshold: float,
    external_lowpass_cutoff_hz: float,
    lowpass_order: int,
) -> Dict[str, Any]:
    """Select lag outside the target window, then assess it on the target."""
    if len(calibration_windows_s) != 2:
        raise ValueError("G6c-S2 requires exactly two independent calibration windows")
    step = float(lag_step_s)
    if step <= 0 or maximum_lag_s < minimum_lag_s:
        raise ValueError("invalid synchronization lag grid")
    count = int(round((float(maximum_lag_s) - float(minimum_lag_s)) / step)) + 1
    lags = float(minimum_lag_s) + np.arange(count, dtype=float) * step
    if abs(float(lags[-1]) - float(maximum_lag_s)) > 1e-12:
        raise ValueError("synchronization lag range is not divisible by its step")
    time = np.asarray(kinematics["time_s"], dtype=float)
    force = np.asarray(force_n, dtype=float)
    moment = np.asarray(moment_origin_nm, dtype=float)
    evaluation_windows = [
        [float(value) for value in window]
        for window in list(calibration_windows_s) + [target_window_s]
    ]
    for window in evaluation_windows:
        if len(window) != 2 or window[0] >= window[1]:
            raise ValueError("synchronization evaluation windows must be ordered")
        if (
            window[0] + float(minimum_lag_s) < float(time[0]) - 1e-12
            or window[1] + float(maximum_lag_s) > float(time[-1]) + 1e-12
        ):
            raise ValueError(
                "synchronization evaluation window and lag grid require extrapolation"
            )
    common = {
        "mass_kg": float(kinematics["mass_kg"]),
        "gravity_m_s2": kinematics["gravity_m_s2"],
        "vertical_axis": int(kinematics["vertical_axis"]),
        "external_lowpass_cutoff_hz": float(external_lowpass_cutoff_hz),
        "lowpass_order": int(lowpass_order),
        "external_preprocessed": True,
    }

    scans = []
    selected_lags = []
    for window in calibration_windows_s:
        rows = []
        for lag in lags:
            shifted = shift_wrench_on_grid(time, force, moment, float(lag))
            value = calculate_whole_body_balance(
                time,
                kinematics["com_position_m"],
                kinematics["central_angular_momentum_nms"],
                shifted["force_n"],
                shifted["moment_origin_nm"],
                evaluation_window_s=window,
                **common,
            )
            rows.append(
                {
                    "lag_s": float(lag),
                    "residual_force_fraction": float(value["residual_force_fraction"]),
                    "residual_moment_fraction": float(value["residual_moment_fraction"]),
                }
            )
        selected = min(
            rows,
            key=lambda row: (
                row["residual_force_fraction"],
                abs(row["lag_s"]),
                row["lag_s"],
            ),
        )
        selected_lags.append(float(selected["lag_s"]))
        scans.append(
            {
                "calibration_window_s": [float(value) for value in window],
                "selection_metric": "residual_force_fraction",
                "selected_lag_s": float(selected["lag_s"]),
                "selected_force_fraction": float(selected["residual_force_fraction"]),
                "scan": rows,
            }
        )
    disagreement = abs(selected_lags[0] - selected_lags[1])
    stable = disagreement <= float(maximum_calibration_disagreement_s) + 1e-12
    median_lag = float(np.median(selected_lags))
    selected_lag = float(lags[int(np.argmin(np.abs(lags - median_lag)))])

    def target_at(lag: float) -> Dict[str, Any]:
        shifted = shift_wrench_on_grid(time, force, moment, lag)
        return _balance_summary(
            calculate_whole_body_balance(
                time,
                kinematics["com_position_m"],
                kinematics["central_angular_momentum_nms"],
                shifted["force_n"],
                shifted["moment_origin_nm"],
                evaluation_window_s=target_window_s,
                **common,
            )
        )

    baseline = target_at(0.0)
    candidate = target_at(selected_lag)
    baseline_force = float(baseline["residual_force_fraction"])
    candidate_force = float(candidate["residual_force_fraction"])
    reduction = (
        0.0 if baseline_force == 0.0 else (baseline_force - candidate_force) / baseline_force
    )
    synchronization_sufficient = (
        stable
        and reduction >= float(minimum_target_force_reduction)
        and candidate_force <= float(force_fraction_threshold)
    )
    return {
        "selection_uses_target_window": False,
        "lag_grid_s": {
            "minimum": float(lags[0]),
            "maximum": float(lags[-1]),
            "step": step,
            "count": int(lags.size),
        },
        "calibration_scans": scans,
        "calibration_selected_lags_s": selected_lags,
        "calibration_lag_disagreement_s": disagreement,
        "maximum_calibration_disagreement_s": float(
            maximum_calibration_disagreement_s
        ),
        "calibration_stable": bool(stable),
        "selected_lag_s": selected_lag,
        "target_baseline": baseline,
        "target_selected_lag": candidate,
        "target_force_relative_reduction": reduction,
        "minimum_target_force_relative_reduction": float(
            minimum_target_force_reduction
        ),
        "target_selected_force_closed": candidate_force
        <= float(force_fraction_threshold),
        "target_selected_moment_closed": float(candidate["residual_moment_fraction"])
        <= float(moment_fraction_threshold),
        "synchronization_sufficient": bool(synchronization_sufficient),
        "interpretation": (
            "constant-lag synchronization explains the force nonclosure"
            if synchronization_sufficient
            else "constant-lag synchronization is not sufficient under cross-validation"
        ),
    }


def optimal_scalar_mass_diagnostic(
    time_s: Sequence[float],
    com_position_m: Sequence[Sequence[float]],
    filtered_external_force_n: Sequence[Sequence[float]],
    *,
    recorded_mass_kg: float,
    gravity_m_s2: Sequence[float],
    evaluation_window_s: Sequence[float],
    maximum_relative_mass_change: float,
    force_fraction_threshold: float,
) -> Dict[str, Any]:
    """Fit one scalar mass diagnostically; never apply it to the model."""
    time = np.asarray(time_s, dtype=float)
    com = np.asarray(com_position_m, dtype=float)
    force = np.asarray(filtered_external_force_n, dtype=float)
    gravity = np.asarray(gravity_m_s2, dtype=float)
    start, end = [float(value) for value in evaluation_window_s]
    mask = (time >= start - 1e-10) & (time <= end + 1e-10)
    acceleration = time_derivative(time, time_derivative(time, com))
    design = acceleration[mask] - gravity[None, :]
    observed = force[mask]
    denominator = float(np.sum(np.square(design)))
    if denominator <= 0:
        raise ValueError("scalar-mass diagnostic has zero design energy")
    fitted = float(np.sum(design * observed) / denominator)
    if not math.isfinite(fitted) or fitted <= 0:
        raise ValueError("scalar-mass diagnostic produced a nonpositive mass")
    residual = fitted * design - observed
    force_rms = float(np.sqrt(np.mean(np.sum(np.square(residual), axis=1))))
    fraction = force_rms / (fitted * float(np.linalg.norm(gravity)))
    relative_change = (fitted - float(recorded_mass_kg)) / float(recorded_mass_kg)
    plausible = abs(relative_change) <= float(maximum_relative_mass_change)
    sufficient = plausible and fraction <= float(force_fraction_threshold)
    return {
        "recorded_mass_kg": float(recorded_mass_kg),
        "least_squares_mass_kg": fitted,
        "relative_mass_change": relative_change,
        "maximum_plausible_relative_mass_change": float(maximum_relative_mass_change),
        "plausible": bool(plausible),
        "fitted_force_rms_n": force_rms,
        "fitted_force_fraction": fraction,
        "force_closed": fraction <= float(force_fraction_threshold),
        "scalar_mass_sufficient": bool(sufficient),
        "auto_apply": False,
        "selection": "diagnostic-least-squares-on-target-not-accepted-model-update",
    }


def factorial_effect_localization(
    balances: Mapping[str, Mapping[str, Any]],
    *,
    force_fraction_threshold: float,
    moment_fraction_threshold: float,
    minimum_relative_reduction: float,
) -> Dict[str, Any]:
    """Interpret the original/adjusted-model by IK/RRA-kinematics 2x2 design."""
    required = {
        "original_model_ik",
        "original_model_rra_kinematics",
        "adjusted_model_ik",
        "adjusted_model_rra_kinematics",
    }
    missing = sorted(required - set(balances))
    if missing:
        raise ValueError("factorial mechanics result lacks cells: {}".format(missing))

    def outcome(metric: str, threshold: float) -> Dict[str, Any]:
        baseline = float(balances["original_model_ik"][metric])
        kinematics = float(balances["original_model_rra_kinematics"][metric])
        model = float(balances["adjusted_model_ik"][metric])
        combined = float(balances["adjusted_model_rra_kinematics"][metric])
        values = {
            "baseline": baseline,
            "kinematics_only": kinematics,
            "model_only": model,
            "combined": combined,
        }
        reductions = {
            key: (baseline - value) / baseline if baseline != 0 else 0.0
            for key, value in values.items()
            if key != "baseline"
        }
        closes = {key: value <= threshold for key, value in values.items()}
        if closes["kinematics_only"] and not closes["model_only"]:
            classification = "rra-kinematics-sufficient"
        elif closes["model_only"] and not closes["kinematics_only"]:
            classification = "rra-model-adjustment-sufficient"
        elif closes["kinematics_only"] and closes["model_only"]:
            classification = "kinematics-and-model-independently-sufficient"
        elif closes["combined"]:
            classification = "kinematics-model-interaction-required"
        else:
            kin = reductions["kinematics_only"] >= float(minimum_relative_reduction)
            mod = reductions["model_only"] >= float(minimum_relative_reduction)
            if kin and mod:
                classification = "mixed-contributors-not-sufficient"
            elif kin:
                classification = "kinematics-contributor-not-sufficient"
            elif mod:
                classification = "model-adjustment-contributor-not-sufficient"
            else:
                classification = "neither-rra-factor-sufficient"
        return {
            "metric": metric,
            "threshold": float(threshold),
            "values": values,
            "relative_reductions": reductions,
            "closes": closes,
            "minimum_contributor_relative_reduction": float(
                minimum_relative_reduction
            ),
            "classification": classification,
        }

    force = outcome("residual_force_fraction", force_fraction_threshold)
    moment = outcome("residual_moment_fraction", moment_fraction_threshold)
    return {
        "force": force,
        "moment": moment,
        "combined_mechanics_closed": bool(
            force["closes"]["combined"] and moment["closes"]["combined"]
        ),
        "design": "2x2-original-vs-rra-adjusted-model-by-ik-vs-rra-kinematics",
    }


def compare_model_inertial_properties(
    original_model_path: Path, adjusted_model_path: Path
) -> Dict[str, Any]:
    """Report every mass, COM, and inertia change made by RRA."""

    mass_atol_kg = 1e-9
    center_atol_m = 1e-9
    # OpenSim 4 rewrites legacy inertia values with six significant digits.
    # A half-unit in the sixth digit is representation noise, not an RRA edit.
    inertia_atol_kg_m2 = 1e-12
    inertia_rtol = 5e-6

    def bodies(path: Path) -> Dict[str, Dict[str, Any]]:
        root = ET.parse(str(path)).getroot()
        result = {}
        for body in root.iter("Body"):
            name = str(body.get("name", ""))
            # Legacy OpenSim 3 files may serialize ground as a zero-mass Body;
            # OpenSim 4 removes it when writing the RRA-adjusted model. Ground
            # is not a material segment and is excluded from both inventories.
            if not name or name.lower() == "ground":
                continue
            mass_text = body.findtext("mass")
            center_text = body.findtext("mass_center")
            if mass_text is None or center_text is None:
                continue
            combined_inertia = body.findtext("inertia")
            if combined_inertia is not None:
                inertia = [float(value) for value in combined_inertia.split()]
                if len(inertia) != 6:
                    raise ValueError("OpenSim Body inertia must contain six values")
            else:
                inertia = []
                for suffix in ("xx", "yy", "zz", "xy", "xz", "yz"):
                    raw = body.findtext("inertia_{}".format(suffix))
                    if raw is None:
                        raise ValueError("OpenSim Body lacks inertia properties")
                    inertia.append(float(raw))
            result[name] = {
                "mass_kg": float(mass_text),
                "mass_center_m": [float(value) for value in center_text.split()],
                "inertia_kg_m2": inertia,
            }
        return result

    original = bodies(original_model_path)
    adjusted = bodies(adjusted_model_path)
    if set(original) != set(adjusted):
        raise ValueError("RRA adjusted model changed the body inventory")
    rows = []
    for name in sorted(original):
        before = original[name]
        after = adjusted[name]
        mass_delta = float(after["mass_kg"] - before["mass_kg"])
        center_delta = np.asarray(after["mass_center_m"]) - np.asarray(
            before["mass_center_m"]
        )
        inertia_delta = np.asarray(after["inertia_kg_m2"]) - np.asarray(
            before["inertia_kg_m2"]
        )
        inertia_changed = not np.allclose(
            np.asarray(after["inertia_kg_m2"]),
            np.asarray(before["inertia_kg_m2"]),
            rtol=inertia_rtol,
            atol=inertia_atol_kg_m2,
        )
        changed = bool(
            abs(mass_delta) > mass_atol_kg
            or np.max(np.abs(center_delta)) > center_atol_m
            or inertia_changed
        )
        rows.append(
            {
                "body": name,
                "mass_delta_kg": mass_delta,
                "mass_center_delta_m": center_delta.tolist(),
                "inertia_delta_kg_m2": inertia_delta.tolist(),
                "changed": changed,
            }
        )
    return {
        "original_model_sha256": sha256_file(original_model_path),
        "adjusted_model_sha256": sha256_file(adjusted_model_path),
        "substantive_change_tolerances": {
            "mass_absolute_kg": mass_atol_kg,
            "mass_center_absolute_m": center_atol_m,
            "inertia_absolute_kg_m2": inertia_atol_kg_m2,
            "inertia_relative": inertia_rtol,
        },
        "body_count": len(rows),
        "changed_body_count": sum(int(row["changed"]) for row in rows),
        "changed_bodies": [row for row in rows if row["changed"]],
        "all_bodies": rows,
    }


def build_mechanics_localization_plan(
    protocol_path: Path,
    g6c_protocol_path: Path,
    manifest_path: Path,
    data_root: Path,
    prior_g6cs_result_path: Path,
) -> Dict[str, Any]:
    """Build a checksum-bound G6c-S2 plan and inherit the exact G6c RRA setup."""
    protocol_path = protocol_path.resolve()
    g6c_protocol_path = g6c_protocol_path.resolve()
    manifest_path = manifest_path.resolve()
    prior_path = prior_g6cs_result_path.resolve()
    protocol = _load_yaml(protocol_path)
    g6c_protocol = _load_yaml(g6c_protocol_path)
    if str(protocol.get("schema_version")) != MECHANICS_LOCALIZATION_SCHEMA_VERSION:
        raise ValueError("unsupported G6c-S2 protocol version")
    audit = validate_public_source_bundle(manifest_path, data_root)
    if not audit["pass"]:
        raise ValueError("G6c-S2 source bundle failed checksum verification")
    design = protocol.get("design") or {}
    role_map = design.get("roles") or {}
    required = ("model", "markers", "ground_reaction", "raw_c3d", "ik_setup")
    roles = audit["assets_by_role"]
    if any(not str(role_map.get(key, "")).strip() for key in required):
        raise ValueError("G6c-S2 protocol must declare every required source role")
    paths = {
        key: str(Path(str(roles[str(role_map[key])]["absolute_path"])).resolve())
        for key in required
    }
    input_hashes = {key: sha256_file(Path(path)) for key, path in paths.items()}
    prior = json.loads(prior_path.read_text(encoding="utf-8"))
    if prior.get("scope") != "g6c-s-whole-body-balance-audit":
        raise ValueError("prior result is not G6c-S")
    prior_hashes = ((prior.get("worker") or {}).get("input_sha256") or {})
    for key, value in input_hashes.items():
        if str(prior_hashes.get(key, "")) != value:
            raise ValueError("G6c-S2 input differs from prior G6c-S: {}".format(key))
    thresholds = protocol.get("thresholds") or {}
    if thresholds != prior.get("thresholds"):
        raise ValueError("G6c-S2 thresholds must equal prior G6c-S")
    window = [float(value) for value in design.get("kinematics_window_s", [])]
    target = [float(value) for value in design.get("target_window_s", [])]
    if len(window) != 2 or len(target) != 2 or not (
        window[0] < target[0] < target[1] < window[1]
    ):
        raise ValueError("G6c-S2 target window must be inside padded kinematics")
    grid = trc_time_grid(Path(paths["markers"]), window[0], window[1])
    target_grid = grid[(grid >= target[0] - 1e-10) & (grid <= target[1] + 1e-10)]
    if target_grid.size != int(design.get("expected_target_rows", 0)):
        raise ValueError("G6c-S2 target grid row count is not frozen correctly")
    rra = ((g6c_protocol.get("opensim") or {}).get("rra") or {})
    if not rra or int(rra.get("passes", 0)) != 2:
        raise ValueError("G6c-S2 requires the two-pass G6c RRA configuration")
    return {
        "schema_version": MECHANICS_LOCALIZATION_SCHEMA_VERSION,
        "scope": "g6c-s2-mechanics-localization-plan",
        "protocol_sha256": sha256_file(protocol_path),
        "g6c_protocol_sha256": sha256_file(g6c_protocol_path),
        "source_manifest_sha256": audit["manifest_sha256"],
        "prior_g6cs_result_sha256": sha256_file(prior_path),
        "input_paths": paths,
        "input_sha256": input_hashes,
        "kinematics_window_s": window,
        "target_window_s": target,
        "kinematics_time_grid_s": [float(value) for value in grid],
        "kinematics_time_grid_sha256": canonical_sha256(
            [float(value) for value in grid]
        ),
        "target_time_grid_sha256": canonical_sha256(
            [float(value) for value in target_grid]
        ),
        "expected_target_rows": int(target_grid.size),
        "vertical_axis": int(design.get("vertical_axis", 1)),
        "gravity_m_s2": [float(value) for value in design.get("gravity_m_s2", [])],
        "filter": design.get("filter") or {},
        "external_loads": design.get("external_loads") or {},
        "synchronization": design.get("synchronization") or {},
        "mass_diagnostic": design.get("mass_diagnostic") or {},
        "factorial": design.get("factorial") or {},
        "opensim": {
            "required_version": str(
                (protocol.get("opensim") or {}).get("required_version", "4.5.2")
            ),
            "rra": rra,
        },
        "thresholds": dict(thresholds),
        "prior_rra": (prior.get("worker") or {}).get("prior_rra") or {},
    }


def evaluate_mechanics_localization(
    plan: Mapping[str, Any], worker: Mapping[str, Any]
) -> Dict[str, Any]:
    """Evaluate completeness without converting localization into validation."""
    raw_factorial = worker.get("raw_factorial") or {}
    mot_factorial = worker.get("mot_factorial") or {}
    checks = {
        "worker_scope": worker.get("scope") == "g6c-s2-mechanics-localization-worker",
        "worker_input_hashes": worker.get("input_sha256") == plan.get("input_sha256"),
        "prior_result_hash": worker.get("prior_g6cs_result_sha256")
        == plan.get("prior_g6cs_result_sha256"),
        "kinematics_grid_exact": worker.get("kinematics_grid_exact") is True,
        "target_grid_exact": worker.get("target_grid_exact") is True,
        "synchronization_target_hidden_from_selection": (
            (worker.get("synchronization") or {}).get("selection_uses_target_window")
            is False
        ),
        "factorial_complete": bool(raw_factorial) and bool(mot_factorial),
        "rra_two_pass": int(worker.get("rra_pass_count", 0)) == 2,
        "mass_not_auto_applied": (
            (worker.get("scalar_mass_diagnostic") or {}).get("auto_apply") is False
        ),
    }
    diagnostic_complete = all(checks.values())
    mechanics_closed = bool(
        diagnostic_complete
        and raw_factorial.get("combined_mechanics_closed") is True
        and mot_factorial.get("combined_mechanics_closed") is True
        and ((worker.get("prior_rra") or {}).get("residual_moment_fraction", math.inf))
        <= float((plan.get("thresholds") or {}).get("residual_moment_fraction", 0.01))
    )
    return {
        "schema_version": MECHANICS_LOCALIZATION_SCHEMA_VERSION,
        "scope": "g6c-s2-mechanics-localization-audit",
        "pass": mechanics_closed,
        "diagnostic_complete": diagnostic_complete,
        "mechanics_closed": mechanics_closed,
        "checks": checks,
        "thresholds": dict(plan.get("thresholds") or {}),
        "worker": dict(worker),
        "scientific_claim_boundary": (
            "G6c-S2 localizes synchronization, RRA kinematics, and RRA model "
            "adjustment effects; it does not validate personalization"
        ),
        "next_gate": "g6d" if mechanics_closed else "blocked-at-g6c-s2",
    }


def write_mechanics_localization(result: Mapping[str, Any], output: Path) -> None:
    output = output.resolve()
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(
        json.dumps(result, indent=2, sort_keys=True) + "\n", encoding="utf-8"
    )
